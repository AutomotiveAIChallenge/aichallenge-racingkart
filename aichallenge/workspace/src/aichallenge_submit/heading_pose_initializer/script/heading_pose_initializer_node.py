#!/usr/bin/env python3
"""Headless /set_initial_pose service node with raceline visualization.

Logic:
  1. Load a heading-reference CSV at startup as the reference path.
  2. Publish the raceline as RViz markers (LineStrip + heading arrows).
  3. Subscribe to GNSS pose.
  4. On /set_initial_pose service call, find the closest raceline point
     to the current GNSS position, compute yaw, and publish /initialpose.
"""

from __future__ import annotations

import csv
import math
import threading
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from geometry_msgs.msg import PoseWithCovarianceStamped
from std_srvs.srv import Trigger
from visualization_msgs.msg import Marker, MarkerArray


class _Point:
    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class HeadingPoseInitializerNode(Node):
    def __init__(self) -> None:
        super().__init__("heading_pose_initializer")

        self.declare_parameter("heading_csv_path", "")
        self.declare_parameter("gnss_pose_topic", "/sensing/gnss/pose_with_covariance")
        self.declare_parameter("initial_pose_topic", "/initialpose")
        self.declare_parameter("service_name", "/set_initial_pose")
        self.declare_parameter("wait_timeout_sec", 120)
        self.declare_parameter("marker_topic", "/heading_pose_initializer/raceline_markers")
        self.declare_parameter("marker_publish_rate", 1.0)
        self.declare_parameter("arrow_interval", 2)
        self.declare_parameter("arrow_length", 1.0)

        csv_path = str(self.get_parameter("heading_csv_path").value)
        gnss_topic = str(self.get_parameter("gnss_pose_topic").value)
        pose_topic = str(self.get_parameter("initial_pose_topic").value)
        service_name = str(self.get_parameter("service_name").value)
        marker_topic = str(self.get_parameter("marker_topic").value)
        marker_rate = float(self.get_parameter("marker_publish_rate").value)

        self._arrow_interval = int(self.get_parameter("arrow_interval").value)
        self._arrow_length = float(self.get_parameter("arrow_length").value)

        self._raceline_points = self._load_raceline(csv_path)
        self.get_logger().info(
            f"Loaded {len(self._raceline_points)} heading-reference points from {csv_path}"
        )

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._last_gnss: Optional[PoseWithCovarianceStamped] = None

        reliable_volatile = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

        self._gnss_sub = self.create_subscription(
            PoseWithCovarianceStamped, gnss_topic, self._on_gnss, reliable_volatile
        )
        self._pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, pose_topic, reliable_volatile
        )
        self._marker_pub = self.create_publisher(
            MarkerArray, marker_topic, QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        )
        self._service = self.create_service(Trigger, service_name, self._on_service)

        self._marker_array = self._build_markers()
        if marker_rate > 0.0:
            self.create_timer(1.0 / marker_rate, self._publish_markers)

        self.get_logger().info(
            f"heading_pose_initializer ready: gnss={gnss_topic} "
            f"pub={pose_topic} srv={service_name} markers={marker_topic}"
        )

    # ── CSV loading ──────────────────────────────────────────────

    def _load_raceline(self, csv_path: str) -> list[_Point]:
        points: list[_Point] = []
        try:
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    x = float(row["x"])
                    y = float(row["y"])
                    if math.isfinite(x) and math.isfinite(y):
                        points.append(_Point(x, y))
        except Exception as e:
            self.get_logger().error(f"Failed to load heading CSV: {e}")
        return points

    # ── Marker building ──────────────────────────────────────────

    def _build_markers(self) -> MarkerArray:
        ma = MarkerArray()
        pts = self._raceline_points
        if len(pts) < 2:
            return ma

        now = self.get_clock().now().to_msg()

        # ARROWs: heading direction at intervals
        arrow_id = 0
        for i in range(0, len(pts) - 1, self._arrow_interval):
            yaw = self._compute_yaw(pts, i)
            if yaw is None:
                continue

            arrow = Marker()
            arrow.header.frame_id = "map"
            arrow.header.stamp = now
            arrow.ns = "heading_arrows"
            arrow.id = arrow_id
            arrow_id += 1
            arrow.type = Marker.ARROW
            arrow.action = Marker.ADD

            from geometry_msgs.msg import Point as GPoint
            start = GPoint()
            start.x = pts[i].x
            start.y = pts[i].y
            start.z = 0.5
            end = GPoint()
            end.x = pts[i].x + self._arrow_length * math.cos(yaw)
            end.y = pts[i].y + self._arrow_length * math.sin(yaw)
            end.z = 0.5
            arrow.points.append(start)
            arrow.points.append(end)

            arrow.scale.x = 0.25  # shaft diameter
            arrow.scale.y = 0.3  # head diameter
            arrow.scale.z = 0.2  # head length
            arrow.color.r = 1.0
            arrow.color.g = 1.0
            arrow.color.b = 1.0
            arrow.color.a = 0.5
            ma.markers.append(arrow)

        return ma

    def _publish_markers(self) -> None:
        self._marker_pub.publish(self._marker_array)

    # ── GNSS callback ────────────────────────────────────────────

    def _on_gnss(self, msg: PoseWithCovarianceStamped) -> None:
        with self._cv:
            self._last_gnss = msg
            self._cv.notify_all()

    # ── Service handler ──────────────────────────────────────────

    def _on_service(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        if len(self._raceline_points) < 2:
            response.success = False
            response.message = "heading CSV not loaded or has fewer than 2 points"
            self.get_logger().error(response.message)
            return response

        timeout = int(self.get_parameter("wait_timeout_sec").value)
        self.get_logger().info(f"set_initial_pose called, waiting up to {timeout}s for GNSS")

        with self._cv:
            self._cv.wait_for(
                lambda: self._last_gnss is not None,
                timeout=float(timeout),
            )
            gnss = self._last_gnss

        if gnss is None:
            response.success = False
            response.message = "timeout waiting for GNSS"
            self.get_logger().error(response.message)
            return response

        gnss_pt = gnss.pose.pose.position
        if not (math.isfinite(gnss_pt.x) and math.isfinite(gnss_pt.y)):
            response.success = False
            response.message = "GNSS pose is invalid (NaN/Inf)"
            self.get_logger().error(response.message)
            return response

        closest_idx = 0
        closest_d2 = float("inf")
        for i, pt in enumerate(self._raceline_points):
            d2 = (pt.x - gnss_pt.x) ** 2 + (pt.y - gnss_pt.y) ** 2
            if d2 < closest_d2:
                closest_d2 = d2
                closest_idx = i

        yaw = self._compute_yaw(self._raceline_points, closest_idx)
        if yaw is None:
            response.success = False
            response.message = "cannot compute yaw from heading reference"
            self.get_logger().error(response.message)
            return response

        pose_msg = PoseWithCovarianceStamped()
        pose_msg.header.stamp = self.get_clock().now().to_msg()
        pose_msg.header.frame_id = gnss.header.frame_id
        pose_msg.pose.pose.position = gnss.pose.pose.position
        pose_msg.pose.pose.orientation.x = 0.0
        pose_msg.pose.pose.orientation.y = 0.0
        pose_msg.pose.pose.orientation.z = math.sin(yaw * 0.5)
        pose_msg.pose.pose.orientation.w = math.cos(yaw * 0.5)
        pose_msg.pose.covariance[35] = 0.5

        self._pose_pub.publish(pose_msg)

        yaw_deg = math.degrees(yaw)
        response.success = True
        response.message = f"published initial pose (yaw {yaw_deg:.1f} deg)"
        self.get_logger().info(response.message)
        return response

    # ── Yaw computation ──────────────────────────────────────────

    @staticmethod
    def _compute_yaw(points: list[_Point], closest_idx: int) -> Optional[float]:
        min_seg_len2 = 1.0e-6

        for i in range(closest_idx, len(points) - 1):
            p0, p1 = points[i], points[i + 1]
            dx, dy = p1.x - p0.x, p1.y - p0.y
            if dx * dx + dy * dy > min_seg_len2:
                return math.atan2(dy, dx)

        for i in range(closest_idx, 0, -1):
            p0, p1 = points[i - 1], points[i]
            dx, dy = p1.x - p0.x, p1.y - p0.y
            if dx * dx + dy * dy > min_seg_len2:
                return math.atan2(dy, dx)

        return None


def main() -> None:
    rclpy.init()
    node = HeadingPoseInitializerNode()
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
