#!/usr/bin/env python3
"""Summarise a local practice race (make practice-4car).

Reads only what AWSIM and practice_race.bash already write into the run directory:
  result-summary.json        AWSIM, schema v2: final order and lap times
  d<N>-result-details.json   AWSIM, schema v3: penalty events per car
  practice-manifest.json     practice_race.bash: which build sat in which slot

Penalties come straight from AWSIM's own penalty_events. Position swaps are derived
from lap times: at every lap line the cars are ordered (more laps first, then earlier
cumulative time) and each change of order between consecutive lap lines is one swap.
Resolution is one lap, so a pass and a re-pass inside the same lap cancel out.

Usage: practice_summary.py [RUN_DIR] [--json OUT]
RUN_DIR defaults to the newest output/*/ that holds a practice-manifest.json.
Stdlib only, Python 3.8+.
"""

import argparse
import json
import sys
from pathlib import Path

PENALTY_KINDS = ("crash", "wall", "over", "block")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def newest_run(output_root):
    runs = sorted(p.parent for p in output_root.glob("*/practice-manifest.json"))
    if not runs:
        raise SystemExit(f"no practice run under {output_root}/ (pass RUN_DIR)")
    return runs[-1]


def find_details(run_dir, number):
    name = f"d{number}-result-details.json"
    for path in (run_dir / name, run_dir / f"d{number}" / name):
        if path.is_file():
            return load_json(path)
    return None


def cumulative(laps):
    out, total = [], 0.0
    for lap in laps:
        total += lap
        out.append(total)
    return out


def ahead_at(cum_a, cum_b, line):
    """Whether car a is ahead of car b at lap line `line` (1-based); None if neither reached it."""
    a = cum_a[line - 1] if len(cum_a) >= line else None
    b = cum_b[line - 1] if len(cum_b) >= line else None
    if a is None and b is None:
        return None
    if b is None:
        return True
    if a is None:
        return False
    return a < b


def position_swaps(cum_by_car):
    """[{"lap", "car", "passed"}]: `car` moved ahead of `passed` between lap lines lap-1 and lap."""
    cars = sorted(cum_by_car)
    last_line = max((len(c) for c in cum_by_car.values()), default=0)
    swaps = []
    for line in range(2, last_line + 1):
        for i, a in enumerate(cars):
            for b in cars[i + 1:]:
                before = ahead_at(cum_by_car[a], cum_by_car[b], line - 1)
                after = ahead_at(cum_by_car[a], cum_by_car[b], line)
                if before is None or after is None or before == after:
                    continue
                winner, loser = (a, b) if after else (b, a)
                swaps.append({"lap": line, "car": winner, "passed": loser})
    return swaps


def car_row(vehicle, details, label):
    by_kind = details.get("penalty_by_kind", {})
    return {
        "slot": vehicle["vehicle_number"],
        "label": label,
        "position": vehicle.get("final_position"),
        "finished": bool(vehicle.get("finished")),
        "laps": [float(t) for t in vehicle.get("laps", [])],
        "total_s": float(vehicle.get("total_lap_time", 0.0)),
        "best_s": float(vehicle.get("min_lap_time", 0.0)),
        "penalty_count": int(details.get("penalty_count", 0)),
        "penalty_s": float(details.get("penalty_total_seconds", 0.0)),
        "penalty_kinds": {k: int(by_kind.get(k, {}).get("count", 0)) for k in PENALTY_KINDS},
        "penalty_events": list(details.get("penalty_events", [])),
    }


