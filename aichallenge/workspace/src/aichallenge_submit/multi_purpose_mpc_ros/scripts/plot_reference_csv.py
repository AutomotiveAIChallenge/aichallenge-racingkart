#!/usr/bin/env python3

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
import yaml


def _read_float_column(row, name):
    return float(row[name].strip())


def load_points(csv_path, x_column, y_column, color_column):
    x_values = []
    y_values = []
    color_values = []

    with csv_path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path} does not contain a CSV header")

        columns = {name.strip(): name for name in reader.fieldnames}
        for column in [x_column, y_column]:
            if column not in columns:
                raise ValueError(f"missing required column '{column}' in {csv_path}")
        if color_column and color_column not in columns:
            raise ValueError(f"missing color column '{color_column}' in {csv_path}")

        for row in reader:
            x_values.append(_read_float_column(row, columns[x_column]))
            y_values.append(_read_float_column(row, columns[y_column]))
            if color_column:
                color_values.append(_read_float_column(row, columns[color_column]))

    if not x_values:
        raise ValueError(f"{csv_path} does not contain any data rows")

    return x_values, y_values, color_values


def load_trajectory_rows(csv_path):
    rows = []
    with csv_path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path} does not contain a CSV header")
        columns = {name.strip(): name for name in reader.fieldnames}
        required = ["s_m", "x_m", "y_m", "kappa_radpm", "vx_mps"]
        missing = [column for column in required if column not in columns]
        if missing:
            raise ValueError(f"missing required columns for section analysis: {missing}")

        for index, row in enumerate(reader):
            rows.append(
                {
                    "index": index,
                    "s_m": _read_float_column(row, columns["s_m"]),
                    "x_m": _read_float_column(row, columns["x_m"]),
                    "y_m": _read_float_column(row, columns["y_m"]),
                    "kappa_radpm": _read_float_column(row, columns["kappa_radpm"]),
                    "vx_mps": _read_float_column(row, columns["vx_mps"]),
                }
            )
    return rows


def classify_curvature(kappa, straight_threshold, gentle_threshold, medium_threshold):
    abs_kappa = abs(kappa)
    if abs_kappa < straight_threshold:
        return "straight"
    if abs_kappa < gentle_threshold:
        return "gentle_curve"
    if abs_kappa < medium_threshold:
        return "medium_curve"
    return "sharp_curve"


def section_direction(kappas, section_type):
    if section_type == "straight":
        return "none"
    mean_kappa = float(np.mean(kappas))
    if mean_kappa > 0.0:
        return "left"
    if mean_kappa < 0.0:
        return "right"
    return "none"


def section_strategy(section_type):
    return {
        "straight": "加速余地が大きい。障害物検知距離を長めに取り、回避は早期に戻す。",
        "gentle_curve": "基本速度を維持しつつ、横方向回避量を小さく抑える。",
        "medium_curve": "速度と横加速度の余裕を確認し、回避開始を早める。",
        "sharp_curve": "減速優先。大きな横回避は避け、手前でラインを整える。",
    }[section_type]


def section_type_from_max_abs_kappa(max_abs_kappa, straight_threshold, gentle_threshold, medium_threshold):
    if max_abs_kappa < straight_threshold:
        return "straight"
    if max_abs_kappa < gentle_threshold:
        return "gentle_curve"
    if max_abs_kappa < medium_threshold:
        return "medium_curve"
    return "sharp_curve"


