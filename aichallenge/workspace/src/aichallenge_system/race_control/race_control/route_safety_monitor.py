#!/usr/bin/env python3
"""Route deviation safety monitor.

Loads the drivable route area from a lanelet2 .osm map (each lanelet becomes a
polygon of its left bound + reversed right bound) and publishes whether the
vehicle is currently outside every lanelet.
"""

import os

import rclpy
import rclpy.node
from ament_index_python.packages import get_package_share_directory
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool

from race_control.lanelet_map import LaneletMap


def _point_in_polygon(x, y, polygon_x, polygon_y):
    """Ray-casting point-in-polygon test."""
    n = len(polygon_x)
    j = n - 1
    inside = False
    for i in range(n):
        xi, yi = polygon_x[i], polygon_y[i]
        xj, yj = polygon_x[j], polygon_y[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class RouteDeviationSafetyMonitor:
    """Builds lanelet polygons from an .osm map and tests containment."""

    def __init__(self, osm_file_path, logger=None):
        lmap = LaneletMap(osm_file_path)
        self._lane_polygons = []  # list of (xs_tuple, ys_tuple)
        for _lid, left_way, right_way in lmap.lanelets:
            coords = lmap.way_coords(left_way) + list(
                reversed(lmap.way_coords(right_way))
            )
            if len(coords) >= 3:
                self._lane_polygons.append(
                    (tuple(p[0] for p in coords), tuple(p[1] for p in coords))
                )
        if logger:
            logger.info(
                f"Loaded {len(self._lane_polygons)} lanelet polygons from {osm_file_path}"
            )

    def is_in_any_lane(self, x, y):
        for px, py in self._lane_polygons:
            if _point_in_polygon(x, y, px, py):
                return True
        return False


class RouteDeviationSafetyMonitorNode(rclpy.node.Node):
    def __init__(self):
        super().__init__("route_deviation_safety_monitor")

        default_map = os.path.join(
            get_package_share_directory("race_control"), "map", "route_area.osm"
        )
        osm_path = self.declare_parameter("osm_path", default_map).value
        odom_topic = self.declare_parameter(
            "odom_topic", "/localization/kinematic_state"
        ).value
        deviation_topic = self.declare_parameter(
            "deviation_topic", "/vehicle/emergency/is_route_deviation"
        ).value
        period = self.declare_parameter("monitor_period", 0.5).value

        self.safety_monitor = RouteDeviationSafetyMonitor(
            osm_path, logger=self.get_logger()
        )

        self._position = None  # (x, y) or None
        self.is_outside_route = False

        self.create_subscription(Odometry, odom_topic, self.position_callback, 1)
        self.safety_control_pub = self.create_publisher(Bool, deviation_topic, 10)
        self.create_timer(period, self.monitor_position)

    def position_callback(self, msg: Odometry):
        self._position = (msg.pose.pose.position.x, msg.pose.pose.position.y)

    def monitor_position(self):
        pos = self._position
        if pos is None:
            return

        is_in_lane = self.safety_monitor.is_in_any_lane(pos[0], pos[1])
        if is_in_lane and self.is_outside_route:
            self.get_logger().info("Vehicle returned to route")
        elif not is_in_lane and not self.is_outside_route:
            self.get_logger().error("Route deviation detected")
        self.is_outside_route = not is_in_lane

        self.safety_control_pub.publish(Bool(data=self.is_outside_route))


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = RouteDeviationSafetyMonitorNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
