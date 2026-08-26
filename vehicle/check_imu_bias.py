#!/usr/bin/env python3
"""IMU ジャイロバイアス計測・書き込みツール.

autoware 起動後（setup_check.sh --phase runtime のタイミング）に、車両が
完全に静止している状態で /sensing/imu/imu_raw の角速度を数秒サンプリングし、
静止時バイアス（3 軸平均）を測る。静止時ノイズ（std）が十分小さければ、
imu_corrector.param.yaml に書かれている現在の angular_velocity_offset_* を
測定値でそのまま上書きする（乖離の大小によらず、閾値判定はしない）。

符号について（imu_corrector のソースから）:
    imu_corrector は  output = raw - angular_velocity_offset  で補正する。
    （imu_corrector_core.cpp: angular_velocity.z -= angular_velocity_offset_z_imu_link_）
    静止時の補正後角速度を 0 にしたいので、offset には測定した生バイアス
    （平均）を符号そのままで代入すればよい。しかも imu_corrector の入力は
    補正前の imu_raw なので、ここで観測する平均がそのまま「生バイアス」。
    したがって「+ にずれていれば param にも + を書く」。

反映タイミングについて:
    imu_corrector はパラメータを起動時に一度だけ読む（動的リロードなし）。
    このスクリプトが param.yaml を書き換えても、今動いている autoware には
    反映されない。次回 autoware を再起動したときから新しい値が使われる。

静止確認について:
    このスクリプトは1回サンプリングするだけで、再サンプリングの判断はしない。
    静止確認の y/N、および静止時ノイズ（std）超過時に「車両に触れないでください」
    と表示して再計測するかどうかの確認は、いずれも setup_check.sh 側の
    check_imu_bias() が担当する（このスクリプトが呼ばれた時点で人間は
    「静止している」と既に答えている）。std 超過はそれ専用の終了コード
    （exit 4）で返し、呼び出し側が確認の上で再実行できるようにしている。

終了コード:
    0 : 測定成功。param.yaml の angular_velocity_offset_* を新しい値で上書きした
    3 : 測定不能（サンプリング中に車両が動いた / imu_raw が来ない /
        param.yaml を読めなかった）
    4 : 静止時ノイズが大きい（バイアス推定値が信用できないので書き込まず、
        再計測を促す）
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu

# VelocityReport はディストリ/世代で名前空間が変わるため両対応で import する。
try:  # 新しめの Autoware
    from autoware_vehicle_msgs.msg import VelocityReport
except ImportError:  # 旧 autoware_auto 系
    try:
        from autoware_auto_vehicle_msgs.msg import VelocityReport
    except ImportError:
        VelocityReport = None

EXIT_OK = 0
EXIT_MEASURE_FAIL = 3
EXIT_NOISY = 4

AXES = ("x", "y", "z")

# stddev が意味を持つには最低これだけのサンプルが要る。1サンプルしか取れない
# ようなケース（IMU がほぼ来ていない）で std=0.0 になり静止時ノイズチェックを
# すり抜けてしまうのを防ぐための下限。
MIN_SAMPLES = 10

# param.yaml の対象3行にだけマッチする（インデント・コメントはそのまま残すため
# yaml ライブラリでの読み書きはせず、数値部分だけを直接置換する）。
_OFFSET_LINE_RE = {
    axis: re.compile(
        r"^\s*angular_velocity_offset_" + axis + r"\s*:\s*"
        r"(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    )
    for axis in AXES
}


def read_current_offsets(param_yaml_path: str) -> dict[str, float] | None:
    """param.yaml から angular_velocity_offset_* の現在値を直接読む."""
    try:
        with open(param_yaml_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    offsets: dict[str, float] = {}
    for line in lines:
        for axis, pattern in _OFFSET_LINE_RE.items():
            m = pattern.match(line)
            if m:
                offsets[axis] = float(m.group("value"))
    if len(offsets) != len(AXES):
        return None
    return offsets


def write_new_offsets(param_yaml_path: str, new_offsets: dict[str, float]) -> bool:
    """param.yaml の angular_velocity_offset_* 3行だけを新しい測定値で上書きする."""
    try:
        with open(param_yaml_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False

    updated_axes: set[str] = set()
    for i, line in enumerate(lines):
        for axis, pattern in _OFFSET_LINE_RE.items():
            m = pattern.match(line)
            if m:
                start, end = m.span("value")
                lines[i] = f"{line[:start]}{new_offsets[axis]:.6f}{line[end:]}"
                updated_axes.add(axis)

    if updated_axes != set(AXES):
        return False

    # 途中で落ちても param.yaml が空/半端な状態で残らないよう、同じディレクトリに
    # 一時ファイルを書いてから atomic に差し替える（失敗時は旧値がそのまま残る）。
    # symlink（--symlink-install した install 側のパス）を渡された場合も実体を差し替える。
    target = os.path.realpath(param_yaml_path)
    tmp_path = f"{target}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False
    return True


class ImuBiasChecker(Node):
    """imu_raw と velocity_status を購読して静止時ジャイロバイアスを集計する."""

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("imu_bias_checker")
        self._args = args
        self._samples: dict[str, list[float]] = {axis: [] for axis in AXES}
        self._max_abs_velocity = 0.0
        self._velocity_seen = False
        self._collect_from = None  # warmup 経過後の monotonic 時刻

        self.create_subscription(
            Imu, args.imu_topic, self._on_imu, qos_profile_sensor_data
        )
        if VelocityReport is not None:
            self.create_subscription(
                VelocityReport,
                args.velocity_topic,
                self._on_velocity,
                qos_profile_sensor_data,
            )

    def start_collecting(self) -> None:
        self._collect_from = time.monotonic()

    def _on_imu(self, msg: Imu) -> None:
        if self._collect_from is None:
            return
        if (time.monotonic() - self._collect_from) < self._args.warmup:
            return  # warmup 中のサンプルは捨てる
        av = msg.angular_velocity
        self._samples["x"].append(av.x)
        self._samples["y"].append(av.y)
        self._samples["z"].append(av.z)

    def _on_velocity(self, msg: VelocityReport) -> None:
        self._velocity_seen = True
        v = abs(getattr(msg, "longitudinal_velocity", 0.0))
        if v > self._max_abs_velocity:
            self._max_abs_velocity = v

    @property
    def sample_count(self) -> int:
        return len(self._samples["x"])

    @property
    def max_abs_velocity(self) -> float:
        return self._max_abs_velocity

    @property
    def velocity_seen(self) -> bool:
        return self._velocity_seen

    def stats(self) -> dict[str, tuple[float, float]]:
        """各軸の (mean, stddev) を返す."""
        result: dict[str, tuple[float, float]] = {}
        for axis in AXES:
            data = self._samples[axis]
            mean = statistics.fmean(data)
            std = statistics.pstdev(data) if len(data) > 1 else 0.0
            result[axis] = (mean, std)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imu-topic", default="/sensing/imu/imu_raw")
    parser.add_argument("--velocity-topic", default="/vehicle/status/velocity_status")
    parser.add_argument("--duration", type=float, default=5.0,
                        help="サンプリング秒数（warmup を除く）")
    parser.add_argument("--warmup", type=float, default=2.0,
                        help="開始直後に捨てる秒数（IMU の warmup）")
    parser.add_argument("--velocity-threshold", type=float, default=0.05,
                        help="静止判定に使う |longitudinal_velocity| の上限 [m/s]")
    parser.add_argument("--std-threshold", type=float, default=0.03,
                        help="静止時ジャイロ std の警告閾値 [rad/s]。"
                             "暫定値（imu_corrector.param.yaml の想定ノイズ既定値に合わせている）。"
                             "実測を踏まえて後で絞り込む")
    parser.add_argument("--param-yaml",
                        default="/aichallenge/workspace/src/aichallenge_submit/"
                                "imu_corrector/config/imu_corrector.param.yaml",
                        help="書き換え対象の param.yaml パス（コンテナ内の絶対パス）")
    args = parser.parse_args()

    rclpy.init()
    node = ImuBiasChecker(args)

    print("==== IMU gyro bias check ====")
    print(f"imu topic      : {args.imu_topic}")
    print(f"sampling       : {args.duration:.1f}s (after {args.warmup:.1f}s warmup)")
    print("Keep the vehicle completely stationary during sampling.")

    node.start_collecting()
    deadline = time.monotonic() + args.warmup + args.duration
    moved = False
    while rclpy.ok() and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        # サンプリング中に動きを検知したら即中断（静止前提が崩れる）
        if node.velocity_seen and node.max_abs_velocity > args.velocity_threshold:
            moved = True
            break

    # --- 測定不能ケース ---
    if moved:
        print(f"{'':2}❌ Vehicle moved during sampling "
              f"(max |velocity|={node.max_abs_velocity:.3f} m/s > "
              f"{args.velocity_threshold:.3f}). Bias not measured.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_MEASURE_FAIL

    if node.sample_count < MIN_SAMPLES:
        print(f"{'':2}❌ Too few messages on {args.imu_topic} "
              f"({node.sample_count} < {MIN_SAMPLES}). Is the IMU driver up and "
              "publishing at a reasonable rate?")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_MEASURE_FAIL

    stats = node.stats()

    if node.velocity_seen:
        print(f"stationary     : OK (max |velocity|={node.max_abs_velocity:.3f} m/s)")
    else:
        print(f"stationary     : velocity_status not received on {args.velocity_topic}; "
              "relying on manual confirmation")
    print(f"samples        : {node.sample_count}")
    print("")

    noisy = any(std > args.std_threshold for _mean, std in stats.values())

    header = f"{'axis':4}  {'bias[rad/s]':>13}  {'std':>10}  status"
    print(header)
    print("-" * len(header))
    for axis in AXES:
        mean, std = stats[axis]
        status = "WARN(noisy)" if std > args.std_threshold else "OK"
        print(f"{axis:4}  {mean:+.6f}  {std:.6f}  {status}")
    print("")

    # ノイズが大きいと推定バイアス自体が信用できないので、書き込みより先に
    # 独立の終了コードで返し、呼び出し側（setup_check.sh）に再計測の要否を
    # 確認させる。ここでは自動リトライしない。
    if noisy:
        print(f"⚠️  Stationary gyro noise exceeds {args.std_threshold} rad/s "
              "— do not touch the vehicle.")
        print("    The bias estimate above is unreliable while noisy. The vehicle may not")
        print("    have been completely stationary (engine/fan vibration, someone")
        print("    touching it), or the IMU itself is noisy.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_NOISY

    current_offsets = read_current_offsets(args.param_yaml)
    if current_offsets is None:
        print(f"{'':2}❌ Could not read angular_velocity_offset_* from {args.param_yaml}.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_MEASURE_FAIL

    new_offsets = {axis: stats[axis][0] for axis in AXES}
    if not write_new_offsets(args.param_yaml, new_offsets):
        print(f"{'':2}❌ Failed to write new offsets to {args.param_yaml}.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_MEASURE_FAIL

    print(f"Updated {args.param_yaml}:")
    cmp_header = f"{'axis':4}  {'old[rad/s]':>12}  {'new[rad/s]':>12}"
    print(cmp_header)
    print("-" * len(cmp_header))
    for axis in AXES:
        print(f"{axis:4}  {current_offsets[axis]:+.6f}  {new_offsets[axis]:+.6f}")
    print("")
    print("✅ imu_corrector.param.yaml updated.")
    print("   This bias will not take effect until autoware is restarted")
    print("   (imu_corrector reads the parameter once at startup).")

    node.destroy_node()
    rclpy.shutdown()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
