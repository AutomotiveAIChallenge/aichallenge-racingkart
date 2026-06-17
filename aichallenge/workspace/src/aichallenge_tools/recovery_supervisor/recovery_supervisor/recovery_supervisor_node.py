#!/usr/bin/env python3

import copy
import math
from enum import Enum
from typing import Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from autoware_auto_control_msgs.msg import AckermannControlCommand
from autoware_auto_vehicle_msgs.msg import GearCommand, GearReport, VelocityReport
from std_msgs.msg import String


class RecoveryState(str, Enum):
    NORMAL = "NORMAL"
    STUCK_DETECTED = "STUCK_DETECTED"
    REVERSING = "REVERSING"
    DRIVE_SETTLE = "DRIVE_SETTLE"
    COOLDOWN = "COOLDOWN"


class RecoverySupervisor(Node):
    """Final control mux that backs up when forward throttle cannot move the kart."""

    def __init__(self) -> None:
        super().__init__("recovery_supervisor")

        self.declare_parameter("stuck_speed_threshold", 0.2)
        self.declare_parameter("stuck_duration", 1.0)
        self.declare_parameter("command_speed_threshold", 1.0)
        self.declare_parameter("command_accel_threshold", 0.3)
        self.declare_parameter("moving_speed_threshold", 0.5)
        self.declare_parameter("reverse_speed", 1.0)
        self.declare_parameter("reverse_accel", 1.0)
        self.declare_parameter("reverse_duration", 2.0)
        self.declare_parameter("drive_settle_duration", 0.5)
        self.declare_parameter("cooldown_duration", 3.0)
        self.declare_parameter("nominal_timeout_sec", 0.5)
        self.declare_parameter("velocity_timeout_sec", 0.5)
        self.declare_parameter("timer_hz", 20.0)

        self.stuck_speed_threshold = float(self.get_parameter("stuck_speed_threshold").value)
        self.stuck_duration = float(self.get_parameter("stuck_duration").value)
        self.command_speed_threshold = float(self.get_parameter("command_speed_threshold").value)
        self.command_accel_threshold = float(self.get_parameter("command_accel_threshold").value)
        self.moving_speed_threshold = float(self.get_parameter("moving_speed_threshold").value)
        self.reverse_speed = float(self.get_parameter("reverse_speed").value)
        self.reverse_accel = float(self.get_parameter("reverse_accel").value)
        self.reverse_duration = float(self.get_parameter("reverse_duration").value)
        self.drive_settle_duration = float(self.get_parameter("drive_settle_duration").value)
        self.cooldown_duration = float(self.get_parameter("cooldown_duration").value)
        self.nominal_timeout_sec = float(self.get_parameter("nominal_timeout_sec").value)
        self.velocity_timeout_sec = float(self.get_parameter("velocity_timeout_sec").value)
        timer_hz = max(1.0, float(self.get_parameter("timer_hz").value))

        self._control_pub = self.create_publisher(
            AckermannControlCommand, "/control/command/control_cmd", 1
        )
        self._gear_pub = self.create_publisher(GearCommand, "/control/command/gear_cmd", 1)
        self._state_pub = self.create_publisher(String, "/recovery_supervisor/state", 1)

        self.create_subscription(
            AckermannControlCommand,
            "/control/command/nominal_control_cmd",
            self._nominal_callback,
            1,
        )
        self.create_subscription(
            VelocityReport,
            "/vehicle/status/velocity_status",
            self._velocity_callback,
            1,
        )
        self.create_subscription(
            GearReport,
            "/vehicle/status/gear_status",
            self._gear_callback,
            1,
        )

        self._state = RecoveryState.NORMAL
        self._state_enter_time = self._now_sec()
        self._latest_nominal: Optional[AckermannControlCommand] = None
        self._latest_nominal_time: Optional[float] = None
        self._latest_velocity: Optional[float] = None
        self._latest_velocity_time: Optional[float] = None
        self._latest_gear: Optional[int] = None
        self._moving_observed = False
        self._stuck_start_time: Optional[float] = None
        self._attempt_count = 0

        self._timer = self.create_timer(1.0 / timer_hz, self._on_timer)
        self.get_logger().info(
            "recovery_supervisor started: "
            f"stuck_speed_threshold={self.stuck_speed_threshold} "
            f"stuck_duration={self.stuck_duration} "
            f"command_speed_threshold={self.command_speed_threshold} "
            f"command_accel_threshold={self.command_accel_threshold} "
            f"moving_speed_threshold={self.moving_speed_threshold} "
            f"reverse_speed={self.reverse_speed} "
            f"reverse_accel={self.reverse_accel} "
            f"reverse_duration={self.reverse_duration} "
            f"drive_settle_duration={self.drive_settle_duration} "
            f"cooldown_duration={self.cooldown_duration} "
            f"nominal_timeout_sec={self.nominal_timeout_sec} "
            f"velocity_timeout_sec={self.velocity_timeout_sec} "
            f"timer_hz={timer_hz}"
        )

    def _now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _nominal_callback(self, msg: AckermannControlCommand) -> None:
        self._latest_nominal = msg
        self._latest_nominal_time = self._now_sec()

    def _velocity_callback(self, msg: VelocityReport) -> None:
        self._latest_velocity = float(msg.longitudinal_velocity)
        self._latest_velocity_time = self._now_sec()

    def _gear_callback(self, msg: GearReport) -> None:
        self._latest_gear = int(msg.report)

    def _on_timer(self) -> None:
        now = self._now_sec()
        self._publish_state()

        if self._state == RecoveryState.NORMAL:
            self._run_normal(now)
        elif self._state == RecoveryState.STUCK_DETECTED:
            self._run_stuck_detected(now)
        elif self._state == RecoveryState.REVERSING:
            self._run_reversing(now)
        elif self._state == RecoveryState.DRIVE_SETTLE:
            self._run_drive_settle(now)
        elif self._state == RecoveryState.COOLDOWN:
            self._run_cooldown(now)

    def _run_normal(self, now: float) -> None:
        if self._has_fresh_nominal(now):
            self._publish_gear(GearCommand.DRIVE)
            self._publish_nominal()
        else:
            self._publish_gear(GearCommand.DRIVE)
            self._publish_command(0.0, 0.0, 0.0)
            self._stuck_start_time = None
            return

        if not self._has_fresh_velocity(now):
            self._stuck_start_time = None
            return

        velocity = self._latest_velocity
        if velocity is None:
            self._stuck_start_time = None
            return

        if velocity >= self.moving_speed_threshold:
            self._moving_observed = True

        if not self._moving_observed or not self._is_forward_request(self._latest_nominal):
            self._stuck_start_time = None
            return

        if abs(velocity) <= self.stuck_speed_threshold:
            if self._stuck_start_time is None:
                self._stuck_start_time = now
            elif now - self._stuck_start_time >= self.stuck_duration:
                self._start_recovery(now)
        else:
            self._stuck_start_time = None

    def _run_stuck_detected(self, now: float) -> None:
        self._publish_gear(GearCommand.DRIVE)
        self._publish_command(0.0, 0.0, 0.0)
        if now - self._state_enter_time >= 0.2:
            self._set_state(RecoveryState.REVERSING, now)

    def _run_reversing(self, now: float) -> None:
        self._publish_gear(GearCommand.REVERSE)
        # AWSIM expects a positive drive acceleration while the gear and target speed are reverse.
        self._publish_command(-abs(self.reverse_speed), abs(self.reverse_accel), 0.0)
        if now - self._state_enter_time >= self.reverse_duration:
            self._set_state(RecoveryState.DRIVE_SETTLE, now)

    def _run_drive_settle(self, now: float) -> None:
        self._publish_gear(GearCommand.DRIVE)
        self._publish_command(0.0, 0.0, 0.0)
        if now - self._state_enter_time >= self.drive_settle_duration:
            self._set_state(RecoveryState.COOLDOWN, now)

    def _run_cooldown(self, now: float) -> None:
        self._publish_gear(GearCommand.DRIVE)
        if self._has_fresh_nominal(now):
            self._publish_nominal()
        else:
            self._publish_command(0.0, 0.0, 0.0)

        if now - self._state_enter_time >= self.cooldown_duration:
            self._moving_observed = False
            self._stuck_start_time = None
            self._set_state(RecoveryState.NORMAL, now)

    def _start_recovery(self, now: float) -> None:
        self._attempt_count += 1
        self._stuck_start_time = None
        self.get_logger().warn(
            "stuck under throttle detected: "
            f"attempt={self._attempt_count} velocity={self._latest_velocity:.3f}"
        )
        self._set_state(RecoveryState.STUCK_DETECTED, now)

    def _set_state(self, state: RecoveryState, now: float) -> None:
        if state == self._state:
            return
        self.get_logger().info(f"state transition: {self._state.value} -> {state.value}")
        self._state = state
        self._state_enter_time = now
        self._publish_state()

    def _has_fresh_nominal(self, now: float) -> bool:
        return (
            self._latest_nominal is not None
            and self._latest_nominal_time is not None
            and now - self._latest_nominal_time <= self.nominal_timeout_sec
        )

    def _has_fresh_velocity(self, now: float) -> bool:
        return (
            self._latest_velocity is not None
            and self._latest_velocity_time is not None
            and now - self._latest_velocity_time <= self.velocity_timeout_sec
        )

    def _is_forward_request(self, cmd: Optional[AckermannControlCommand]) -> bool:
        if cmd is None:
            return False
        speed = float(cmd.longitudinal.speed)
        acceleration = float(cmd.longitudinal.acceleration)
        return (
            (math.isfinite(speed) and speed >= self.command_speed_threshold)
            or (
                math.isfinite(acceleration)
                and acceleration >= self.command_accel_threshold
                and self._latest_gear in (None, GearReport.DRIVE)
            )
        )

    def _publish_nominal(self) -> None:
        if self._latest_nominal is None:
            self._publish_command(0.0, 0.0, 0.0)
            return
        msg = copy.deepcopy(self._latest_nominal)
        stamp = self.get_clock().now().to_msg()
        msg.stamp = stamp
        msg.lateral.stamp = stamp
        msg.longitudinal.stamp = stamp
        self._control_pub.publish(msg)

    def _publish_command(self, speed: float, acceleration: float, steer: float) -> None:
        stamp = self.get_clock().now().to_msg()
        msg = AckermannControlCommand()
        msg.stamp = stamp
        msg.lateral.stamp = stamp
        msg.lateral.steering_tire_angle = float(steer)
        msg.lateral.steering_tire_rotation_rate = 2.0
        msg.longitudinal.stamp = stamp
        msg.longitudinal.speed = float(speed)
        msg.longitudinal.acceleration = float(acceleration)
        self._control_pub.publish(msg)

    def _publish_gear(self, command: int) -> None:
        msg = GearCommand()
        msg.stamp = self.get_clock().now().to_msg()
        msg.command = command
        self._gear_pub.publish(msg)

    def _publish_state(self) -> None:
        msg = String()
        msg.data = self._state.value
        self._state_pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RecoverySupervisor()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        if "context is not valid" not in str(exc):
            raise
    finally:
        try:
            node.destroy_node()
        finally:
            if rclpy.ok():
                rclpy.shutdown()


if __name__ == "__main__":
    main()
