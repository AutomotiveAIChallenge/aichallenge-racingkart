#!/usr/bin/env python3

from dataclasses import dataclass
from typing import Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from autoware_auto_control_msgs.msg import AckermannControlCommand
from autoware_auto_vehicle_msgs.msg import GearCommand, VelocityReport
from std_msgs.msg import String


@dataclass
class Sample:
    stamp: float
    value: object


class RecoveryTopicWatch(Node):
    """Print compact recovery supervisor telemetry for manual smoke checks."""

    def __init__(self) -> None:
        super().__init__("recovery_topic_watch")

        self.declare_parameter("rate_hz", 2.0)
        self.declare_parameter("stale_timeout_sec", 1.0)

        rate_hz = max(0.2, float(self.get_parameter("rate_hz").value))
        self._stale_timeout_sec = max(0.1, float(self.get_parameter("stale_timeout_sec").value))

        self._state: Optional[Sample] = None
        self._gear: Optional[Sample] = None
        self._nominal_cmd: Optional[Sample] = None
        self._final_cmd: Optional[Sample] = None
        self._velocity: Optional[Sample] = None

        self.create_subscription(String, "/recovery_supervisor/state", self._on_state, 1)
        self.create_subscription(GearCommand, "/control/command/gear_cmd", self._on_gear, 1)
        self.create_subscription(
            AckermannControlCommand,
            "/control/command/nominal_control_cmd",
            self._on_nominal_cmd,
            1,
        )
        self.create_subscription(
            AckermannControlCommand,
            "/control/command/control_cmd",
            self._on_final_cmd,
            1,
        )
        self.create_subscription(
            VelocityReport,
            "/vehicle/status/velocity_status",
            self._on_velocity,
            1,
        )

        self.create_timer(1.0 / rate_hz, self._print_status)

    def _now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_state(self, msg: String) -> None:
        self._state = Sample(self._now_sec(), msg.data)

    def _on_gear(self, msg: GearCommand) -> None:
        self._gear = Sample(self._now_sec(), int(msg.command))

    def _on_nominal_cmd(self, msg: AckermannControlCommand) -> None:
        self._nominal_cmd = Sample(self._now_sec(), msg)

    def _on_final_cmd(self, msg: AckermannControlCommand) -> None:
        self._final_cmd = Sample(self._now_sec(), msg)

    def _on_velocity(self, msg: VelocityReport) -> None:
        self._velocity = Sample(self._now_sec(), float(msg.longitudinal_velocity))

    def _is_stale(self, sample: Optional[Sample], now: float) -> bool:
        return sample is None or now - sample.stamp > self._stale_timeout_sec

    def _sample_age(self, sample: Optional[Sample], now: float) -> str:
        if sample is None:
            return "none"
        age = now - sample.stamp
        if age > self._stale_timeout_sec:
            return f"stale:{age:.1f}s"
        return f"{age:.1f}s"

    def _format_state(self, now: float) -> str:
        if self._state is None:
            return "state=none"
        return f"state={self._state.value}({self._sample_age(self._state, now)})"

    def _format_gear(self, now: float) -> str:
        if self._gear is None:
            return "gear=none"
        return f"gear={self._gear_name(int(self._gear.value))}({self._sample_age(self._gear, now)})"

    def _format_velocity(self, now: float) -> str:
        if self._velocity is None:
            return "vel=none"
        stale = "*" if self._is_stale(self._velocity, now) else ""
        return f"vel={float(self._velocity.value):+.2f}m/s{stale}"

    def _format_command(self, label: str, sample: Optional[Sample], now: float) -> str:
        if sample is None:
            return f"{label}=none"
        msg = sample.value
        if not isinstance(msg, AckermannControlCommand):
            return f"{label}=invalid"
        stale = "*" if self._is_stale(sample, now) else ""
        return (
            f"{label}=v:{msg.longitudinal.speed:+.2f} "
            f"a:{msg.longitudinal.acceleration:+.2f} "
            f"steer:{msg.lateral.steering_tire_angle:+.2f}{stale}"
        )

    def _print_status(self) -> None:
        now = self._now_sec()
        print(
            " | ".join(
                [
                    self._format_state(now),
                    self._format_gear(now),
                    self._format_velocity(now),
                    self._format_command("nominal", self._nominal_cmd, now),
                    self._format_command("final", self._final_cmd, now),
                ]
            ),
            flush=True,
        )

    def _gear_name(self, gear: int) -> str:
        names = {
            getattr(GearCommand, "DRIVE", 2): "DRIVE",
            getattr(GearCommand, "REVERSE", 20): "REVERSE",
            getattr(GearCommand, "PARK", 22): "PARK",
            getattr(GearCommand, "NEUTRAL", 1): "NEUTRAL",
            getattr(GearCommand, "LOW", 3): "LOW",
        }
        return names.get(gear, str(gear))


def main() -> None:
    rclpy.init()
    node = RecoveryTopicWatch()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
