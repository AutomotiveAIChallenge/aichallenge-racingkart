#!/usr/bin/env python3
"""measure_joy_latency.py の CSV から、レポートに貼る数字を出す。

全体の統計と、時間バケットごとの推移の両方を出す。回線の一時的な劣化が
あったかどうかは、全体の統計では平均に溶けて見えなくなるため、
バケットに割って見る必要がある。
"""

import argparse
import csv
import statistics


def percentile(ordered, fraction):
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def describe(values):
    ordered = sorted(values)
    deviation = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
    return {
        "n": len(ordered),
        "mean": statistics.mean(ordered),
        "sd": deviation,
        "var": deviation**2,
        "min": ordered[0],
        "p50": percentile(ordered, 0.50),
        "p95": percentile(ordered, 0.95),
        "p99": percentile(ordered, 0.99),
        "max": ordered[-1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--bucket", type=float, default=30.0, help="[s] 推移の刻み")
    parser.add_argument("--rate", type=float, default=20.0, help="[Hz] 送信レート")
    args = parser.parse_args()

    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))
    values = [float(row["end_to_end"]) for row in rows]
    elapsed = [float(row["elapsed"]) for row in rows]

    stats = describe(values)
    print("=== 全体 ===")
    print(
        "| 区間 | mean | SD | 分散 | min | p50 | p95 | p99 | max |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    print(
        f"| zenoh | {stats['mean']:.1f} | {stats['sd']:.1f} | {stats['var']:.1f} | "
        f"{stats['min']:.1f} | {stats['p50']:.1f} | {stats['p95']:.1f} | "
        f"{stats['p99']:.1f} | {stats['max']:.1f} |"
    )

    span = max(elapsed) - min(elapsed)
    print(f"\nn={stats['n']}  測定長 {span:.1f} s  実効受信レート {stats['n'] / span:.2f} Hz")

    buckets = {}
    for moment, value in zip(elapsed, values):
        buckets.setdefault(int(moment // args.bucket) * args.bucket, []).append(value)
    print(f"\n=== {args.bucket:.0f} 秒ごとの推移 ===")
    print("| 経過 [s] | n | 受信 [Hz] | p50 | p95 | max |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for start in sorted(buckets):
        chunk = describe(buckets[start])
        print(
            f"| {start:.0f}–{start + args.bucket:.0f} | {chunk['n']} | "
            f"{chunk['n'] / args.bucket:.1f} | {chunk['p50']:.1f} | "
            f"{chunk['p95']:.1f} | {chunk['max']:.1f} |"
        )


if __name__ == "__main__":
    main()
