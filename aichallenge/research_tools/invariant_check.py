#!/usr/bin/env python3
"""不変条件の検査（方針 §5）。失敗した項目だけを出す。

Usage: invariant_check.py <results/<run>/>
終了コード: 0 = 失敗なし、1 = 失敗あり、2 = 読めない、3 = I5〜I7 を1つも検査できなかった（窓が2未満。走行していない疑い）。
閾値は暫定（根拠: 未確定。§10 手順6 のベースライン5走行で確定し、ここに「根拠: experiments/0001」と書く）。
I9（lap_med_2p の悪化幅）はベースライン分布が無いので skip。
I1〜I4（舵角・変化率・加速度・周期）は rosbag を読む check_cmd_limits.py で別途（初回提出前が期限）。
"""
import sys

from bench_common import load_result_dir

THRESH = {  # 暫定
    "I5_solved_rate_min": 0.95,
    "I7_t_p99_max_ms": 25.0,
}


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        r = load_result_dir(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        print(f"invariant: cannot read {sys.argv[1]}: {e}")
        return 2
    fails, skipped, notes = [], [], []
    s = r["stats"]
    if s is None:
        skipped += ["I5", "I6", "I7"]
        notes.append(f"mpc_stats windows={r['windows_n']} (need >=2)")
    else:
        if s["solved_rate"] is None or s["solved_rate"] < THRESH["I5_solved_rate_min"]:
            fails.append(f"I5 solved_rate={s['solved_rate']} < {THRESH['I5_solved_rate_min']} (windows 2..{r['windows_n']})")
        if s["rejected_n"] is None:
            notes.append("I6 は fallbacks だけ検査（rejected は a284274 形式の計測行に無い）")
            if s["fallback_n"] != 0:
                fails.append(f"I6 fallback_n={s['fallback_n']} (expected 0; rejected unrecorded)")
        elif s["rejected_n"] != 0 or s["fallback_n"] != 0:
            fails.append(f"I6 rejected_n={s['rejected_n']} fallback_n={s['fallback_n']} (expected 0/0)")
        if s["t_p99_max"] >= THRESH["I7_t_p99_max_ms"]:
            fails.append(f"I7 t_p99_max={s['t_p99_max']}ms >= {THRESH['I7_t_p99_max_ms']}ms")
        if s["maxiter_n"] or s["infeasible_n"]:
            notes.append(f"status: maximum_iterations={s['maxiter_n']} primal_infeasible*={s['infeasible_n']} (要確認、失格ではない)")
    if r["penalties"] is None:
        skipped.append("I8")
        notes.append("awsim_result.json なし")
    else:
        bad = {k: v for k, v in r["penalties"].items() if v}
        if bad:
            fails.append("I8 penalty " + " ".join(f"{k}={v}" for k, v in bad.items()))
    skipped.append("I9")
    for f in fails:
        print("FAIL " + f)
    checked = [i for i in ("I5", "I6", "I7", "I8") if i not in skipped]
    skipped = [i for i in ("I5", "I6", "I7", "I8", "I9") if i in skipped]
    print(f"invariant: fails={len(fails)} checked={','.join(checked)} skipped={','.join(skipped)}"
          + (" | " + "; ".join(notes) if notes else ""))
    if fails:
        return 1
    return 3 if s is None else 0


if __name__ == "__main__":
    sys.exit(main())
