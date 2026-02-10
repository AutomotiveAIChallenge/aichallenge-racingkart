#!/usr/bin/env python3

import copy
import numpy as np

# ROS 2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy

from geometry_msgs.msg import Point, Vector3
from visualization_msgs.msg import Marker, MarkerArray
from std_msgs.msg import ColorRGBA

# Multi_Purpose_MPC
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.tools.reference_path_generator import ReferencePathGenerator


class ReferencePathVisualizer(Node):
    WP_SPHERE_ENABLED: bool = False
    LENGTH_TEXT_ENABLED: bool = False

    def __init__(self, config_path: str) -> None:
        super().__init__("reference_path_visualizer")

        self._reference_path = ReferencePathGenerator.get_reference_path(config_path)
        self._setup_publisher()

    def _setup_publisher(self) -> None:
        latching_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self._ref_path_pub = self.create_publisher(
            MarkerArray, "/mpc/ref_path", latching_qos)

        # Timer to periodically publish the reference path
        self._timer = self.create_timer(1.0, self._publish_ref_path)

    def _publish_ref_path(self):
        self._publish_ref_path_marker(self._reference_path)

    def _publish_ref_path_marker(self, ref_path: ReferencePath):

        ref_path_marker_array = MarkerArray()

        line_base = Marker()
        line_base.header.frame_id = "map"
        line_base.ns = "ref_path"
        line_base.type = Marker.LINE_STRIP
        line_base.action = Marker.ADD
        line_base.pose.position.z = 0.0
        line_base.scale.x = 0.2
        line_base.color = ColorRGBA(r=0.0, g=0.0, b=1.0, a=0.7)

        for i in range(len(ref_path.waypoints) - 1):
            line = copy.deepcopy(line_base)
            line.id = i
            start = Point()
            start.x = ref_path.waypoints[i].x
            start.y = ref_path.waypoints[i].y
            end = Point()
            end.x = ref_path.waypoints[i + 1].x
            end.y = ref_path.waypoints[i + 1].y
            line.points.append(start) # type: ignore
            line.points.append(end) # type: ignore
            ref_path_marker_array.markers.append(line) # type: ignore

        if self.WP_SPHERE_ENABLED:
            spheres = Marker()
            spheres.header.frame_id = "map"
            spheres.ns = "ref_path_point"
            spheres.type = Marker.SPHERE_LIST
            spheres.action = Marker.ADD
            radius = 0.2
            spheres.scale = Vector3(x=radius, y=radius, z=radius)
            spheres.color = ColorRGBA(r=1.0, g=1.0, b=0.0, a=0.7)

            length_cum = np.cumsum(self._reference_path.segment_lengths)

            text_base = Marker()
            text_base.header.frame_id = "map"
            text_base.type = Marker.TEXT_VIEW_FACING
            text_base.action = Marker.ADD
            text_base.pose.position.z = 0.0
            text_base.scale.z = 0.7
            text_base.color = ColorRGBA(r=1.0, g=1.0, b=1.0, a=0.7)


            for i in range(len(ref_path.waypoints) - 1):
                p = Point()
                p.x = ref_path.waypoints[i].x
                p.y = ref_path.waypoints[i].y
                p.z = 0.
                spheres.points.append(p) #type: ignore

                text_i = copy.deepcopy(text_base)
                text_i.ns = f"ref_path_length_{i}"
                text_i.pose.position = p
                text_i.text = f"{i}_{length_cum[i]:.2f}"
                ref_path_marker_array.markers.append(text_i) # type: ignore

            ref_path_marker_array.markers.append(spheres) # type: ignore

        if self.LENGTH_TEXT_ENABLED:
            text_base = Marker()
            text_base.header.frame_id = "map"
            text_base.type = Marker.TEXT_VIEW_FACING
            text_base.action = Marker.ADD
            text_base.pose.position.z = 0.0
            text_base.scale.z = 0.7
            text_base.color = ColorRGBA(r=1.0, g=1.0, b=1.0, a=0.7)

            length_cum = np.cumsum(self._reference_path.segment_lengths)
            for i in range(len(ref_path.waypoints) - 1):
                p = Point()
                p.x = ref_path.waypoints[i].x
                p.y = ref_path.waypoints[i].y
                p.z = 0.

                text_i = copy.deepcopy(text_base)
                text_i.ns = f"ref_path_length_{i}"
                text_i.pose.position = p
                text_i.text = f"{i}_{length_cum[i]:.2f}"
                ref_path_marker_array.markers.append(text_i) # type: ignore

        self._ref_path_pub.publish(ref_path_marker_array)

def main(args=None):
    rclpy.init(args=args)
    config_path = "config/config.yaml"
    node = ReferencePathVisualizer(config_path)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()