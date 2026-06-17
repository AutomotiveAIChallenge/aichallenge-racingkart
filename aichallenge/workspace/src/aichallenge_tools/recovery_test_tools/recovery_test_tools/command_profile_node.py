#!/usr/bin/env python3

import math
import sys
import time
from typing import Optional

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node

from autoware_auto_control_msgs.msg import AckermannControlCommand
from autoware_auto_vehicle_msgs.msg import GearCommand, VelocityReport


class CommandProfileNode(Node):
    """Publish deterministic command profiles for recovery development."""

    def __init__(self) -> None:
        super().__init__("recovery_command_profile")

        self.declare_parameter("profile", "stuck_repro")
        self.declare_parameter("initial_delay_sec", 2.0)
        self.declare_parameter("max_duration_sec", 35.0)
        self.declare_parameter("post_event_observe_sec", 3.0)
        self.declare_parameter("output_control_topic", "/control/command/control_cmd")
        self.declare_parameter("publish_gear_cmd", True)
        self.declare_parameter("stop_on_stuck", True)
        self.declare_parameter("success_on_timeout", False)
        self.declare_parameter("moving_speed_mps", 0.5)
        self.declare_parameter("stuck_speed_mps", 0.2)
        self.declare_parameter("stuck_duration_sec", 1.5)

        self.declare_parameter("stuck_repro_speed_mps", 5.0)
        self.declare_parameter("stuck_repro_accel_mps2", 1.0)
        self.declare_parameter("stuck_repro_steer_rad", 0.55)
        self.declare_parameter("stuck_repro_phase_sec", 5.0)

        self.declare_parameter("reverse_speed_mps", -1.0)
        self.declare_parameter("reverse_accel_mps2", 1.0)
        self.declare_parameter("reverse_duration_sec", 3.0)

        self.profile = str(self.get_parameter("profile").value)
        if self.profile == "wall_hit":
            self.get_logger().warn("profile 'wall_hit' is deprecated; using 'stuck_repro'")
            self.profile = "stuck_repro"
        self.initial_delay_sec = float(self.get_parameter("initial_delay_sec").value)
        self.max_duration_sec = float(self.get_parameter("max_duration_sec").value)
        self.post_event_observe_sec = float(self.get_parameter("post_event_observe_sec").value)
        self.output_control_topic = str(self.get_parameter("output_control_topic").value)
        self.publish_gear_cmd = bool(self.get_parameter("publish_gear_cmd").value)
        self.stop_on_stuck = bool(self.get_parameter("stop_on_stuck").value)
        self.success_on_timeout = bool(self.get_parameter("success_on_timeout").value)
        self.moving_speed_mps = float(self.get_parameter("moving_speed_mps").value)
        self.stuck_speed_mps = float(self.get_parameter("stuck_speed_mps").value)
        self.stuck_duration_sec = float(self.get_parameter("stuck_duration_sec").value)

        self.stuck_repro_speed_mps = float(self.get_parameter("stuck_repro_speed_mps").value)
        self.stuck_repro_accel_mps2 = float(self.get_parameter("stuck_repro_accel_mps2").value)
        self.stuck_repro_steer_rad = float(self.get_parameter("stuck_repro_steer_rad").value)
        self.stuck_repro_phase_sec = max(
            0.1,
            float(self.get_parameter("stuck_repro_phase_sec").value),
        )

        self.reverse_speed_mps = float(self.get_parameter("reverse_speed_mps").value)
        self.reverse_accel_mps2 = float(self.get_parameter("reverse_accel_mps2").value)
        self.reverse_duration_sec = float(self.get_parameter("reverse_duration_sec").value)

        if self.profile not in ("stuck_repro", "reverse_smoke"):
            raise ValueError(f"unsupported profile: {self.profile}")

        self._pub = self.create_publisher(AckermannControlCommand, self.output_control_topic, 1)
        self._gear_pub = self.create_publisher(GearCommand, "/control/command/gear_cmd", 1)
        self.create_subscription(VelocityReport, "/vehicle/status/velocity_status", self._velocity_callback, 1)

        self._start_wall_time = time.monotonic()
        self._stuck_detected_wall_time: Optional[float] = None
        self._last_velocity: Optional[float] = None
        self._moving_observed = False
        self._stuck_wall_time: Optional[float] = None
        self.done = False
        self.success = False
        self._reverse_logged = False
        self._stuck_repro_logged = False

        self._timer = self.create_timer(
            0.05,
            self._on_timer,
            clock=Clock(clock_type=ClockType.STEADY_TIME),
        )
        self.get_logger().info(
            f"starting recovery command profile: {self.profile} "
            f"output_control_topic={self.output_control_topic} "
            f"publish_gear_cmd={self.publish_gear_cmd} stop_on_stuck={self.stop_on_stuck}"
        )

    def _velocity_callback(self, msg: VelocityReport) -> None:
        self._last_velocity = float(msg.longitudinal_velocity)

    def _make_command(self, speed: float, acceleration: float, steer: float) -> AckermannControlCommand:
        stamp = self.get_clock().now().to_msg()
        cmd = AckermannControlCommand()
        cmd.stamp = stamp
        cmd.lateral.stamp = stamp
        cmd.lateral.steering_tire_angle = float(steer)
        cmd.lateral.steering_tire_rotation_rate = 2.0
        cmd.longitudinal.stamp = stamp
        cmd.longitudinal.speed = float(speed)
        cmd.longitudinal.acceleration = float(acceleration)
        return cmd

    def _publish_zero(self) -> None:
        self._pub.publish(self._make_command(0.0, 0.0, 0.0))

    def _publish_gear(self, command: int) -> None:
        if not self.publish_gear_cmd:
            return
        msg = GearCommand()
        msg.stamp = self.get_clock().now().to_msg()
        msg.command = command
        self._gear_pub.publish(msg)

    def _finish(self, success: bool, reason: str) -> None:
        self.success = success
        self.done = True
        self._publish_zero()
        self._publish_gear(GearCommand.DRIVE)
        log = self.get_logger().info if success else self.get_logger().error
        log(f"profile finished: success={success} reason={reason}")

    def _on_timer(self) -> None:
        now = time.monotonic()
        elapsed = now - self._start_wall_time

        if elapsed < self.initial_delay_sec:
            self._publish_zero()
            return

        if elapsed >= self.max_duration_sec:
            reason = "max_duration_completed" if self.success_on_timeout else "timeout"
            self._finish(self.success_on_timeout, reason)
            return

        if self.profile == "stuck_repro":
            self._run_stuck_repro(now, elapsed - self.initial_delay_sec)
            return

        if self.profile == "reverse_smoke":
            self._run_reverse_smoke(now, elapsed - self.initial_delay_sec)
            return

        self._finish(False, f"unsupported profile: {self.profile}")

    def _run_stuck_repro(self, now: float, active_elapsed: float) -> None:
        if self._stuck_detected_wall_time is not None and self.stop_on_stuck:
            self._publish_zero()
            if now - self._stuck_detected_wall_time >= self.post_event_observe_sec:
                self._finish(True, "stuck_under_throttle_observed")
            return

        if self._last_velocity is not None and self._stuck_detected_wall_time is None:
            if self._last_velocity >= self.moving_speed_mps:
                self._moving_observed = True
                self._stuck_wall_time = None
            elif self._moving_observed and abs(self._last_velocity) <= self.stuck_speed_mps:
                if self._stuck_wall_time is None:
                    self._stuck_wall_time = now
                elif now - self._stuck_wall_time >= self.stuck_duration_sec:
                    self._stuck_detected_wall_time = now
                    self.get_logger().warn(
                        "stuck under throttle detected after motion: "
                        f"velocity={self._last_velocity:.3f}"
                    )
                    if self.stop_on_stuck:
                        self._publish_zero()
                        return
            else:
                self._stuck_wall_time = None

        phase = int(active_elapsed / self.stuck_repro_phase_sec)
        self._publish_gear(GearCommand.DRIVE)
        steer_sign = 1.0 if phase % 2 == 0 else -1.0
        steer = steer_sign * self.stuck_repro_steer_rad
        if not math.isfinite(steer):
            steer = 0.0
        if not self._stuck_repro_logged:
            self._stuck_repro_logged = True
            self.get_logger().info(
                "stuck_repro active: "
                f"speed={self.stuck_repro_speed_mps} accel={self.stuck_repro_accel_mps2}"
            )
        self._pub.publish(
            self._make_command(
                self.stuck_repro_speed_mps,
                self.stuck_repro_accel_mps2,
                steer,
            )
        )

    def _run_reverse_smoke(self, now: float, active_elapsed: float) -> None:
        if active_elapsed < self.reverse_duration_sec:
            if not self._reverse_logged:
                self._reverse_logged = True
                self.get_logger().info(
                    f"reverse active: speed={self.reverse_speed_mps} accel={self.reverse_accel_mps2}"
                )
            self._publish_gear(GearCommand.REVERSE)
            self._pub.publish(
                self._make_command(self.reverse_speed_mps, self.reverse_accel_mps2, 0.0)
            )
            return

        self._publish_zero()
        self._publish_gear(GearCommand.DRIVE)
        if active_elapsed >= self.reverse_duration_sec + self.post_event_observe_sec:
            self._finish(True, "reverse_command_published")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CommandProfileNode()
    try:
        while rclpy.ok() and not node.done:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        node.success = False
    finally:
        for _ in range(5):
            node._publish_zero()
            rclpy.spin_once(node, timeout_sec=0.02)
        success = node.success
        node.destroy_node()
        rclpy.shutdown()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
