#!/usr/bin/env python3

import rclpy
import rclpy.node
import xml.etree.ElementTree as ET
import os
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool


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
    def __init__(self, osm_file_path=None, logger=None):
        if osm_file_path:
            self.osm_file = osm_file_path
        else:
            try:
                from ament_index_python.packages import get_package_share_directory
                self.osm_file = os.path.join(
                    get_package_share_directory('race_control'),
                    'map', 'route_area.osm'
                )
            except ImportError:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                self.osm_file = os.path.join(script_dir, '..', 'map', 'route_area.osm')

        # list of (xs_tuple, ys_tuple) per lanelet polygon
        self._lane_polygons = []
        self._load_map(logger)

    def _load_map(self, logger=None):
        try:
            tree = ET.parse(self.osm_file)
        except (ET.ParseError, FileNotFoundError, OSError) as e:
            if logger:
                logger.error(f"Failed to load map {self.osm_file}: {e}")
            raise

        root = tree.getroot()

        nodes = {}
        for node in root.findall("node"):
            node_id = node.attrib['id']
            local_x = local_y = None
            for tag in node.findall('tag'):
                if tag.attrib['k'] == 'local_x':
                    local_x = float(tag.attrib['v'])
                elif tag.attrib['k'] == 'local_y':
                    local_y = float(tag.attrib['v'])
            if local_x is not None and local_y is not None:
                nodes[node_id] = (local_x, local_y)

        for relation in root.findall("relation"):
            if relation.find("tag[@k='type'][@v='lanelet']") is not None:
                left_way = right_way = None
                for member in relation.findall("member"):
                    role = member.attrib.get('role')
                    ref = member.attrib.get('ref')
                    if role == 'left':
                        left_way = ref
                    elif role == 'right':
                        right_way = ref

                if left_way and right_way:
                    left_coords = self._get_way_coordinates(root, nodes, left_way)
                    right_coords = self._get_way_coordinates(root, nodes, right_way)
                    if left_coords and right_coords:
                        coords = left_coords + list(reversed(right_coords))
                        if len(coords) >= 3:
                            xs = tuple(p[0] for p in coords)
                            ys = tuple(p[1] for p in coords)
                            self._lane_polygons.append((xs, ys))

        if logger:
            logger.info(f"Loaded {len(self._lane_polygons)} lanelet polygons from {self.osm_file}")

    @staticmethod
    def _get_way_coordinates(root, nodes, way_id):
        way = root.find(f"way[@id='{way_id}']")
        if way is None:
            return []
        coords = []
        for nd in way.findall('nd'):
            node_ref = nd.attrib['ref']
            if node_ref in nodes:
                coords.append(nodes[node_ref])
        return coords

    def is_in_any_lane(self, x, y):
        for px, py in self._lane_polygons:
            if _point_in_polygon(x, y, px, py):
                return True
        return False


class RouteDeviationSafetyMonitorNode(rclpy.node.Node):
    def __init__(self):
        super().__init__("route_deviation_safety_monitor")

        self.safety_monitor = RouteDeviationSafetyMonitor(logger=self.get_logger())

        self._position = None  # (x, y) tuple or None
        self.is_outside_route = False

        self.position_sub = self.create_subscription(
            Odometry,
            '/localization/kinematic_state',
            self.position_callback,
            1
        )

        self.safety_control_pub = self.create_publisher(
            Bool,
            '/vehicle/emergency/is_route_deviation',
            10
        )

        self.monitoring_timer = self.create_timer(0.5, self.monitor_position)

    def position_callback(self, msg: Odometry):
        self._position = (msg.pose.pose.position.x, msg.pose.pose.position.y)

    def monitor_position(self):
        pos = self._position
        if pos is None:
            return

        is_in_lane = self.safety_monitor.is_in_any_lane(pos[0], pos[1])

        if is_in_lane:
            if self.is_outside_route:
                self.get_logger().info("Vehicle returned to route")
            self.is_outside_route = False
        else:
            if not self.is_outside_route:
                self.get_logger().error("Route deviation detected")
            self.is_outside_route = True

        safety_msg = Bool()
        safety_msg.data = self.is_outside_route
        self.safety_control_pub.publish(safety_msg)


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