def build_sections(rows, straight_threshold, gentle_threshold, medium_threshold, min_section_length):
    if not rows:
        return []

    labels = [
        classify_curvature(row["kappa_radpm"], straight_threshold, gentle_threshold, medium_threshold)
        for row in rows
    ]
    raw_sections = []
    start = 0
    for index in range(1, len(rows)):
        if labels[index] != labels[start]:
            raw_sections.append([start, index - 1, labels[start]])
            start = index
    raw_sections.append([start, len(rows) - 1, labels[start]])

    merged = []
    for section_index, section in enumerate(raw_sections):
        length = rows[section[1]]["s_m"] - rows[section[0]]["s_m"]
        if length < min_section_length:
            if merged:
                previous = merged[-1]
                previous_length = rows[previous[1]]["s_m"] - rows[previous[0]]["s_m"]
                has_next = section_index + 1 < len(raw_sections)
                previous_is_stable = previous_length >= min_section_length
                # Preserve a long approach section before a corner complex.
                # Short curvature fragments after a stable section should start
                # the next section instead of being absorbed backward.
                if previous_is_stable and previous[2] != section[2] and has_next:
                    raw_sections[section_index + 1][0] = section[0]
                else:
                    previous[1] = section[1]
            elif section_index + 1 < len(raw_sections):
                raw_sections[section_index + 1][0] = section[0]
            else:
                merged.append(section)
        else:
            merged.append(section)

    sections = []
    for section_id, (start_index, end_index, section_type) in enumerate(merged, start=1):
        section_rows = rows[start_index : end_index + 1]
        kappas = np.array([row["kappa_radpm"] for row in section_rows])
        speeds = np.array([row["vx_mps"] for row in section_rows])
        max_abs_kappa = float(np.max(np.abs(kappas)))
        # After merging short fragments, keep the section label conservative.
        # Avoidance planning should not treat a section as straight if it contains
        # a short high-curvature segment.
        section_type = section_type_from_max_abs_kappa(
            max_abs_kappa, straight_threshold, gentle_threshold, medium_threshold
        )
        start_s = section_rows[0]["s_m"]
        end_s = section_rows[-1]["s_m"]
        sections.append(
            {
                "section_id": section_id,
                "start_index": start_index,
                "end_index": end_index,
                "start_s_m": start_s,
                "end_s_m": end_s,
                "length_m": end_s - start_s,
                "type": section_type,
                "direction": section_direction(kappas, section_type),
                "mean_abs_kappa": float(np.mean(np.abs(kappas))),
                "max_abs_kappa": max_abs_kappa,
                "mean_vx_mps": float(np.mean(speeds)),
                "strategy_hint": section_strategy(section_type),
            }
        )
    return sections


