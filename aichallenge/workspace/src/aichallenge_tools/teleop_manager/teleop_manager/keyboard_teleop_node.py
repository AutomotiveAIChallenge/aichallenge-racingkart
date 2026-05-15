#!/usr/bin/env python3
"""Keyboard teleop node.

Reads non-blocking keystrokes from stdin (termios + select) and publishes
AckermannControlCommand on /awsim/control_cmd, mirroring the joystick variant
in teleop_manager_node.cpp. Also exposes the same helper commands (rosbag
trigger, AWSIM control-mode request, initial-pose reset).

For interactive use, run from a docker exec shell, e.g.

    docker exec -it autoware-d1 bash
    source /aichallenge/workspace/install/setup.bash
    ros2 run teleop_manager keyboard_teleop_node.py
"""

import os
import select
import sys
import termios
import tty
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter

from autoware_auto_control_msgs.msg import AckermannControlCommand
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import Bool, Empty


HELP_TEXT = """
==== keyboard teleop ====
  w / s : accelerate / brake (longitudinal acceleration)
  a / d : steer left / right
  space : emergency stop (zero everything)
  +/-   : speed_scale  +/- 0.1
  [ / ] : steer_scale  +/- 0.1
  r     : reset initial pose + reset AWSIM
  t     : rosbag trigger ON
  y     : rosbag trigger OFF
  m     : AWSIM start (control_mode_request)
  h     : show this help
  q     : quit
=========================
"""


