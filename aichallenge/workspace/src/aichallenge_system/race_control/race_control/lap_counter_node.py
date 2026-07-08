#!/usr/bin/env python3
"""Lap counter node.

Derives a start line from the lanelet2 map (the entry edge of a configured
lanelet: first left-bound node to first right-bound node, using local_x/local_y
tags) and counts laps / lap times each time the vehicle crosses it.

Kept intentionally minimal: XML is parsed once at startup and the per-odometry
work is a couple of 2D geometry operations.
"""

import math
import os
import sys

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import Float64, Int32, String

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lanelet_map import LaneletMap  # noqa: E402


def load_start_line(map_path: str, lanelet_id: int):
    """Return ((ax, ay), (bx, by)): entry edge of the given lanelet."""
    lmap = LaneletMap(map_path)
    bound = lmap.lanelet(lanelet_id)
    if bound is None:
        raise ValueError(f"lanelet id {lanelet_id} not found in {map_path}")
    left_way, right_way = bound
    return lmap.way_coords(left_way)[0], lmap.way_coords(right_way)[0]


class LapCounterNode(Node):
    def __init__(self):
        super().__init__("lap_counter")
        map_path = self.declare_parameter("map_path", "").value
        lanelet_id = self.declare_parameter("start_lanelet_id", 14).value
        self._min_lap_time = self.declare_parameter("min_lap_time", 10.0).value
        self._margin = self.declare_parameter("line_margin", 2.0).value
        odom_topic = self.declare_parameter(
            "odom_topic", "/localization/kinematic_state"
        ).value

        (ax, ay), (bx, by) = load_start_line(map_path, lanelet_id)
        self._a = (ax, ay)
        self._ab = (bx - ax, by - ay)
        self._ab_len = math.hypot(*self._ab)
        self.get_logger().info(
            f"start line: ({ax:.2f}, {ay:.2f}) -> ({bx:.2f}, {by:.2f}) "
            f"(lanelet {lanelet_id})"
        )

        self._prev_side = None
        self._lap_start = None  # stamp (float sec) of current lap start
        self._lap_count = -1  # first crossing arms lap 1
        self._lap_times = []

        self._pub_count = self.create_publisher(Int32, "~/lap_count", 1)
        self._pub_last = self.create_publisher(Float64, "~/last_lap_time", 1)
        self._pub_current = self.create_publisher(Float64, "~/current_lap_time", 1)
        self._pub_summary = self.create_publisher(String, "~/summary", 1)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)

    def _on_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        apx, apy = p.x - self._a[0], p.y - self._a[1]
        cross = self._ab[0] * apy - self._ab[1] * apx
        side = cross > 0.0
        # projection along the line, normalized [0, 1]
        t = (self._ab[0] * apx + self._ab[1] * apy) / (self._ab_len**2)
        on_segment = -self._margin / self._ab_len <= t <= 1.0 + self._margin / self._ab_len

        if self._prev_side is not None and side != self._prev_side and on_segment:
            self._on_cross(stamp)
        self._prev_side = side

        if self._lap_start is not None:
            self._pub_current.publish(Float64(data=stamp - self._lap_start))

    def _on_cross(self, stamp: float):
        if self._lap_start is not None:
            lap_time = stamp - self._lap_start
            if lap_time < self._min_lap_time:
                return  # debounce jitter around the line
            self._lap_times.append(lap_time)
            self._pub_last.publish(Float64(data=lap_time))
        self._lap_count += 1
        self._lap_start = stamp
        self._pub_count.publish(Int32(data=self._lap_count))
        times = ", ".join(f"{t:.2f}" for t in self._lap_times)
        summary = f"lap={self._lap_count} lap_times=[{times}]"
        self._pub_summary.publish(String(data=summary))
        self.get_logger().info(summary)


def main():
    rclpy.init()
    node = LapCounterNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
