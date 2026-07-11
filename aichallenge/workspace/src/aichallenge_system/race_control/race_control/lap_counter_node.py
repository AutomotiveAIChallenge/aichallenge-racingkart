#!/usr/bin/env python3
"""Lap counter node.

Derives a start line from the lanelet2 map (the entry edge of a configured
lanelet: first left-bound node to first right-bound node, using local_x/local_y
tags) and counts laps / lap times each time the vehicle crosses it.
Geometry/state lives in race_control.lap_tracker.LapTracker (pure, tested).
"""

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import Float64, Int32, String

from race_control.lanelet_map import LaneletMap
from race_control.lap_tracker import LapTracker


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
        min_lap_time = self.declare_parameter("min_lap_time", 10.0).value
        margin = self.declare_parameter("line_margin", 2.0).value
        odom_topic = self.declare_parameter(
            "odom_topic", "/localization/kinematic_state"
        ).value

        line_a, line_b = load_start_line(map_path, lanelet_id)
        self._tracker = LapTracker(
            line_a, line_b, margin=margin, min_lap_time=min_lap_time
        )
        self.get_logger().info(
            f"start line: ({line_a[0]:.2f}, {line_a[1]:.2f}) -> "
            f"({line_b[0]:.2f}, {line_b[1]:.2f}) (lanelet {lanelet_id})"
        )

        self._pub_count = self.create_publisher(Int32, "~/lap_count", 1)
        self._pub_last = self.create_publisher(Float64, "~/last_lap_time", 1)
        self._pub_current = self.create_publisher(Float64, "~/current_lap_time", 1)
        self._pub_summary = self.create_publisher(String, "~/summary", 1)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)

    def _on_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self._tracker.update(p.x, p.y, stamp):
            self._on_lap()
        if self._tracker.lap_start is not None:
            self._pub_current.publish(Float64(data=stamp - self._tracker.lap_start))

    def _on_lap(self):
        tracker = self._tracker
        if tracker.lap_times:
            self._pub_last.publish(Float64(data=tracker.lap_times[-1]))
        self._pub_count.publish(Int32(data=tracker.lap_count))
        times = ", ".join(f"{t:.2f}" for t in tracker.lap_times)
        summary = f"lap={tracker.lap_count} lap_times=[{times}]"
        self._pub_summary.publish(String(data=summary))
        self.get_logger().info(summary)


def main():
    rclpy.init()
    node = None
    try:
        node = LapCounterNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
