# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib>=3.7", "numpy>=1.24"]
# ///
"""measure_joy_latency.py が出した CSV から joy 遅延の図を描く。

背景を透過にし、文字を中間グレーにしてあるので、文書が明るく描画されても
暗く描画されても読める。

  uv run plot_joy_latency.py --csv data/joy_e2e_long.csv --out ../../docs/images
"""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# dataviz categorical slots。明るい面でも暗い面でも読めることを確認した色。
BLUE = "#2a78d6"
ORANGE = "#eb6834"
INK = "#6b6b6b"
GRID = "#9a9a9a"


def load(path):
    columns = {"elapsed": [], "end_to_end": []}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for key in columns:
                columns[key].append(float(row[key]))
    return {key: np.array(values) for key, values in columns.items()}


def style(axes):
    axes.set_facecolor("none")
    axes.grid(True, color=GRID, alpha=0.25, linewidth=0.8)
    axes.set_axisbelow(True)
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axes.spines[spine].set_color(GRID)
    axes.tick_params(colors=INK, labelsize=9)
    axes.xaxis.label.set_color(INK)
    axes.yaxis.label.set_color(INK)
    axes.title.set_color(INK)


def ink_legend(axes, **kwargs):
    legend = axes.legend(frameon=False, fontsize=9, **kwargs)
    for text in legend.get_texts():
        text.set_color(INK)
    return legend


def save(figure, path):
    figure.savefig(path, format="svg", transparent=True, bbox_inches="tight")
    plt.close(figure)
    print(f"wrote {path}")


def pct(values, q):
    return float(np.percentile(values, q))


def plot_histogram(data, out, name, upper=300.0, step=5.0):
    values = data["end_to_end"]
    figure, axes = plt.subplots(figsize=(8, 4.2))
    bins = np.arange(0, upper + step, step)
    weights = np.full_like(values, 100.0 / len(values))
    axes.hist(
        np.clip(values, 0, upper),
        bins=bins,
        weights=weights,
        histtype="stepfilled",
        color=BLUE,
        alpha=0.45,
        edgecolor=BLUE,
        linewidth=1.5,
        label=f"joy 片方向 zenoh 往復  (n={len(values)})",
    )
    top = axes.get_ylim()[1]
    for q, dash, height in ((50, (4, 3), 0.60), (95, (1, 2), 0.40)):
        mark = pct(values, q)
        axes.axvline(mark, color=BLUE, linewidth=1.4, dashes=dash, alpha=0.9)
        axes.annotate(
            f"p{q} {mark:.0f} ms",
            xy=(mark, top * height),
            xytext=(7, 0),
            textcoords="offset points",
            fontsize=9,
            color=INK,
            va="center",
        )
    beyond = int((values > upper).sum())
    if beyond:
        axes.annotate(
            f"{upper:.0f} ms 超: {beyond} 件（最終ビンに寄せた）",
            xy=(0.30, 0.80),
            xycoords="axes fraction",
            ha="left",
            fontsize=8,
            color=INK,
        )
    axes.set_xlabel("遅延 [ms]")
    axes.set_ylabel("サンプルの割合 [%]")
    axes.set_title("joy 遅延の分布", fontsize=11, loc="left")
    axes.set_xlim(20, upper)
    ink_legend(axes, loc="upper right")
    style(axes)
    save(figure, out / name)


def plot_cdf(data, out, name, upper=400.0):
    values = np.sort(data["end_to_end"])
    share = np.arange(1, len(values) + 1) / len(values) * 100.0
    figure, axes = plt.subplots(figsize=(8, 4.2))
    axes.plot(values, share, color=BLUE, linewidth=1.8)
    for q, dash in ((50, (4, 3)), (95, (1, 2)), (99, (1, 1))):
        mark = pct(values, q)
        axes.axvline(mark, color=ORANGE, linewidth=1.2, dashes=dash, alpha=0.8)
        axes.annotate(
            f"p{q} {mark:.0f}",
            xy=(mark, q),
            xytext=(6, -12),
            textcoords="offset points",
            fontsize=9,
            color=INK,
        )
    axes.set_xlabel("遅延 [ms]")
    axes.set_ylabel("この値以下のサンプル [%]")
    axes.set_title("joy 遅延の累積分布", fontsize=11, loc="left")
    axes.set_xlim(20, upper)
    axes.set_ylim(0, 100)
    style(axes)
    save(figure, out / name)


def plot_timeseries(data, out, name, window=51):
    elapsed = data["elapsed"]
    values = data["end_to_end"]
    figure, axes = plt.subplots(figsize=(9, 4.2))
    axes.scatter(elapsed, values, s=4, color=BLUE, alpha=0.25, edgecolors="none", label="各サンプル")
    if len(values) > window:
        # 移動中央値。外れ値に引きずられずに水準の移り変わりを見る。
        half = window // 2
        centers, medians = [], []
        for index in range(half, len(values) - half):
            centers.append(elapsed[index])
            medians.append(np.median(values[index - half : index + half + 1]))
        axes.plot(centers, medians, color=ORANGE, linewidth=2.0, label=f"移動中央値（{window} 点）")
    axes.set_xlabel("測定開始からの経過 [s]")
    axes.set_ylabel("遅延 [ms]")
    axes.set_title("測定中の遅延の推移", fontsize=11, loc="left")
    axes.set_yscale("log")
    axes.set_ylim(20, max(values.max() * 1.2, 300))
    ink_legend(axes, loc="upper left")
    style(axes)
    save(figure, out / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="measure_joy_latency.py の CSV")
    parser.add_argument("--out", required=True, help="SVG の出力先ディレクトリ")
    parser.add_argument("--prefix", default="joy-latency", help="ファイル名の接頭辞")
    args = parser.parse_args()

    plt.rcParams["font.family"] = [
        "Noto Sans CJK JP",
        "IPAexGothic",
        "TakaoPGothic",
        "DejaVu Sans",
    ]

    data = load(args.csv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    plot_histogram(data, out, f"{args.prefix}-histogram.svg")
    plot_cdf(data, out, f"{args.prefix}-cdf.svg")
    plot_timeseries(data, out, f"{args.prefix}-timeseries.svg")


if __name__ == "__main__":
    main()
