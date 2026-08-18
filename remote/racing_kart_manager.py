#!/usr/bin/env python3
"""racing_kart_manager の ROS ノード。

判断はすべて racing_kart_manager_core の純関数が行い、このファイルは
「購読して、呼んで、publish する」だけの薄い層に徹する。

仕様: docs/spec/multi-vehicle-start-stop.md
"""

from __future__ import annotations

import dataclasses

import rclpy
from autoware_auto_vehicle_msgs.msg import ControlModeReport, VelocityReport
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from sensor_msgs.msg import Joy
from std_msgs.msg import String

from racing_kart_manager_core import (
    INITIAL_STATE,
    Event,
    EventKind,
    JoyObservation,
    JoyValue,
    ManagerState,
    Mode,
    VehicleObservation,
    next_state,
    parse_command,
    parse_vehicles,
    spec_for,
    status,
    status_to_json,
    transform,
)

#: 車両側の debug/status を読むために必要。遠隔PCのイメージに入っていない場合、
#: emergency は UNKNOWN のままになり、すべての操作が塞がれる（安全側）。
try:
    from racing_kart_msgs.msg import VehicleDebug

    HAS_VEHICLE_DEBUG = True
except ImportError:  # pragma: no cover - 環境依存
    VehicleDebug = None
    HAS_VEHICLE_DEBUG = False

STATUS_PUBLISH_RATE_HZ = 5.0

#: GUI を後から起動しても最新状態が出るように transient_local にする
STATUS_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)

#: ボタン押下を取りこぼさない
COMMAND_QOS = QoSProfile(
    depth=10,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
)


def to_core_joy(msg: Joy) -> JoyValue:
    stamp = msg.header.stamp
    return JoyValue(
        axes=tuple(float(a) for a in msg.axes),
        buttons=tuple(int(b) for b in msg.buttons),
        stamp_ns=int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec),
    )


def to_ros_joy(value: JoyValue) -> Joy:
    msg = Joy()
    msg.header.stamp.sec = value.stamp_ns // 1_000_000_000
    msg.header.stamp.nanosec = value.stamp_ns % 1_000_000_000
    msg.axes = [float(a) for a in value.axes]
    msg.buttons = [int(b) for b in value.buttons]
    return msg


