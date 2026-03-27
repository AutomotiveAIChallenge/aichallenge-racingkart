#!/usr/bin/env python3
"""Headless /set_initial_pose service node.

Provides the same initial-pose logic that was previously embedded in the
RViz control_mode_panel, but without any GUI dependency. This allows
initial pose setting to work in CUI / GPU-less environments.

Logic:
  1. Subscribe to GNSS pose and trajectory.
  2. On /set_initial_pose service call, find the closest trajectory point
     to the current GNSS position and compute yaw from adjacent points.
  3. Publish the result on /initialpose.
"""

from __future__ import annotations

import math
import threading
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from autoware_auto_planning_msgs.msg import Trajectory
from geometry_msgs.msg import Point, PoseWithCovarianceStamped
from std_srvs.srv import Trigger


class InitialPoseServiceNode(Node):
    def __init__(self) -> None:
        super().__init__("initial_pose_service")

        self.declare_parameter("gnss_pose_topic", "/sensing/gnss/pose_with_covariance")
        self.declare_parameter("trajectory_topic", "/planning/scenario_planning/trajectory")
        self.declare_parameter("initial_pose_topic", "/initialpose")
        self.declare_parameter("service_name", "/set_initial_pose")
        self.declare_parameter("wait_timeout_sec", 120)

        gnss_topic = str(self.get_parameter("gnss_pose_topic").value)
        traj_topic = str(self.get_parameter("trajectory_topic").value)
        pose_topic = str(self.get_parameter("initial_pose_topic").value)
        service_name = str(self.get_parameter("service_name").value)

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._last_gnss: Optional[PoseWithCovarianceStamped] = None
        self._last_traj_points: list[Point] = []
        self._last_traj_frame_id: str = ""

        reliable_volatile = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        best_effort_volatile = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )

        self._gnss_sub = self.create_subscription(
            PoseWithCovarianceStamped, gnss_topic, self._on_gnss, reliable_volatile
        )
        self._traj_sub = self.create_subscription(
            Trajectory, traj_topic, self._on_trajectory, best_effort_volatile
        )
        self._pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, pose_topic, reliable_volatile
        )
        self._service = self.create_service(Trigger, service_name, self._on_service)

        self.get_logger().info(
            f"initial_pose_service ready: gnss={gnss_topic} traj={traj_topic} "
            f"pub={pose_topic} srv={service_name}"
        )

    def _on_gnss(self, msg: PoseWithCovarianceStamped) -> None:
        with self._cv:
            self._last_gnss = msg
            self._cv.notify_all()

    def _on_trajectory(self, msg: Trajectory) -> None:
        points: list[Point] = []
        for tp in msg.points:
            p = Point()
            p.x = tp.pose.position.x
            p.y = tp.pose.position.y
            p.z = 0.0
            points.append(p)
        with self._cv:
            self._last_traj_points = points
            self._last_traj_frame_id = msg.header.frame_id
            self._cv.notify_all()

    def _on_service(
        self,
        _request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        timeout = int(self.get_parameter("wait_timeout_sec").value)
        self.get_logger().info(f"set_initial_pose called, waiting up to {timeout}s for GNSS+trajectory")

        with self._cv:
            self._cv.wait_for(
                lambda: self._last_gnss is not None and len(self._last_traj_points) >= 2,
                timeout=float(timeout),
            )
            gnss = self._last_gnss
            traj_points = list(self._last_traj_points)
            traj_frame_id = self._last_traj_frame_id

        if gnss is None:
            response.success = False
            response.message = "timeout waiting for GNSS"
            self.get_logger().error(response.message)
            return response

        if len(traj_points) < 2:
            response.success = False
            response.message = "timeout waiting for trajectory"
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
        found = False
        for i, pt in enumerate(traj_points):
            if not (math.isfinite(pt.x) and math.isfinite(pt.y)):
                continue
            d2 = (pt.x - gnss_pt.x) ** 2 + (pt.y - gnss_pt.y) ** 2
            if d2 < closest_d2:
                closest_d2 = d2
                closest_idx = i
                found = True

        if not found:
            response.success = False
            response.message = "all trajectory points are invalid"
            self.get_logger().error(response.message)
            return response

        yaw = self._compute_yaw(traj_points, closest_idx)
        if yaw is None:
            response.success = False
            response.message = "cannot compute yaw from trajectory"
            self.get_logger().error(response.message)
            return response

        pose_msg = PoseWithCovarianceStamped()
        pose_msg.header.stamp = self.get_clock().now().to_msg()
        pose_msg.header.frame_id = traj_frame_id or gnss.header.frame_id
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

    @staticmethod
    def _compute_yaw(points: list[Point], closest_idx: int) -> Optional[float]:
        min_seg_len2 = 1.0e-6

        for i in range(closest_idx, len(points) - 1):
            p0, p1 = points[i], points[i + 1]
            if not all(math.isfinite(v) for v in (p0.x, p0.y, p1.x, p1.y)):
                continue
            dx, dy = p1.x - p0.x, p1.y - p0.y
            if dx * dx + dy * dy > min_seg_len2:
                return math.atan2(dy, dx)

        for i in range(closest_idx, 0, -1):
            p0, p1 = points[i - 1], points[i]
            if not all(math.isfinite(v) for v in (p0.x, p0.y, p1.x, p1.y)):
                continue
            dx, dy = p1.x - p0.x, p1.y - p0.y
            if dx * dx + dy * dy > min_seg_len2:
                return math.atan2(dy, dx)

        return None


def main() -> None:
    rclpy.init()
    node = InitialPoseServiceNode()
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
