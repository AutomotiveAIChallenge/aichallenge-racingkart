#!/usr/bin/env python3
"""config.yaml と `ros2 param dump /mpc_controller` の出力を比べる。

Usage: compare_params.py <config.yaml> <params_runtime.yaml>
終了コード: 0 = 比較できた全項目が一致、1 = 差あり、2 = エラー（読めない、例外、比較できた項目が 0）。
比較するのは実行時パラメータのうち config.yaml の mpc: 節に対応があるもの
（v_max, ay_max, Q0..2, R0..1, QN0..2, steering_tire_angle_gain_var, accel_low_pass_gain,
 steer_low_pass_gain, wp_id_offset）。対応の無い実行時パラメータは「比較対象外」として一覧に出す。
"""
import re
import sys

import yaml


def config_value(mpc, name):
    m = re.fullmatch(r"(Q|R|QN)(\d)", name)
    if m:
        arr = mpc.get(m.group(1))
        idx = int(m.group(2))
        return (arr[idx], True) if isinstance(arr, list) and idx < len(arr) else (None, False)
    if name in mpc:
        return mpc[name], True
    return None, False


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    cfg = yaml.safe_load(open(sys.argv[1]))
    dump = yaml.safe_load(open(sys.argv[2]))
    mpc = cfg.get("mpc", {})
    # dump の形: {'/mpc_controller': {'ros__parameters': {...}}}
    params = None
    for node, body in dump.items():
        if isinstance(body, dict) and "ros__parameters" in body:
            params = body["ros__parameters"]
            node_name = node
    if params is None:
        print("ros__parameters が見つからない", file=sys.stderr)
        return 2
    diffs, same, skipped = [], [], []
    for name in sorted(params):
        cv, found = config_value(mpc, name)
        rv = params[name]
        if not found:
            skipped.append(name)
            continue
        eq = (abs(float(cv) - float(rv)) <= 1e-9 * max(1.0, abs(float(cv)))) if isinstance(cv, (int, float)) and isinstance(rv, (int, float)) else (cv == rv)
        (same if eq else diffs).append((name, cv, rv))
    if not same and not diffs:
        print("比較できた項目が 0（config.yaml の mpc: 節か dump の形が想定と違う）", file=sys.stderr)
        return 2
    print(f"node={node_name} compared={len(same) + len(diffs)} diff={len(diffs)} not_in_config={len(skipped)}")
    for name, cv, rv in diffs:
        print(f"DIFF {name}: config={cv} runtime={rv}")
    for name, cv, rv in same:
        print(f"same {name}: {rv}")
    print("not_in_config: " + " ".join(skipped))
    return 1 if diffs else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"compare_params: error: {e}", file=sys.stderr)
        sys.exit(2)