class RacingKartManagerNode(Node):
    def __init__(self, vehicles: tuple[str, ...]) -> None:
        super().__init__("racing_kart_manager")

        self._vehicles = vehicles
        self._state: ManagerState = INITIAL_STATE
        self._stopping_since: float | None = None

        self._joy: JoyValue | None = None
        self._joy_at: float | None = None
        self._velocity: dict[str, tuple[float, float]] = {}
        self._debug: dict[str, tuple[bool, float]] = {}
        self._control_mode: dict[str, tuple[int, float]] = {}

        self._joy_publishers = {
            vehicle_id: self.create_publisher(
                Joy, f"/{vehicle_id}/racing_kart/joy", 1
            )
            for vehicle_id in vehicles
        }

        self.create_subscription(Joy, "/racing_kart/joy", self._on_joy, 1)

        for vehicle_id in vehicles:
            self.create_subscription(
                VelocityReport,
                f"/{vehicle_id}/vehicle/status/velocity_status",
                self._velocity_callback(vehicle_id),
                1,
            )
            self.create_subscription(
                ControlModeReport,
                f"/{vehicle_id}/vehicle/status/control_mode",
                self._control_mode_callback(vehicle_id),
                1,
            )
            if HAS_VEHICLE_DEBUG:
                self.create_subscription(
                    VehicleDebug,
                    f"/{vehicle_id}/racing_kart/debug/status",
                    self._debug_callback(vehicle_id),
                    1,
                )

        self._status_publisher = self.create_publisher(
            String, "/racing_kart_manager/status", STATUS_QOS
        )
        self.create_subscription(
            String, "/racing_kart_manager/command", self._on_command, COMMAND_QOS
        )
        self.create_timer(1.0 / STATUS_PUBLISH_RATE_HZ, self._publish_status)

        if not HAS_VEHICLE_DEBUG:
            self.get_logger().error(
                "racing_kart_msgs が見つかりません。"
                "debug/status を購読できないため emergency は常に UNKNOWN になり、"
                "すべての操作が塞がれます。遠隔PCのイメージに racing_kart_msgs を入れてください。"
            )
        self.get_logger().info(f"racing_kart_manager started. vehicles={vehicles}")

    # ------------------------------------------------------------------
    # 時刻と観測
    # ------------------------------------------------------------------

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _age(self, at: float | None) -> float | None:
        return None if at is None else self._now() - at

    def _observations(self) -> dict[str, VehicleObservation]:
        result = {}
        for vehicle_id in self._vehicles:
            velocity = self._velocity.get(vehicle_id)
            debug = self._debug.get(vehicle_id)
            mode = self._control_mode.get(vehicle_id)
            result[vehicle_id] = VehicleObservation(
                vehicle_id=vehicle_id,
                velocity_mps=None if velocity is None else velocity[0],
                velocity_age_s=None if velocity is None else self._age(velocity[1]),
                emergency=None if debug is None else debug[0],
                debug_age_s=None if debug is None else self._age(debug[1]),
                control_mode=None if mode is None else mode[0],
                control_mode_age_s=None if mode is None else self._age(mode[1]),
            )
        return result

    def _joy_observation(self) -> JoyObservation:
        return JoyObservation(joy=self._joy, age_s=self._age(self._joy_at))

    def _state_now(self) -> ManagerState:
        """停止プロトコルの経過秒を今の時刻で埋めた状態。"""
        if self._state.mode is Mode.STOPPING and self._stopping_since is not None:
            return dataclasses.replace(
                self._state, stopping_elapsed_s=self._now() - self._stopping_since
            )
        return self._state

    # ------------------------------------------------------------------
    # 遷移
    # ------------------------------------------------------------------

    def _advance(self, event: Event) -> None:
        before = self._state_now()
        after = next_state(
            before,
            event,
            self._observations(),
            self._joy_observation(),
            self._vehicles,
        )
        if after.mode is Mode.STOPPING:
            if before.mode is not Mode.STOPPING:
                self._stopping_since = self._now()
        else:
            self._stopping_since = None

        if after != before:
            self.get_logger().info(
                f"mode {before.mode.name} -> {after.mode.name}"
                f"{' (' + after.selected + ')' if after.selected else ''}"
            )
        self._state = after

    # ------------------------------------------------------------------
    # コールバック
    # ------------------------------------------------------------------

    def _on_joy(self, msg: Joy) -> None:
        """joy 受信が唯一の publish 契機。

        タイマーで publish すると、ジョイスティックが死んでも車両には新鮮な
        joy が届き続け、5秒の生存チェーン (REQ-04) が成立しなくなる。
        """
        self._joy = to_core_joy(msg)
        self._joy_at = self._now()
        self._advance(Event(kind=EventKind.JOY))

        outgoing = transform(self._joy, spec_for(self._state, self._vehicles))
        for vehicle_id, value in outgoing.items():
            self._joy_publishers[vehicle_id].publish(to_ros_joy(value))

    def _on_command(self, msg: String) -> None:
        event = parse_command(msg.data)
        if event is None:
            self.get_logger().warn(f"invalid command dropped: {msg.data!r}")
            return
        self._advance(event)

    def _velocity_callback(self, vehicle_id: str):
        def callback(msg: VelocityReport) -> None:
            self._velocity[vehicle_id] = (float(msg.longitudinal_velocity), self._now())

        return callback

    def _control_mode_callback(self, vehicle_id: str):
        def callback(msg: ControlModeReport) -> None:
            self._control_mode[vehicle_id] = (int(msg.mode), self._now())

        return callback

    def _debug_callback(self, vehicle_id: str):
        def callback(msg) -> None:
            self._debug[vehicle_id] = (bool(msg.emergency), self._now())

        return callback

    def _publish_status(self) -> None:
        """status だけはタイマーで出す。joy の送出経路ではないので生存チェーンに影響しない。"""
        state = self._state_now()
        current = status(
            state, self._observations(), self._joy_observation(), self._vehicles
        )
        message = String()
        message.data = status_to_json(current, self.get_clock().now().nanoseconds)
        self._status_publisher.publish(message)


def main() -> None:
    import sys

    vehicles = parse_vehicles(sys.argv[1:])
    if vehicles is None:
        print(
            "usage: racing_kart_manager.py <VEHICLE_ID> [VEHICLE_ID ...]\n"
            "  例: racing_kart_manager.py A2 A3 A7\n"
            "  対象車両を1台以上、重複なしで指定してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    rclpy.init()
    node = RacingKartManagerNode(vehicles)
    try:
        rclpy.spin(node)  # 既定の SingleThreadedExecutor。到着順の処理を保証する
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
