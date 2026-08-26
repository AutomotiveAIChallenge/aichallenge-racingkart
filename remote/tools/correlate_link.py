#!/usr/bin/env python3
"""joy の遅延と TCP の状態を時刻で突き合わせ、劣化の原因を切り分ける。

measure_joy_latency.py と probe_link.py を同時に走らせ、双方の CSV に入っている
epoch で突き合わせる。

zenoh は TCP/TLS の上で動き、輻輳制御は Drop に設定されている
（ブリッジのログの reliable_routes_blocking: false）。したがって:

  遅延が伸びた瞬間に TCP の retrans が跳ねている
      -> 経路（LTE 区間）でパケットが落ち、TCP の再送で遅延が伸びた。
         送信キューが詰まって zenoh 自身がメッセージを捨てたのが受信レート低下の正体
  retrans は平常なのに遅延が伸び、メッセージも消えている
      -> 経路は無傷。router かブリッジの側で滞留・破棄が起きている

  ./correlate_link.py --joy joy_e2e.csv --link link_probe.csv
"""

import argparse
import csv
import statistics


def load_joy(path):
    """秒バケットごとに (遅延のリスト) を返す。"""
    buckets = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            second = int(float(row["epoch"]))
            buckets.setdefault(second, []).append(float(row["end_to_end"]))
    return buckets


def load_link(path):
    """秒バケットごとに {socket: 統計} を返す。"""
    buckets = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            second = int(float(row["epoch"]))
            entry = buckets.setdefault(second, {"retrans": 0, "rtt": []})
            entry["retrans"] += int(row["retrans_delta"])
            rtt = float(row["rtt_ms"])
            if rtt == rtt:  # NaN を弾く
                entry["rtt"].append(rtt)
    return buckets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--joy", required=True)
    parser.add_argument("--link", required=True)
    parser.add_argument(
        "--slow",
        type=float,
        default=150.0,
        help="[ms] この p50 を超えた秒を劣化とみなす",
    )
    parser.add_argument(
        "--thin",
        type=int,
        default=8,
        help="この受信件数を下回った秒を劣化とみなす（正常は毎秒 10 件）",
    )
    args = parser.parse_args()

    joy = load_joy(args.joy)
    link = load_link(args.link)
    shared = sorted(set(joy) & set(link))
    # 最初と最後の秒はバケットが途中で切れており、受信件数が構造的に少なくなる。
    # 劣化として拾ってしまうので落とす。
    shared = shared[1:-1]
    if not shared:
        print("突き合わせできる秒がない。epoch 列のある CSV か確認すること")
        return

    print(f"突き合わせた秒数: {len(shared)}")

    degraded = []
    for second in shared:
        values = joy[second]
        p50 = statistics.median(values)
        if p50 > args.slow or len(values) < args.thin:
            degraded.append((second, p50, len(values), link[second]))

    total_retrans = sum(link[s]["retrans"] for s in shared)
    all_rtt = [r for s in shared for r in link[s]["rtt"]]
    print(
        f"TCP 再送 合計 {total_retrans} 回 / RTT 平均 "
        f"{statistics.mean(all_rtt):.1f} ms（n={len(all_rtt)}）"
    )

    if not degraded:
        print(
            f"\n劣化した秒は無い（p50 > {args.slow:.0f} ms または受信 < {args.thin} 件/秒）。"
            "\n劣化を捕まえられていないので、この測定では切り分けできない。長く回すこと"
        )
        return

    print(f"\n=== 劣化した秒: {len(degraded)} 件 ===")
    print("| 経過 | joy p50 [ms] | 受信 [件/s] | TCP 再送 | TCP RTT [ms] |")
    print("| --- | ---: | ---: | ---: | ---: |")
    base = shared[0]
    for second, p50, count, stats in degraded[:40]:
        rtt = statistics.mean(stats["rtt"]) if stats["rtt"] else float("nan")
        print(
            f"| {second - base} s | {p50:.1f} | {count} | {stats['retrans']} | {rtt:.1f} |"
        )

    retrans_in_degraded = sum(stats["retrans"] for _s, _p, _c, stats in degraded)
    print(
        f"\n劣化した {len(degraded)} 秒のあいだの TCP 再送: {retrans_in_degraded} 回"
        f"（全体 {total_retrans} 回）"
    )
    if retrans_in_degraded > 0:
        print(
            "→ 劣化の瞬間に再送が出ている。経路（LTE 区間）のパケットロスが原因で、"
            "\n  詰まった送信キューを zenoh が捨てたのが受信レート低下の正体と読める"
        )
    else:
        print(
            "→ 劣化の瞬間に再送が出ていない。経路は無傷なので、router かブリッジの側で"
            "\n  滞留・破棄が起きている疑いが強い"
        )


if __name__ == "__main__":
    main()
