#!/usr/bin/env python3
"""IMU ジャイロバイアス簡易チェック（read-only 診断）.

autoware 起動後（setup_check.sh --phase runtime のタイミング）に、車両が
完全に静止している状態で /sensing/imu/imu_raw の角速度を数秒サンプリングし、
静止時バイアス（3 軸平均）を測る。そのうえで imu_corrector の現在の
angular_velocity_offset_* と比較し、乖離が大きい場合に「警告」と
「param.yaml へどう書けばよいか（符号込み）」を出力する。

値の書き換えは一切しない（人間が判断して param.yaml を直す想定）。

符号について（imu_corrector のソースから）:
    imu_corrector は  output = raw - angular_velocity_offset  で補正する。
    （imu_corrector_core.cpp: angular_velocity.z -= angular_velocity_offset_z_imu_link_）
    静止時の補正後角速度を 0 にしたいので、offset には測定した生バイアス
    （平均）を符号そのままで代入すればよい。しかも imu_corrector の入力は
    補正前の imu_raw なので、ここで観測する平均がそのまま「生バイアス」。
    したがって「+ にずれていれば param にも + を書く」。

終了コード:
    0 : 全軸 OK（乖離・ノイズとも許容範囲）
    2 : WARN（乖離が大きい / 静止時ノイズが大きい / 現行 offset を取得できず
        比較できなかった。いずれも測定自体は成功）
    3 : 測定不能（サンプリング中に車両が動いた / imu_raw が来ない）
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu

from rcl_interfaces.msg import ParameterType
from rcl_interfaces.srv import GetParameters

# VelocityReport はディストリ/世代で名前空間が変わるため両対応で import する。
try:  # 新しめの Autoware
    from autoware_vehicle_msgs.msg import VelocityReport
except ImportError:  # 旧 autoware_auto 系
    try:
        from autoware_auto_vehicle_msgs.msg import VelocityReport
    except ImportError:
        VelocityReport = None

EXIT_OK = 0
EXIT_WARN = 2
EXIT_MEASURE_FAIL = 3

AXES = ("x", "y", "z")


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

    def fetch_current_offsets(self, timeout_sec: float = 3.0) -> dict[str, float] | None:
        """imu_corrector から現在の angular_velocity_offset_* を取得する.

        取得できなければ None を返す（比較を諦め、測定値のみ提示する）。
        """
        service = f"{self._args.corrector_node}/get_parameters"
        client = self.create_client(GetParameters, service)
        if not client.wait_for_service(timeout_sec=timeout_sec):
            return None

        req = GetParameters.Request()
        req.names = [f"angular_velocity_offset_{axis}" for axis in AXES]
        future = client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout_sec)
        response = future.result()
        if response is None or len(response.values) != len(AXES):
            return None

        offsets: dict[str, float] = {}
        for axis, value in zip(AXES, response.values):
            if value.type != ParameterType.PARAMETER_DOUBLE:
                return None
            offsets[axis] = value.double_value
        return offsets


def _param_yaml_hint(args: argparse.Namespace) -> str:
    return args.param_yaml


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imu-topic", default="/sensing/imu/imu_raw")
    parser.add_argument("--velocity-topic", default="/vehicle/status/velocity_status")
    parser.add_argument("--corrector-node", default="/sensing/imu/imu_corrector")
    parser.add_argument("--duration", type=float, default=5.0,
                        help="サンプリング秒数（warmup を除く）")
    parser.add_argument("--warmup", type=float, default=2.0,
                        help="開始直後に捨てる秒数（IMU の warmup）")
    parser.add_argument("--velocity-threshold", type=float, default=0.05,
                        help="静止判定に使う |longitudinal_velocity| の上限 [m/s]")
    parser.add_argument("--warn-threshold", type=float, default=0.005,
                        help="bias と現行 offset の乖離の警告閾値 [rad/s]")
    parser.add_argument("--std-threshold", type=float, default=0.01,
                        help="静止時ジャイロ std の警告閾値 [rad/s]")
    parser.add_argument("--param-yaml",
                        default="aichallenge/workspace/src/aichallenge_submit/"
                                "imu_corrector/config/imu_corrector.param.yaml",
                        help="書き換え先の案内に表示する param.yaml パス")
    args = parser.parse_args()

    rclpy.init()
    node = ImuBiasChecker(args)

    print("==== IMU gyro bias check (read-only) ====")
    print(f"imu topic      : {args.imu_topic}")
    print(f"sampling       : {args.duration:.1f}s (after {args.warmup:.1f}s warmup)")
    print("Keep the vehicle completely stationary during sampling.")

    node.start_collecting()
    deadline = time.monotonic() + args.warmup + args.duration
    moved = False
    try:
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            # サンプリング中に動きを検知したら即中断（静止前提が崩れる）
            if node.velocity_seen and node.max_abs_velocity > args.velocity_threshold:
                moved = True
                break
    finally:
        pass

    # --- 測定不能ケース ---
    if moved:
        print(f"{'':2}❌ Vehicle moved during sampling "
              f"(max |velocity|={node.max_abs_velocity:.3f} m/s > "
              f"{args.velocity_threshold:.3f}). Bias not measured.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_MEASURE_FAIL

    if node.sample_count == 0:
        print(f"{'':2}❌ No messages on {args.imu_topic}. "
              "Is the IMU driver up and publishing?")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_MEASURE_FAIL

    stats = node.stats()
    offsets = node.fetch_current_offsets()

    if node.velocity_seen:
        print(f"stationary     : OK (max |velocity|={node.max_abs_velocity:.3f} m/s)")
    else:
        print(f"stationary     : velocity_status not received on {args.velocity_topic}; "
              "relying on manual confirmation")
    print(f"samples        : {node.sample_count}")
    print("")

    # --- 比較表 ---
    header = f"{'axis':4}  {'bias[rad/s]':>13}  {'offset[rad/s]':>14}  {'diff':>12}  {'std':>10}  status"
    print(header)
    print("-" * len(header))

    warn = False
    noisy = False
    fix_lines: list[str] = []
    for axis in AXES:
        mean, std = stats[axis]
        current = offsets[axis] if offsets is not None else None
        diff = (mean - current) if current is not None else None

        status = "OK"
        axis_warn = False
        if std > args.std_threshold:
            status = "WARN(noisy)"
            axis_warn = True
            noisy = True
        if diff is not None and abs(diff) > args.warn_threshold:
            status = "WARN(offset)" if status == "OK" else "WARN(offset,noisy)"
            axis_warn = True
        elif current is None:
            # offset を読めなかった時点で「許容範囲内」とは言えないので必ず警告する。
            status = "WARN(no-offset)" if status == "OK" else "WARN(no-offset,noisy)"
            axis_warn = True

        current_str = f"{current:+.6f}" if current is not None else "   n/a   "
        diff_str = f"{diff:+.6f}" if diff is not None else "   n/a   "
        print(f"{axis:4}  {mean:+.6f}  {current_str:>14}  {diff_str:>12}  {std:.6f}  {status}")

        if axis_warn:
            warn = True
        if diff is not None and abs(diff) > args.warn_threshold:
            fix_lines.append(
                f"  angular_velocity_offset_{axis}: {mean:.6f}"
            )

    print("")

    if warn:
        print("⚠️  IMU gyro bias check raised a warning.")
        if offsets is None:
            print("    Could not read angular_velocity_offset_* from "
                  f"{args.corrector_node}; the bias comparison was skipped.")
            print("    Check that imu_corrector is running, then re-run this check.")
            print("    Measured stationary bias, for reference:")
            for axis in AXES:
                print(f"      angular_velocity_offset_{axis}: {stats[axis][0]:.6f}")
        if noisy:
            print(f"    Stationary gyro noise exceeds {args.std_threshold} rad/s. "
                  "The vehicle may not have been")
            print("    completely stationary (engine/fan vibration, someone leaning on it),")
            print("    or the IMU itself is noisy. Re-measure on a settled vehicle first.")
        if fix_lines:
            print("    How to fix — edit the param file and set the measured bias as-is:")
            print(f"      file: {_param_yaml_hint(args)}")
            for line in fix_lines:
                print(f"      {line.strip()}")
            print("    Note: imu_corrector computes (raw - offset), so write the")
            print("          measured bias value with its sign unchanged (+ stays +).")
            print("    Restart autoware after editing so the new offset takes effect.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_WARN

    print("✅ IMU gyro bias is within tolerance of the current imu_corrector offsets.")
    node.destroy_node()
    rclpy.shutdown()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
