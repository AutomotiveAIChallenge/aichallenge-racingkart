#!/usr/bin/env python3
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import rclpy
from autoware_auto_planning_msgs.msg import Trajectory
from geometry_msgs.msg import Point
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import ColorRGBA
from v2x_msgs.msg import V2XVehiclePosition, V2XVehiclePositionArray
from visualization_msgs.msg import Marker, MarkerArray


@dataclass
class DebugObstacle:
    x: float
    y: float
    vx: float
    vy: float
    yaw: float


class V2XSafetyDebugNode(Node):
    """Publish synthetic V2X obstacles for one-vehicle safety-filter debugging."""

    def __init__(self) -> None:
        super().__init__("v2x_safety_debug_node")
        self._declare_parameters()
        self._odom: Optional[Odometry] = None
        self._trajectory: Optional[Trajectory] = None
        self._anchored_obstacle: Optional[DebugObstacle] = None
        self._start_time_sec = self._now_sec()

        self.create_subscription(
            Odometry, str(self._p("odom_topic")), self._on_odom, 1
        )
        trajectory_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            Trajectory, str(self._p("trajectory_topic")), self._on_trajectory, trajectory_qos
        )
        self._v2x_pub = self.create_publisher(
            V2XVehiclePositionArray, str(self._p("v2x_topic")), 1
        )
        self._marker_pub = self.create_publisher(
            MarkerArray, str(self._p("marker_topic")), 1
        )

        publish_rate_hz = max(1.0, float(self._p("publish_rate_hz")))
        self._timer = self.create_timer(1.0 / publish_rate_hz, self._on_timer)
        self.get_logger().info(
            "v2x_safety_debug ready: "
            f"mode={self._p('mode')}, scenario={self._p('scenario')}, "
            f"v2x_topic={self._p('v2x_topic')}"
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("mode", "trajectory_relative")
        self.declare_parameter("scenario", "front_slowdown")
        self.declare_parameter("publish_rate_hz", 10.0)
        self.declare_parameter("vehicle_id", "debug_obstacle_1")
        self.declare_parameter("odom_topic", "/localization/kinematic_state")
        self.declare_parameter("trajectory_topic", "/planning/scenario_planning/trajectory")
        self.declare_parameter("v2x_topic", "/v2x/vehicle_positions")
        self.declare_parameter("marker_topic", "/debug/v2x_safety/markers")
        self.declare_parameter("frame_id", "map")
        self.declare_parameter("front_offset_m", 18.0)
        self.declare_parameter("lateral_offset_m", 0.0)
        self.declare_parameter("obstacle_speed_mps", 0.0)
        self.declare_parameter("anchor_ego_relative_obstacle", True)
        self.declare_parameter("map_x", 0.0)
        self.declare_parameter("map_y", 0.0)
        self.declare_parameter("map_yaw", 0.0)
        self.declare_parameter("crossing_front_offset_m", 12.0)
        self.declare_parameter("crossing_lateral_amplitude_m", 4.0)
        self.declare_parameter("crossing_period_s", 4.0)
        self.declare_parameter("covariance_stddev_m", 0.1)
        self.declare_parameter("marker_scale_m", 1.0)

    def _p(self, name: str):
        return self.get_parameter(name).value

    def _now_sec(self) -> float:
        return float(self.get_clock().now().nanoseconds) * 1.0e-9

    def _on_odom(self, msg: Odometry) -> None:
        self._odom = msg

    def _on_trajectory(self, msg: Trajectory) -> None:
        self._trajectory = msg

    def _on_timer(self) -> None:
        obstacle = self._make_obstacle()
        if obstacle is None:
            self._publish_empty_markers()
            return

        stamp = self.get_clock().now().to_msg()
        vehicle = V2XVehiclePosition()
        vehicle.header.stamp = stamp
        vehicle.header.frame_id = str(self._p("frame_id"))
        vehicle.vehicle_id = str(self._p("vehicle_id"))
        vehicle.position.x = obstacle.x
        vehicle.position.y = obstacle.y
        vehicle.position.z = 0.0
        stddev = float(self._p("covariance_stddev_m"))
        vehicle.covariance.x = stddev
        vehicle.covariance.y = stddev
        vehicle.covariance.z = stddev

        array = V2XVehiclePositionArray()
        array.header.stamp = stamp
        array.header.frame_id = str(self._p("frame_id"))
        array.vehicles.append(vehicle)
        self._v2x_pub.publish(array)
        self._publish_marker(obstacle, stamp)

    def _make_obstacle(self) -> Optional[DebugObstacle]:
        mode = str(self._p("mode"))
        scenario = str(self._p("scenario"))

        if scenario == "front_emergency":
            front_offset_m = 0.8
            lateral_offset_m = 0.0
            speed_mps = 0.0
        elif scenario == "lateral_clear":
            front_offset_m = 18.0
            lateral_offset_m = 3.0
            speed_mps = 0.0
        elif scenario == "crossing":
            return self._make_crossing_obstacle(mode)
        else:
            front_offset_m = float(self._p("front_offset_m"))
            lateral_offset_m = float(self._p("lateral_offset_m"))
            speed_mps = float(self._p("obstacle_speed_mps"))

        if mode == "map_static":
            return DebugObstacle(
                x=float(self._p("map_x")),
                y=float(self._p("map_y")),
                vx=speed_mps * math.cos(float(self._p("map_yaw"))),
                vy=speed_mps * math.sin(float(self._p("map_yaw"))),
                yaw=float(self._p("map_yaw")),
            )
        if mode == "trajectory_relative":
            return self._make_trajectory_relative_obstacle(
                front_offset_m, lateral_offset_m, speed_mps
            )
        return self._make_ego_relative_obstacle(front_offset_m, lateral_offset_m, speed_mps)

    def _make_crossing_obstacle(self, mode: str) -> Optional[DebugObstacle]:
        elapsed = self._now_sec() - self._start_time_sec
        period = max(0.1, float(self._p("crossing_period_s")))
        phase = 2.0 * math.pi * elapsed / period
        lateral = float(self._p("crossing_lateral_amplitude_m")) * math.sin(phase)
        lateral_speed = (
            float(self._p("crossing_lateral_amplitude_m"))
            * 2.0
            * math.pi
            * math.cos(phase)
            / period
        )
        front = float(self._p("crossing_front_offset_m"))
        if mode == "map_static":
            yaw = float(self._p("map_yaw"))
            x = float(self._p("map_x")) - lateral * math.sin(yaw)
            y = float(self._p("map_y")) + lateral * math.cos(yaw)
            vx = -lateral_speed * math.sin(yaw)
            vy = lateral_speed * math.cos(yaw)
            return DebugObstacle(x=x, y=y, vx=vx, vy=vy, yaw=yaw)
        if mode == "trajectory_relative":
            return self._make_trajectory_relative_obstacle(front, lateral, 0.0, lateral_speed, anchor=False)
        return self._make_ego_relative_obstacle(front, lateral, 0.0, lateral_speed, anchor=False)

    def _make_trajectory_relative_obstacle(
        self,
        front_offset_m: float,
        lateral_offset_m: float,
        forward_speed_mps: float,
        lateral_speed_mps: float = 0.0,
        anchor: Optional[bool] = None,
    ) -> Optional[DebugObstacle]:
        if anchor is None:
            anchor = bool(self._p("anchor_ego_relative_obstacle"))
        if anchor and self._anchored_obstacle is not None:
            return self._anchored_obstacle
        if self._odom is None:
            self.get_logger().warn(
                "waiting for odometry before publishing trajectory-relative V2X debug obstacle",
                throttle_duration_sec=2.0,
            )
            return None
        if self._trajectory is None or len(self._trajectory.points) < 2:
            self.get_logger().warn(
                "waiting for trajectory before publishing trajectory-relative V2X debug obstacle",
                throttle_duration_sec=2.0,
            )
            return None

        ego = self._odom.pose.pose.position
        points = self._trajectory.points
        cumulative = self._trajectory_cumulative_distance(points)
        total_length = cumulative[-1]
        nearest_index = min(
            range(len(points)),
            key=lambda i: math.hypot(
                float(points[i].pose.position.x) - float(ego.x),
                float(points[i].pose.position.y) - float(ego.y),
            ),
        )
        target_s = cumulative[nearest_index] + front_offset_m
        x, y, yaw = self._sample_trajectory(points, cumulative, target_s, total_length)
        lat_x = -math.sin(yaw)
        lat_y = math.cos(yaw)
        x += lateral_offset_m * lat_x
        y += lateral_offset_m * lat_y
        vx = forward_speed_mps * math.cos(yaw) + lateral_speed_mps * lat_x
        vy = forward_speed_mps * math.sin(yaw) + lateral_speed_mps * lat_y
        obstacle = DebugObstacle(x=x, y=y, vx=vx, vy=vy, yaw=yaw)
        if anchor:
            self._anchored_obstacle = obstacle
            self.get_logger().info(
                "anchored trajectory-relative V2X debug obstacle at "
                f"x={obstacle.x:.2f}, y={obstacle.y:.2f}, "
                f"front_offset={front_offset_m:.2f}, lateral_offset={lateral_offset_m:.2f}"
            )
        return obstacle

    @staticmethod
    def _trajectory_cumulative_distance(points) -> list:
        cumulative = [0.0]
        for i in range(1, len(points)):
            prev = points[i - 1].pose.position
            cur = points[i].pose.position
            cumulative.append(cumulative[-1] + math.hypot(cur.x - prev.x, cur.y - prev.y))
        return cumulative

    @staticmethod
    def _sample_trajectory(points, cumulative, target_s: float, total_length: float) -> Tuple[float, float, float]:
        if total_length <= 1.0e-6:
            point = points[0].pose.position
            return float(point.x), float(point.y), 0.0
        target_s = target_s % total_length
        upper = 1
        while upper < len(cumulative) and cumulative[upper] < target_s:
            upper += 1
        upper = min(upper, len(cumulative) - 1)
        lower = max(0, upper - 1)
        p0 = points[lower].pose.position
        p1 = points[upper].pose.position
        span = max(1.0e-6, cumulative[upper] - cumulative[lower])
        ratio = (target_s - cumulative[lower]) / span
        x = float(p0.x) + (float(p1.x) - float(p0.x)) * ratio
        y = float(p0.y) + (float(p1.y) - float(p0.y)) * ratio
        yaw = math.atan2(float(p1.y) - float(p0.y), float(p1.x) - float(p0.x))
        return x, y, yaw

    def _make_ego_relative_obstacle(
        self,
        front_offset_m: float,
        lateral_offset_m: float,
        forward_speed_mps: float,
        lateral_speed_mps: float = 0.0,
        anchor: Optional[bool] = None,
    ) -> Optional[DebugObstacle]:
        if anchor is None:
            anchor = bool(self._p("anchor_ego_relative_obstacle"))
        if anchor and self._anchored_obstacle is not None:
            return self._anchored_obstacle
        if self._odom is None:
            self.get_logger().warn(
                "waiting for odometry before publishing ego-relative V2X debug obstacle",
                throttle_duration_sec=2.0,
            )
            return None
        pose = self._odom.pose.pose
        yaw = self._yaw_from_quaternion(
            pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w
        )
        dir_x = math.cos(yaw)
        dir_y = math.sin(yaw)
        lat_x = -math.sin(yaw)
        lat_y = math.cos(yaw)
        x = float(pose.position.x) + front_offset_m * dir_x + lateral_offset_m * lat_x
        y = float(pose.position.y) + front_offset_m * dir_y + lateral_offset_m * lat_y
        vx = forward_speed_mps * dir_x + lateral_speed_mps * lat_x
        vy = forward_speed_mps * dir_y + lateral_speed_mps * lat_y
        obstacle = DebugObstacle(x=x, y=y, vx=vx, vy=vy, yaw=yaw)
        if anchor:
            self._anchored_obstacle = obstacle
            self.get_logger().info(
                "anchored V2X debug obstacle at "
                f"x={obstacle.x:.2f}, y={obstacle.y:.2f}, "
                f"front_offset={front_offset_m:.2f}, lateral_offset={lateral_offset_m:.2f}"
            )
        return obstacle

    def _publish_marker(self, obstacle: DebugObstacle, stamp) -> None:
        array = MarkerArray()
        array.markers.append(self._delete_marker(stamp))
        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = str(self._p("frame_id"))
        marker.ns = "v2x_safety_debug"
        marker.id = 1
        marker.type = Marker.SPHERE
        marker.action = Marker.ADD
        marker.pose.position = Point(x=obstacle.x, y=obstacle.y, z=0.5)
        scale = float(self._p("marker_scale_m"))
        marker.scale.x = scale
        marker.scale.y = scale
        marker.scale.z = scale
        marker.color = ColorRGBA(r=1.0, g=0.25, b=0.0, a=0.85)
        array.markers.append(marker)

        arrow = Marker()
        arrow.header.stamp = stamp
        arrow.header.frame_id = str(self._p("frame_id"))
        arrow.ns = "v2x_safety_debug"
        arrow.id = 2
        arrow.type = Marker.ARROW
        arrow.action = Marker.ADD
        arrow.pose.position = Point(x=obstacle.x, y=obstacle.y, z=0.8)
        arrow.pose.orientation.z = math.sin(obstacle.yaw * 0.5)
        arrow.pose.orientation.w = math.cos(obstacle.yaw * 0.5)
        arrow.scale.x = max(1.0, math.hypot(obstacle.vx, obstacle.vy))
        arrow.scale.y = 0.18
        arrow.scale.z = 0.18
        arrow.color = ColorRGBA(r=1.0, g=1.0, b=0.0, a=0.85)
        array.markers.append(arrow)

        self._marker_pub.publish(array)

    def _publish_empty_markers(self) -> None:
        self._marker_pub.publish(MarkerArray(markers=[self._delete_marker(self.get_clock().now().to_msg())]))

    def _delete_marker(self, stamp) -> Marker:
        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = str(self._p("frame_id"))
        marker.ns = "v2x_safety_debug"
        marker.id = 0
        marker.action = Marker.DELETEALL
        return marker

    @staticmethod
    def _yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        return math.atan2(siny_cosp, cosy_cosp)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = V2XSafetyDebugNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
