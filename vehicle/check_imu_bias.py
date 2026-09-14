#!/usr/bin/env python3
"""IMU ジャイロバイアス計測・書き込みツール.

autoware 起動後（setup_check.sh --phase runtime のタイミング）に、車両が
完全に静止している状態で /sensing/imu/imu_raw の角速度を数秒サンプリングし、
静止時バイアス（3 軸平均）を測る。静止時ノイズ（std）が十分小さければ、
imu_corrector.param.yaml に書かれている現在の angular_velocity_offset_* を
現在値・実測値・差分を表示し、参加者の承認を確認した場合だけ上書きする。
runtimeチェックでは測定結果を --proposal-output に一時保存し、ホスト側で承認後に
--apply-proposal で適用する。--bias-output の車両別保存元も承認後だけ更新する。
保存元の値は次の提出物へ自動適用しない。

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
    0 : 承認後の更新成功、または --proposal-output による測定結果の保存成功
    3 : 計測・保存・適用失敗（移動 / サンプル不足 / 読み書き失敗 / 計測後の設定変更）
    4 : 静止時ノイズが大きい（バイアス推定値が信用できないので書き込まず、
        再計測を促す）
    5 : 更新見送り（承認なし / 対象設定なし）
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu

from calibration import AXES, atomic_write, confirm_update, parse_offsets, replace_offsets, save_bias

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
EXIT_SKIPPED = 5

# stddev が意味を持つ最小サンプル数。1 サンプルしか取れない（IMU がほぼ来ていない）と
# std=0.0 になり、静止時ノイズチェックをすり抜けてしまう。
MIN_SAMPLES = 10


def apply_proposal(proposal: dict, bias_output: Path | None) -> int:
    """Apply the approved measurement only if the participant settings are still current."""
    try:
        param = Path(proposal["param_yaml"])
        before = param.read_text(encoding="utf-8")
        if before != proposal["current_text"]:
            raise ValueError("participant IMU settings changed after measurement; re-measure")
        updated = replace_offsets(before, proposal["offsets"])
        atomic_write(param, updated)
        if bias_output is not None:
            try:
                save_bias(bias_output, proposal["offsets"])
            except (OSError, ValueError):
                try:
                    atomic_write(param, before)
                except OSError as rollback_error:
                    print(f"❌ Failed to restore param.yaml after bias save failure: {rollback_error}")
                raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"❌ Failed to apply approved IMU bias: {exc}")
        return EXIT_MEASURE_FAIL
    print("✅ imu_corrector.param.yaml updated (participant approved).")
    if bias_output is not None:
        print(f"Saved vehicle IMU bias: {bias_output}")
    print("Restart autoware to apply the new offsets.")
    return EXIT_OK


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
    parser.add_argument("--bias-output", type=Path,
                        help="承認後の測定値を保存する車両別 imu_bias.yaml")
    proposal_mode = parser.add_mutually_exclusive_group()
    proposal_mode.add_argument("--proposal-output", type=Path,
                               help="設定を変更せず、承認用の測定結果を一時保存する")
    proposal_mode.add_argument("--apply-proposal", type=Path,
                               help="参加者の承認後に一時保存した測定結果を適用する")
    args = parser.parse_args()

    if args.apply_proposal is not None:
        try:
            proposal = json.loads(args.apply_proposal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"❌ Could not read IMU proposal: {exc}")
            return EXIT_MEASURE_FAIL
        return apply_proposal(proposal, args.bias_output)

    if not Path(args.param_yaml).is_file():
        print(f"⚠️ No IMU correction settings at {args.param_yaml}; skipping IMU update.")
        return EXIT_SKIPPED

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

    # ノイズが大きいと推定バイアス自体が信用できない。書き込みより先に独立の終了コードで返し、
    # 呼び出し側（setup_check.sh）に再計測の要否を確認させる。ここでは自動リトライしない。
    if noisy:
        print(f"⚠️  Stationary gyro noise exceeds {args.std_threshold} rad/s "
              "— do not touch the vehicle.")
        print("    The bias estimate above is unreliable while noisy. The vehicle may not")
        print("    have been completely stationary (engine/fan vibration, someone")
        print("    touching it), or the IMU itself is noisy.")
        node.destroy_node()
        rclpy.shutdown()
        return EXIT_NOISY

    node.destroy_node()
    rclpy.shutdown()
    try:
        current_text = Path(args.param_yaml).read_text(encoding="utf-8")
        current_offsets = parse_offsets(current_text)
    except OSError as exc:
        print(f"❌ Could not read IMU settings: {exc}")
        return EXIT_MEASURE_FAIL
    except ValueError as exc:
        print(f"⚠️ No supported IMU offsets; skipping IMU update: {exc}")
        return EXIT_SKIPPED

    new_offsets = {axis: stats[axis][0] for axis in AXES}
    print(f"IMUジャイロバイアス [rad/s]: {args.param_yaml}")
    cmp_header = f"{'軸':4}  {'現在値':>12}  {'実測値':>12}  {'差分(実測値−現在値)':>18}"
    print(cmp_header)
    print("-" * len(cmp_header))
    for axis in AXES:
        print(f"{axis:4}  {current_offsets[axis]:+.6f}  {new_offsets[axis]:+.6f}  "
              f"{new_offsets[axis] - current_offsets[axis]:+.6f}")
    print("")
    proposal = {"param_yaml": args.param_yaml, "current_text": current_text, "offsets": new_offsets}
    if args.proposal_output is not None:
        try:
            atomic_write(args.proposal_output, json.dumps(proposal, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"❌ Could not save IMU proposal: {exc}")
            return EXIT_MEASURE_FAIL
        print("IMU measurement complete; settings have not been changed.")
        return EXIT_OK
    if not confirm_update("実測値で上書きしますか？\n参加者の承認を確認してください。 [y/N]: "):
        print("⚠️ IMU measured; update declined. Participant settings and saved bias retained.")
        return EXIT_SKIPPED
    return apply_proposal(proposal, args.bias_output)


if __name__ == "__main__":
    sys.exit(main())