class KeyboardTeleopNode(Node):
    def __init__(self) -> None:
        super().__init__("keyboard_teleop_node")
        self.set_parameters([Parameter("use_sim_time", value=True)])

        self.declare_parameter("speed_scale", 1.0)
        self.declare_parameter("steer_scale", 1.0)
        self.declare_parameter("accel_step", 0.3)
        self.declare_parameter("steer_step", 0.1)
        self.declare_parameter("max_accel", 2.0)
        self.declare_parameter("max_steer", 0.6)
        self.declare_parameter("timer_hz", 40.0)
        self.declare_parameter("key_timeout_sec", 0.3)
        self.declare_parameter("decay_per_sec", 4.0)
        self.declare_parameter("reset_frame_id", "map")
        self.declare_parameter("reset_pos_x", 89666.0)
        self.declare_parameter("reset_pos_y", 43124.0)
        self.declare_parameter("reset_pos_z", 0.0)
        self.declare_parameter("reset_ori_x", 0.0)
        self.declare_parameter("reset_ori_y", 0.0)
        self.declare_parameter("reset_ori_z", -0.968393)
        self.declare_parameter("reset_ori_w", 0.249429)

        self.speed_scale = float(self.get_parameter("speed_scale").value)
        self.steer_scale = float(self.get_parameter("steer_scale").value)
        self.accel_step = float(self.get_parameter("accel_step").value)
        self.steer_step = float(self.get_parameter("steer_step").value)
        self.max_accel = float(self.get_parameter("max_accel").value)
        self.max_steer = float(self.get_parameter("max_steer").value)
        self.timer_hz = float(self.get_parameter("timer_hz").value)
        self.key_timeout_sec = float(self.get_parameter("key_timeout_sec").value)
        self.decay_per_sec = float(self.get_parameter("decay_per_sec").value)

        self.cmd_pub = self.create_publisher(
            AckermannControlCommand, "/awsim/control_cmd", 10
        )
        self.trigger_pub = self.create_publisher(Bool, "/rosbag2_recorder/trigger", 10)
        self.awsim_trigger_pub = self.create_publisher(
            Bool, "/awsim/control_mode_request_topic", 10
        )
        self.reset_pub = self.create_publisher(Empty, "/admin/awsim/reset", 10)
        self.initialpose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 10
        )

        self.current_accel = 0.0
        self.current_steer = 0.0
        self.last_key_time = self.get_clock().now()
        self.last_tick_time = self.get_clock().now()

        self._stdin_fd = sys.stdin.fileno()
        self._termios_old: Optional[list] = None
        self._tty_ready = False
        if os.isatty(self._stdin_fd):
            self._termios_old = termios.tcgetattr(self._stdin_fd)
            tty.setcbreak(self._stdin_fd)
            self._tty_ready = True
            self.get_logger().info("Keyboard ready. Press 'h' for help.")
            sys.stdout.write(HELP_TEXT)
            sys.stdout.flush()
        else:
            self.get_logger().warn(
                "stdin is not a TTY; keyboard input disabled. "
                "Re-run from `docker exec -it` shell via `ros2 run teleop_manager keyboard_teleop_node.py`."
            )

        period = 1.0 / self.timer_hz
        self.timer = self.create_timer(period, self._on_timer)

    def destroy_node(self) -> bool:
        self._restore_terminal()
        return super().destroy_node()

    def _restore_terminal(self) -> None:
        if self._tty_ready and self._termios_old is not None:
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._termios_old)
            self._tty_ready = False

    def _read_key(self) -> Optional[str]:
        if not self._tty_ready:
            return None
        rlist, _, _ = select.select([self._stdin_fd], [], [], 0.0)
        if not rlist:
            return None
        return os.read(self._stdin_fd, 1).decode("utf-8", errors="replace")

    def _handle_key(self, key: str) -> bool:
        self.last_key_time = self.get_clock().now()
        if key == "w":
            self.current_accel = min(self.current_accel + self.accel_step, self.max_accel)
        elif key == "s":
            self.current_accel = max(self.current_accel - self.accel_step, -self.max_accel)
        elif key == "a":
            self.current_steer = min(self.current_steer + self.steer_step, self.max_steer)
        elif key == "d":
            self.current_steer = max(self.current_steer - self.steer_step, -self.max_steer)
        elif key == " ":
            self.current_accel = 0.0
            self.current_steer = 0.0
            self.get_logger().info("Emergency stop.")
        elif key == "+":
            self.speed_scale = round(self.speed_scale + 0.1, 1)
            self.get_logger().info(f"speed_scale = {self.speed_scale:.1f}")
        elif key == "-":
            self.speed_scale = round(max(self.speed_scale - 0.1, 0.0), 1)
            self.get_logger().info(f"speed_scale = {self.speed_scale:.1f}")
        elif key == "]":
            self.steer_scale = round(self.steer_scale + 0.1, 1)
            self.get_logger().info(f"steer_scale = {self.steer_scale:.1f}")
        elif key == "[":
            self.steer_scale = round(max(self.steer_scale - 0.1, 0.0), 1)
            self.get_logger().info(f"steer_scale = {self.steer_scale:.1f}")
        elif key == "t":
            self.trigger_pub.publish(Bool(data=True))
            self.get_logger().info("rosbag trigger ON")
        elif key == "y":
            self.trigger_pub.publish(Bool(data=False))
            self.get_logger().info("rosbag trigger OFF")
        elif key == "m":
            self.awsim_trigger_pub.publish(Bool(data=True))
            self.get_logger().info("AWSIM control mode requested")
        elif key == "r":
            self._publish_reset()
        elif key == "h":
            sys.stdout.write(HELP_TEXT)
            sys.stdout.flush()
        elif key == "q":
            return False
        return True

    def _publish_reset(self) -> None:
        self.reset_pub.publish(Empty())
        pose = PoseWithCovarianceStamped()
        pose.header.frame_id = self.get_parameter("reset_frame_id").value
        pose.pose.pose.position.x = float(self.get_parameter("reset_pos_x").value)
        pose.pose.pose.position.y = float(self.get_parameter("reset_pos_y").value)
        pose.pose.pose.position.z = float(self.get_parameter("reset_pos_z").value)
        pose.pose.pose.orientation.x = float(self.get_parameter("reset_ori_x").value)
        pose.pose.pose.orientation.y = float(self.get_parameter("reset_ori_y").value)
        pose.pose.pose.orientation.z = float(self.get_parameter("reset_ori_z").value)
        pose.pose.pose.orientation.w = float(self.get_parameter("reset_ori_w").value)
        self.initialpose_pub.publish(pose)
        self.get_logger().info("Reset published.")

    def _on_timer(self) -> None:
        keep_running = True
        while True:
            key = self._read_key()
            if key is None:
                break
            if not self._handle_key(key):
                keep_running = False

        if not keep_running:
            self.get_logger().info("'q' pressed - shutting down")
            self._restore_terminal()
            rclpy.shutdown()
            return

        now = self.get_clock().now()
        dt = (now - self.last_tick_time).nanoseconds * 1e-9
        self.last_tick_time = now

        idle = (now - self.last_key_time).nanoseconds * 1e-9
        if idle > self.key_timeout_sec:
            decay = self.decay_per_sec * dt
            if self.current_accel > 0:
                self.current_accel = max(self.current_accel - decay, 0.0)
            elif self.current_accel < 0:
                self.current_accel = min(self.current_accel + decay, 0.0)
            if self.current_steer > 0:
                self.current_steer = max(self.current_steer - decay, 0.0)
            elif self.current_steer < 0:
                self.current_steer = min(self.current_steer + decay, 0.0)

        out = AckermannControlCommand()
        out.stamp = now.to_msg()
        out.longitudinal.stamp = out.stamp
        out.lateral.stamp = out.stamp
        out.longitudinal.acceleration = float(self.current_accel * self.speed_scale)
        out.longitudinal.speed = 0.0
        out.lateral.steering_tire_angle = float(self.current_steer * self.steer_scale)
        out.lateral.steering_tire_rotation_rate = 1.0
        self.cmd_pub.publish(out)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = KeyboardTeleopNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node._restore_terminal()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
