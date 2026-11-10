#!/usr/bin/env python3
"""results/ の全走行を1つの表にする（指標の定義は bench_common.py）。

Usage: summarize.py [results_dir] [--json]
出力は Markdown の表。「出典」列は results 内のディレクトリ名（= 方針の出典列）。
"""
import json
import sys
from pathlib import Path

from bench_common import PENALTY_KINDS, load_result_dir

COLS = ["label", "commit", "status", "lap_n", "lap_med_2p", "laps_source", "solved_rate", "rejected_n", "fallback_n",
        "t_p99_max", "maxiter_n"] + [f"penalty_{k}_n" for k in PENALTY_KINDS] + ["出典"]
NO_METRICS = ("aborted", "running", "unreadable")  # 指標を出さない status


def row(r):
    s = r["stats"] or {}
    pen = r["penalties"] or {}
    if r["status"] in NO_METRICS:
        s, pen = {}, {}
        r = {**r, "lap_n": None, "lap_med_2p": None, "laps_source": None}
    return {
        "label": r["label"],
        "commit": (r["commit"] or "-") + ("+dirty" if r["dirty"] else ""),
        "status": r["status"],
        "lap_n": r["lap_n"],
        "lap_med_2p": r["lap_med_2p"],
        "laps_source": {"awsim_result.json": "json", "bench.txt": "monitor"}.get(r["laps_source"]),
        "solved_rate": s.get("solved_rate"),
        "rejected_n": s.get("rejected_n"),
        "fallback_n": s.get("fallback_n"),
        "t_p99_max": s.get("t_p99_max"),
        "maxiter_n": s.get("maxiter_n"),
        **{f"penalty_{k}_n": pen.get(k) for k in PENALTY_KINDS},
        "出典": r["dir"],
    }


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}" if v <= 1.0 else f"{v:.2f}"
    return str(v)


def load_or_error(p):
    try:
        return load_result_dir(p)
    except Exception as e:  # noqa: BLE001
        print(f"summarize: cannot read {p.name}: {e}", file=sys.stderr)
        return {"dir": p.name, "label": None, "commit": None, "dirty": False, "status": "unreadable",
                "stats": None, "penalties": None, "lap_n": None, "lap_med_2p": None, "laps_source": None}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = Path(args[0]) if args else Path(__file__).resolve().parent / "results"
    dirs = sorted(p for p in base.iterdir() if p.is_dir() and (p / "meta.json").exists())
    rows = [row(load_or_error(p)) for p in dirs]
    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print("| " + " | ".join(COLS) + " |")
    print("|" + "---|" * len(COLS))
    for r in rows:
        print("| " + " | ".join(fmt(r[c]) for c in COLS) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