def summarise(run_dir):
    summary = load_json(run_dir / "result-summary.json")
    manifest_path = run_dir / "practice-manifest.json"
    manifest = load_json(manifest_path) if manifest_path.is_file() else {}
    labels = {s["slot"]: s["label"] for s in manifest.get("slots", [])}
    cars, warnings = [], []
    for vehicle in summary.get("vehicles", []):
        number = vehicle["vehicle_number"]
        details = find_details(run_dir, number)
        if details is None:
            warnings.append(f"d{number}-result-details.json missing: penalties of slot {number} unknown")
        label = labels.get(number, vehicle.get("vehicle_name", f"GoKart{number}"))
        cars.append(car_row(vehicle, details or {}, label))

    expected = len(manifest.get("slots", []))
    if expected and len(cars) != expected:
        warnings.append(f"{expected} slots were started but AWSIM reports {len(cars)} cars: a slot never joined")
    for car in cars:
        # A parked car turns every other car's result into "raced against an obstacle".
        if not car["laps"]:
            warnings.append(
                f"slot {car['slot']} ({car['label']}) completed no lap: the others raced a parked car; "
                f"check d{car['slot']}/autoware.log"
            )

    swaps = position_swaps({car["slot"]: cumulative(car["laps"]) for car in cars})
    for car in cars:
        car["swaps_gained"] = sum(1 for s in swaps if s["car"] == car["slot"])
        car["swaps_lost"] = sum(1 for s in swaps if s["passed"] == car["slot"])
    cars.sort(key=lambda c: (c["position"] is None, c["position"] or 0))
    condition_keys = ("class", "handicap", "npcs", "grid", "seed", "round", "pin")
    return {
        "run_dir": str(run_dir),
        "session": summary.get("session", {}),
        "conditions": {k: manifest[k] for k in condition_keys if manifest.get(k) not in (None, "")},
        "cars": cars,
        "swaps": swaps,
        "warnings": warnings,
    }


def seconds(value):
    return f"{value:.2f}" if value else "-"


def render(result):
    lines = [f"# Practice race {Path(result['run_dir']).name}", ""]
    if result["conditions"]:
        lines += ["conditions: " + " ".join(f"{k}={v}" for k, v in result["conditions"].items()), ""]
    lines.append("| pos | slot | build | laps | total s | best s | penalties n / s | crash | wall | over | block | swaps +/- |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in result["cars"]:
        k = c["penalty_kinds"]
        laps = f"{len(c['laps'])}{'' if c['finished'] else ' DNF'}"
        lines.append(
            f"| {c['position']} | {c['slot']} | {c['label']} | {laps} | {seconds(c['total_s'])} | "
            f"{seconds(c['best_s'])} | {c['penalty_count']} / {c['penalty_s']:.1f} | {k['crash']} | "
            f"{k['wall']} | {k['over']} | {k['block']} | +{c['swaps_gained']} / -{c['swaps_lost']} |"
        )
    lines += ["", "## Lap times (s)", ""]
    for c in result["cars"]:
        laps = ", ".join(f"{t:.2f}" for t in c["laps"]) or "no lap"
        lines.append(f"- slot {c['slot']} {c['label']}: {laps}")
    if result["swaps"]:
        lines += ["", "## Position swaps (lap-line resolution)", ""]
        lines += [f"- lap {s['lap']}: slot {s['car']} moved ahead of slot {s['passed']}" for s in result["swaps"]]
    events = sorted(
        ((c["slot"], e) for c in result["cars"] for e in c["penalty_events"]),
        key=lambda item: item[1].get("race_time", 0.0),
    )
    if events:
        lines += ["", "## Penalty events", ""]
        for slot, e in events:
            lines.append(
                f"- t={e.get('race_time', 0.0):.1f} s lap {e.get('lap')} slot {slot}: "
                f"{e.get('kind')} ({e.get('duration', 0.0):.1f} s)"
            )
    if result["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in result["warnings"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Summarise a make practice-4car run.")
    parser.add_argument("run_dir", nargs="?", type=Path, help="output/<timestamp> (default: newest practice run)")
    parser.add_argument("--json", type=Path, help="also write the summary as JSON")
    args = parser.parse_args(argv)
    run_dir = args.run_dir or newest_run(Path("output"))
    if not (run_dir / "result-summary.json").is_file():
        raise SystemExit(f"{run_dir}/result-summary.json not found (race not finished?)")
    result = summarise(run_dir)
    sys.stdout.write(render(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