def write_sections_csv(sections, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as stream:
        fieldnames = [
            "section_id",
            "start_index",
            "end_index",
            "start_s_m",
            "end_s_m",
            "length_m",
            "type",
            "direction",
            "mean_abs_kappa",
            "max_abs_kappa",
            "mean_vx_mps",
            "strategy_hint",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for section in sections:
            writer.writerow(section)


def draw_sections(ax, rows, sections):
    colors = {
        "straight": "#2ecc71",
        "gentle_curve": "#3498db",
        "medium_curve": "#f39c12",
        "sharp_curve": "#e74c3c",
    }
    for section in sections:
        section_rows = rows[section["start_index"] : section["end_index"] + 1]
        if len(section_rows) < 2:
            continue
        points = np.array([[row["x_m"], row["y_m"]] for row in section_rows])
        segments = np.stack([points[:-1], points[1:]], axis=1)
        collection = LineCollection(
            segments,
            colors=colors[section["type"]],
            linewidths=3.0,
            alpha=0.95,
            zorder=15,
        )
        ax.add_collection(collection)

        mid_row = section_rows[len(section_rows) // 2]
        label = f"S{section['section_id']}\\n{section['type'].replace('_', ' ')}"
        ax.text(
            mid_row["x_m"],
            mid_row["y_m"],
            label,
            fontsize=7,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": colors[section["type"]], "alpha": 0.75},
            zorder=25,
        )


def load_constraints_rows(constraints_csv_path):
    rows = []
    with constraints_csv_path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"{constraints_csv_path} does not contain a CSV header")
        columns = {name.strip(): name for name in reader.fieldnames}
        required = ["x", "y", "psi", "ub", "lb"]
        missing = [column for column in required if column not in columns]
        if missing:
            raise ValueError(f"missing required columns for avoidance overlay: {missing}")

        previous = None
        cumulative_s = 0.0
        for row in reader:
            x = _read_float_column(row, columns["x"])
            y = _read_float_column(row, columns["y"])
            if previous is not None:
                cumulative_s += float(np.hypot(x - previous[0], y - previous[1]))
            rows.append(
                {
                    "s_m": cumulative_s,
                    "x": x,
                    "y": y,
                    "psi": _read_float_column(row, columns["psi"]),
                    "ub": _read_float_column(row, columns["ub"]),
                    "lb": _read_float_column(row, columns["lb"]),
                }
            )
            previous = (x, y)
    return rows


def load_avoidance_config(config_path):
    with config_path.open() as stream:
        config = yaml.safe_load(stream)
    avoidance = config.get("avoidance", {}) or {}
    defaults = {
        "wall_margin_m": float(avoidance.get("wall_margin_m", 1.0)),
        "blend_length_m": float(avoidance.get("blend_length_m", 4.0)),
    }
    policies = []
    for item in avoidance.get("sections", []) or []:
        policy = dict(item)
        policy["wall_margin_m"] = float(policy.get("wall_margin_m", defaults["wall_margin_m"]))
        policy["blend_length_m"] = float(policy.get("blend_length_m", defaults["blend_length_m"]))
        policy["start_s_m"] = float(policy["start_s_m"])
        policy["end_s_m"] = float(policy["end_s_m"])
        policy["line"] = policy.get("line", "center")
        policies.append(policy)
    return policies


def lateral_point(row, e_y):
    return (
        row["x"] - e_y * np.sin(row["psi"]),
        row["y"] + e_y * np.cos(row["psi"]),
    )


def candidate_lateral_offsets(row, wall_margin):
    center = (row["ub"] + row["lb"]) / 2.0
    return {
        "center": center,
        "left_wall": float(np.clip(row["ub"] - wall_margin, row["lb"], row["ub"])),
        "right_wall": float(np.clip(row["lb"] + wall_margin, row["lb"], row["ub"])),
    }


def policy_at_s(policies, s_m):
    for policy in policies:
        if policy["start_s_m"] <= s_m <= policy["end_s_m"]:
            return policy
    return None


def next_policy_after_s(policies, s_m):
    for policy in policies:
        if s_m < policy["start_s_m"]:
            return policy
    return None


def target_for_policy(row, policy):
    return candidate_lateral_offsets(row, policy["wall_margin_m"])[policy["line"]]


def blended_avoidance_offset(row, policies):
    center = (row["ub"] + row["lb"]) / 2.0
    policy = policy_at_s(policies, row["s_m"])
    if policy is None:
        next_policy = next_policy_after_s(policies, row["s_m"])
        if next_policy is None or next_policy["blend_length_m"] <= 0.0:
            return center
        distance_to_start = next_policy["start_s_m"] - row["s_m"]
        if distance_to_start > next_policy["blend_length_m"]:
            return center
        alpha = np.clip(1.0 - distance_to_start / next_policy["blend_length_m"], 0.0, 1.0)
        return center + (target_for_policy(row, next_policy) - center) * alpha

    current_target = target_for_policy(row, policy)
    if policy["blend_length_m"] <= 0.0:
        return current_target

    distance_to_end = policy["end_s_m"] - row["s_m"]
    if distance_to_end > policy["blend_length_m"]:
        return current_target

    next_policy = next_policy_after_s(policies, row["s_m"])
    next_is_near = (
        next_policy is not None and next_policy["start_s_m"] - policy["end_s_m"] <= policy["blend_length_m"]
    )
    end_target = target_for_policy(row, next_policy) if next_is_near else center
    alpha = np.clip(distance_to_end / policy["blend_length_m"], 0.0, 1.0)
    return end_target + (current_target - end_target) * alpha


def draw_avoidance_lines(ax, constraints_rows, policies):
    if not constraints_rows or not policies:
        return

    policies = sorted(policies, key=lambda policy: policy["start_s_m"])
    candidate_colors = {
        "center": "#555555",
        "left_wall": "#8e44ad",
        "right_wall": "#16a085",
    }
    for policy in policies:
        section_rows = [
            row for row in constraints_rows if policy["start_s_m"] <= row["s_m"] <= policy["end_s_m"]
        ]
        if len(section_rows) < 2:
            continue

        for line_name, color in candidate_colors.items():
            points = np.array(
                [
                    lateral_point(row, candidate_lateral_offsets(row, policy["wall_margin_m"])[line_name])
                    for row in section_rows
                ]
            )
            ax.plot(
                points[:, 0],
                points[:, 1],
                linestyle="--",
                linewidth=1.1,
                color=color,
                alpha=0.65,
                zorder=16,
                label=f"{line_name} candidate" if policy is policies[0] else None,
            )

        selected_line = policy["line"]
        selected_points = np.array(
            [
                lateral_point(row, candidate_lateral_offsets(row, policy["wall_margin_m"])[selected_line])
                for row in section_rows
            ]
        )
        ax.plot(
            selected_points[:, 0],
            selected_points[:, 1],
            linewidth=3.0,
            color="#ff00ff",
            alpha=0.95,
            zorder=18,
            label="selected avoidance line" if policy is policies[0] else None,
        )

        mid = selected_points[len(selected_points) // 2]
        ax.text(
            mid[0],
            mid[1],
            f"S{policy['section_id']} {selected_line}",
            fontsize=7,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "#ff00ff", "alpha": 0.75},
            zorder=26,
        )

    target_start = max(0.0, min(policy["start_s_m"] for policy in policies) - max(policy["blend_length_m"] for policy in policies))
    target_end = max(policy["end_s_m"] for policy in policies)
    target_rows = [row for row in constraints_rows if target_start <= row["s_m"] <= target_end]
    if len(target_rows) >= 2:
        target_points = np.array(
            [lateral_point(row, blended_avoidance_offset(row, policies)) for row in target_rows]
        )
        ax.plot(
            target_points[:, 0],
            target_points[:, 1],
            linewidth=3.5,
            color="#ff00ff",
            alpha=0.95,
            zorder=19,
            label="blended target line",
        )


def load_occupancy_grid(map_yaml_path):
    with map_yaml_path.open() as stream:
        map_config = yaml.safe_load(stream)

    image_path = map_yaml_path.parent / map_config["image"]
    image = np.asarray(mpimg.imread(image_path))
    if image.ndim == 3:
        image = image[:, :, 0]
    if image.ndim != 2:
        raise ValueError(f"unexpected occupancy grid dimensions: {image.shape}")

    # Match multi_purpose_mpc_ros.core.map.Map: 1 is free, 0 is occupied.
    occupied_threshold = float(map_config["occupied_thresh"])
    occupancy = np.where(image >= occupied_threshold, 1.0, 0.0)

    resolution = float(map_config["resolution"])
    origin_x, origin_y = float(map_config["origin"][0]), float(map_config["origin"][1])
    height, width = occupancy.shape
    extent = [
        origin_x,
        origin_x + (width - 1) * resolution,
        origin_y,
        origin_y + (height - 1) * resolution,
    ]
    return occupancy, extent


def draw_occupancy_grid(ax, map_yaml_path, alpha):
    occupancy, extent = load_occupancy_grid(map_yaml_path)
    ax.imshow(
        occupancy,
        cmap="gray",
        origin="upper",
        extent=extent,
        interpolation="nearest",
        alpha=alpha,
        zorder=0,
    )


def plot_csv(
    csv_path,
    output_path,
    x_column,
    y_column,
    color_column,
    title,
    map_yaml_path,
    map_alpha,
    sections,
    section_rows,
    avoidance_policies,
    constraints_rows,
):
    x_values, y_values, color_values = load_points(csv_path, x_column, y_column, color_column)

    fig, ax = plt.subplots(figsize=(10, 8), constrained_layout=True)
    if map_yaml_path:
        draw_occupancy_grid(ax, map_yaml_path, map_alpha)

    if color_column:
        scatter = ax.scatter(x_values, y_values, c=color_values, s=16, cmap="viridis", zorder=10)
        fig.colorbar(scatter, ax=ax, label=color_column)
    else:
        ax.plot(x_values, y_values, linewidth=1.5, zorder=10)
        ax.scatter(x_values, y_values, s=8, zorder=11)

    if sections:
        draw_sections(ax, section_rows, sections)

    if avoidance_policies:
        draw_avoidance_lines(ax, constraints_rows, avoidance_policies)

    ax.scatter(x_values[0], y_values[0], c="lime", s=80, marker="o", label="start", edgecolors="black", zorder=20)
    ax.scatter(x_values[-1], y_values[-1], c="red", s=80, marker="x", label="end", zorder=20)
    ax.set_title(title or csv_path.name)
    ax.set_xlabel(x_column)
    ax.set_ylabel(y_column)
    ax.axis("equal")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="best")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Plot a reference path CSV and save it as an image.")
    parser.add_argument("csv_path", type=Path, help="CSV file containing x/y trajectory columns")
    parser.add_argument("-o", "--output", type=Path, help="Output image path. Defaults to <csv_stem>.png")
    parser.add_argument("--x-column", default="x_m", help="X coordinate column name")
    parser.add_argument("--y-column", default="y_m", help="Y coordinate column name")
    parser.add_argument(
        "--color-column",
        default="vx_mps",
        help="Optional column used for point color. Pass an empty string to disable coloring.",
    )
    parser.add_argument("--title", default="", help="Plot title")
    parser.add_argument("--map-yaml", type=Path, help="Optional occupancy grid YAML to draw behind the CSV path")
    parser.add_argument("--map-alpha", type=float, default=0.45, help="Occupancy grid overlay alpha")
    parser.add_argument("--auto-sections", action="store_true", help="Analyze curvature and draw path sections")
    parser.add_argument("--sections-output", type=Path, help="Optional CSV output path for detected sections")
    parser.add_argument("--avoidance-config", type=Path, help="Optional MPC config YAML with avoidance sections")
    parser.add_argument(
        "--constraints-csv",
        type=Path,
        help="Optional constraints CSV for avoidance overlay. Defaults to <csv_stem>_constraints.csv",
    )
    parser.add_argument("--straight-threshold", type=float, default=0.03, help="Abs curvature threshold for straight")
    parser.add_argument("--gentle-threshold", type=float, default=0.08, help="Abs curvature threshold for gentle curves")
    parser.add_argument("--medium-threshold", type=float, default=0.15, help="Abs curvature threshold for medium curves")
    parser.add_argument("--min-section-length", type=float, default=5.0, help="Merge sections shorter than this length")
    args = parser.parse_args()

    output_path = args.output or args.csv_path.with_suffix(".png")
    color_column = args.color_column or None
    sections = []
    section_rows = []
    if args.auto_sections:
        section_rows = load_trajectory_rows(args.csv_path)
        sections = build_sections(
            section_rows,
            args.straight_threshold,
            args.gentle_threshold,
            args.medium_threshold,
            args.min_section_length,
        )
        if args.sections_output:
            write_sections_csv(sections, args.sections_output)

    avoidance_policies = []
    constraints_rows = []
    if args.avoidance_config:
        avoidance_policies = load_avoidance_config(args.avoidance_config)
        constraints_csv = args.constraints_csv or args.csv_path.with_name(
            f"{args.csv_path.stem}_constraints{args.csv_path.suffix}"
        )
        constraints_rows = load_constraints_rows(constraints_csv)

    plot_csv(
        args.csv_path,
        output_path,
        args.x_column,
        args.y_column,
        color_column,
        args.title,
        args.map_yaml,
        args.map_alpha,
        sections,
        section_rows,
        avoidance_policies,
        constraints_rows,
    )
    print(f"saved plot image: {output_path}")


if __name__ == "__main__":
    main()
