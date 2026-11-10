"""bench の結果ディレクトリ／旧 txt を読む共通関数（summarize.py・invariant_check.py が使う）。

計測器（LM）の一部。数値の定義はここだけに置く:
  lap_n       周回数（awsim_result.json の lap_count。無ければ bench.txt の "LAP k done" 行の k>=1 の数。どちらも無ければ None）
  laps        周回タイム [s]（awsim_result.json の laps。無ければ bench.txt の LAP 行）
              注意: 2系統は約 0.1 s ずれる（lap_monitor は周回が変わる直前の /awsim/status の値）。
              表の laps_source 列で区別し、同じ系統どうしで比べる。JSON を正とする
  lap_med_2p  2周目以降の周回タイムの中央値（周回が2未満なら None）
  窓          autoware.log（無ければ bench.txt）の [mpc_stats] 行。1窓目（起動直後の400回）は除外
  solved_rate 2窓目以降の ('solved' + 'solved inaccurate') / solves の合計比
  rejected_n / fallback_n / maxiter_n / infeasible_n  2窓目以降の合計（rejected は a284274 の計測行に無いので None）
  t_p99_max   2窓目以降の t_p99 の最大 [ms]
  penalty_<kind>_n  awsim_result.json の penalty_by_kind[kind].count
"""
import ast
import json
import re
import statistics
from pathlib import Path

STATS_RE = re.compile(
    r"\[mpc_stats\] calls=(?P<calls>\d+) solves=(?P<solves>\d+) status=(?P<status>\{.*?\}) "
    r"iter_mean=(?P<iter_mean>[\d.]+) iter_max=(?P<iter_max>[\d.]+) "
    r"t_mean=(?P<t_mean>[\d.]+)ms t_p99=(?P<t_p99>[\d.]+)ms "
    r"max_raw_delta=(?P<max_raw_delta>[\d.]+)deg fallbacks=(?P<fallbacks>\d+)"
    r"(?: rejected=(?P<rejected>\d+) setups=(?P<setups>\d+))?"  # a284274 の計測行には rejected/setups が無い
)
LAP_RE = re.compile(r"^LAP (\d+) done: ([\d.]+) s")
PENALTY_KINDS = ("crash", "wall", "over", "block")


def parse_stats_lines(text):
    """[mpc_stats] 行を dict のリストにする（出現順）。"""
    out = []
    for line in text.splitlines():
        m = STATS_RE.search(line)
        if not m:
            continue
        d = {k: (v if k == "status" else (None if v is None else float(v))) for k, v in m.groupdict().items()}
        d["status"] = ast.literal_eval(d["status"])
        out.append(d)
    return out


def stats_summary(windows):
    """1窓目を除外して合算。窓が1つ以下なら None を返す。"""
    w = windows[1:]
    if not w:
        return None
    solves = sum(x["solves"] for x in w)
    ok = sum(x["status"].get("solved", 0) + x["status"].get("solved inaccurate", 0) for x in w)
    return {
        "windows_used": len(w),
        "solves": int(solves),
        "solved_rate": (ok / solves) if solves else None,
        "rejected_n": None if any(x["rejected"] is None for x in w) else int(sum(x["rejected"] for x in w)),
        "fallback_n": int(sum(x["fallbacks"] for x in w)),
        "maxiter_n": int(sum(x["status"].get("maximum iterations reached", 0) for x in w)),
        "infeasible_n": int(sum(v for x in w for k, v in x["status"].items() if k.startswith("primal infeasible"))),
        "t_p99_max": max(x["t_p99"] for x in w),
        "max_raw_delta_max": max(x["max_raw_delta"] for x in w),
    }


def laps_from_bench_txt(text):
    laps = []
    for line in text.splitlines():
        m = LAP_RE.match(line)
        if m and int(m.group(1)) >= 1:
            laps.append(float(m.group(2)))
    return laps


def load_result_dir(path):
    """新形式ディレクトリ（meta.json 必須）を読む。旧 txt は migrate 済みの前提。"""
    p = Path(path)
    meta = json.loads((p / "meta.json").read_text())
    r = {"dir": p.name, "label": meta.get("label"), "commit": meta.get("commit"),
         "dirty": meta.get("dirty"), "status": meta.get("status"), "meta": meta}
    bench_txt = (p / "bench.txt").read_text() if (p / "bench.txt").exists() else ""
    log_txt = (p / "autoware.log").read_text(errors="replace") if (p / "autoware.log").exists() else ""
    r["stats_source"] = "autoware.log" if log_txt else ("bench.txt" if bench_txt else None)
    windows = parse_stats_lines(log_txt or bench_txt)
    r["windows_n"] = len(windows)
    r["stats"] = stats_summary(windows)
    res_json = p / "awsim_result.json"
    if res_json.exists():
        j = json.loads(res_json.read_text())
        laps = list(j.get("laps", []))
        r["lap_n"] = int(j.get("lap_count", len(laps)))
        r["laps_source"] = "awsim_result.json"
        r["penalties"] = {k: int(j.get("penalty_by_kind", {}).get(k, {}).get("count", 0)) for k in PENALTY_KINDS}
    else:
        laps = laps_from_bench_txt(bench_txt)
        has_monitor = bool(re.search(r"^(LAP|END)", bench_txt, re.M))
        r["lap_n"] = len(laps) if has_monitor else None
        r["laps_source"] = "bench.txt" if has_monitor else None
        r["penalties"] = None
    r["laps"] = laps
    r["lap_med_2p"] = statistics.median(laps[1:]) if len(laps) >= 2 else None
    return r
