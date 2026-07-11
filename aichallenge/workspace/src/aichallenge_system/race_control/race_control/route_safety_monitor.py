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

from race_control.route_area import RouteArea


class RouteDeviationSafetyMonitor:
    """Thin wrapper: builds a RouteArea from an .osm map and tests containment."""

    def __init__(self, osm_file_path, logger=None):
        self.area = RouteArea.from_osm(osm_file_path)
        if logger:
            logger.info(
                f"Loaded {len(self.area)} lanelet polygons from {osm_file_path}"
            )

    def is_in_any_lane(self, x, y):
        return self.area.contains(x, y)


class RouteDeviationSafetyMonitorNode(rclpy.node.Node):
    def __init__(self):
        super().__init__("route_deviation_safety_monitor")

        osm_path = self.declare_parameter("osm_path", "").value
        if not osm_path:
            osm_path = os.path.join(
                get_package_share_directory("race_control"), "map", "route_area.osm"
            )
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
