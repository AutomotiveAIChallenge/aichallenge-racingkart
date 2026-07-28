# Copyright 2026 aichallenge
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Longitudinal collision guard.

Sits between the controller and the vehicle. It republishes the incoming
AckermannControlCommand unchanged unless another kart (from V2X) or a wall
(from LaserScan) is close ahead in the ego travel corridor, in which case it
caps the commanded speed (so the ego can always stop before the obstacle) and,
in the worst case, commands an emergency brake. The steering command is always
passed through untouched, so this does not fight the main controller's line.
"""

import math

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from autoware_auto_control_msgs.msg import AckermannControlCommand
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from v2x_msgs.msg import V2XVehiclePositionArray


def yaw_from_quaternion(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


class CollisionGuard(Node):
    def __init__(self) -> None:
        super().__init__("collision_guard")

        # --- parameters ---
        self._use_v2x = self.declare_parameter("use_v2x_guard", True).value
        self._use_scan = self.declare_parameter("use_scan_guard", True).value

        # Comfortable deceleration used to compute the safe speed (v^2 = 2*a*d).
        self._brake_decel = float(self.declare_parameter("brake_decel", 2.0).value)
        # Deceleration commanded on an emergency stop (system clamps to vehicle limit).
        self._emergency_decel = float(self.declare_parameter("emergency_decel", 3.0).value)

        # Geometry of the "ahead" corridor.
        self._corridor_half_width = float(self.declare_parameter("corridor_half_width", 1.2).value)
        self._other_vehicle_radius = float(self.declare_parameter("other_vehicle_radius", 1.0).value)
        self._ego_front_offset = float(self.declare_parameter("ego_front_offset", 0.5).value)
        # Desired standing gap to the obstacle (target speed reaches 0 at this gap).
        self._standstill_gap = float(self.declare_parameter("standstill_gap", 3.0).value)
        # Below this clear distance -> emergency stop.
        self._emergency_gap = float(self.declare_parameter("emergency_gap", 1.5).value)

        # V2X handling.
        self._v2x_max_range = float(self.declare_parameter("v2x_max_range", 25.0).value)
        self._self_ignore_radius = float(self.declare_parameter("self_ignore_radius", 1.5).value)
        self._sensor_stale_sec = float(self.declare_parameter("sensor_stale_sec", 0.5).value)

        # Wall (scan) handling: last-resort AEB only, so it does not false-trigger
        # on corner walls during normal cornering. Narrow, dead-ahead, short range.
        self._scan_cone_half_deg = float(self.declare_parameter("scan_cone_half_deg", 6.0).value)
        self._scan_trigger_distance = float(self.declare_parameter("scan_trigger_distance", 3.0).value)
        self._scan_min_range = float(self.declare_parameter("scan_min_range", 0.05).value)

        # --- state ---
        self._odom = None
        self._v2x = None
        self._scan = None

        # --- io ---
        reliable = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                              history=HistoryPolicy.KEEP_LAST)
        best_effort = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                                 history=HistoryPolicy.KEEP_LAST)

        self._pub = self.create_publisher(AckermannControlCommand, "~/output/control_cmd", 1)
        self.create_subscription(AckermannControlCommand, "~/input/control_cmd",
                                 self._on_cmd, 1)
        self.create_subscription(Odometry, "/localization/kinematic_state",
                                 self._on_odom, best_effort)
        if self._use_v2x:
            self.create_subscription(V2XVehiclePositionArray, "/v2x/vehicle_positions",
                                     self._on_v2x, reliable)
        if self._use_scan:
            self.create_subscription(LaserScan, "/scan", self._on_scan, best_effort)

        self.get_logger().info(
            f"collision_guard up (v2x={self._use_v2x}, scan={self._use_scan}, "
            f"brake_decel={self._brake_decel}, standstill_gap={self._standstill_gap})")

    # --- subscription callbacks ---
    def _on_odom(self, msg: Odometry) -> None:
        self._odom = msg

    def _on_v2x(self, msg: V2XVehiclePositionArray) -> None:
        self._v2x = msg

    def _on_scan(self, msg: LaserScan) -> None:
        self._scan = msg

    def _is_fresh(self, msg) -> bool:
        if msg is None:
            return False
        try:
            stamp = msg.header.stamp
        except AttributeError:
            return True
        msg_t = stamp.sec + stamp.nanosec * 1e-9
        now_t = self.get_clock().now().nanoseconds * 1e-9
        # stamp of 0 means "not stamped"; accept it rather than dropping.
        if msg_t <= 0.0:
            return True
        return (now_t - msg_t) <= self._sensor_stale_sec

    # --- core ---
    def _forward_clearance(self, ego_x, ego_y, yaw):
        """Return (min clear distance ahead in the corridor, emergency_flag)."""
        d_min = math.inf
        emergency = False

        hx, hy = math.cos(yaw), math.sin(yaw)  # heading unit vector

        # --- other karts via V2X ---
        if self._use_v2x and self._is_fresh(self._v2x):
            for veh in self._v2x.vehicles:
                rx = veh.position.x - ego_x
                ry = veh.position.y - ego_y
                if math.hypot(rx, ry) < self._self_ignore_radius:
                    continue  # this is (approximately) us
                lon = rx * hx + ry * hy            # forward distance
                lat = -rx * hy + ry * hx           # left distance
                if lon <= 0.0 or lon > self._v2x_max_range:
                    continue
                if abs(lat) > self._corridor_half_width + self._other_vehicle_radius:
                    continue
                clear = lon - self._other_vehicle_radius - self._ego_front_offset
                d_min = min(d_min, clear)

        # --- walls via scan (last-resort, dead-ahead, short range) ---
        if self._use_scan and self._is_fresh(self._scan):
            scan = self._scan
            cone = math.radians(self._scan_cone_half_deg)
            n = len(scan.ranges)
            for i in range(n):
                angle = scan.angle_min + i * scan.angle_increment
                if abs(angle) > cone:
                    continue
                r = scan.ranges[i]
                if not math.isfinite(r) or r < self._scan_min_range:
                    continue
                clear = r - self._ego_front_offset
                if clear < self._scan_trigger_distance:
                    d_min = min(d_min, clear)
                    if clear < self._emergency_gap:
                        emergency = True

        return d_min, emergency

    def _on_cmd(self, msg: AckermannControlCommand) -> None:
        out = msg  # steering (lateral) is passed through unchanged

        if self._odom is None:
            self._pub.publish(out)
            return

        p = self._odom.pose.pose
        ego_x, ego_y = p.position.x, p.position.y
        yaw = yaw_from_quaternion(p.orientation)

        d_min, emergency = self._forward_clearance(ego_x, ego_y, yaw)

        if math.isinf(d_min):
            self._pub.publish(out)  # nothing ahead -> transparent
            return

        if emergency or d_min <= self._emergency_gap:
            out.longitudinal.speed = 0.0
            out.longitudinal.acceleration = -abs(self._emergency_decel)
            self.get_logger().warn(
                f"EMERGENCY BRAKE: obstacle {d_min:.2f} m ahead", throttle_duration_sec=1.0)
            self._pub.publish(out)
            return

        # Safe speed so the ego can stop within (clearance - standstill_gap).
        eff = max(d_min - self._standstill_gap, 0.0)
        v_safe = math.sqrt(2.0 * self._brake_decel * eff)

        if msg.longitudinal.speed > v_safe:
            out.longitudinal.speed = v_safe
            out.longitudinal.acceleration = -abs(self._brake_decel)
            self.get_logger().info(
                f"slow: cap {v_safe:.2f} m/s (obstacle {d_min:.2f} m ahead)",
                throttle_duration_sec=1.0)

        self._pub.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = CollisionGuard()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
