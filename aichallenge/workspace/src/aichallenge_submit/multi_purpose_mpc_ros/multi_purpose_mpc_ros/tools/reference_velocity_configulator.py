#!/usr/bin/env python3

from typing import Dict, List, Tuple

import os, shutil
from datetime import datetime
import copy
import yaml
from collections import OrderedDict

# ROS 2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy
from rcl_interfaces.msg import SetParametersResult

from geometry_msgs.msg import Point, Vector3
from visualization_msgs.msg import Marker, MarkerArray
from std_msgs.msg import ColorRGBA

# Multi_Purpose_MPC
from multi_purpose_mpc_ros.tools.reference_path_generator import ReferencePathGenerator

RED = ColorRGBA(r=1.0, g=0.0, b=0.0, a=1.0)
YELLOW = ColorRGBA(r=1.0, g=1.0, b=0.0, a=1.0)
CYAN = ColorRGBA(r=0.0, g=156.0 / 255.0, b=209.0 / 255.0, a=1.0)
WHITE = ColorRGBA(r=1.0, g=1.0, b=1.0, a=1.0)

class ReferenceVelocityConfigulator():
    MIN_VELOCITY = 20.0 # km/h
    MAX_VELOCITY = 30.0 # km/h

    def __init__(
            self,
            node: Node,
            ref_path_config_path: str,
            ref_vel_config_path: str) -> None:

        self._node = node

        self._ref_vel_config_path = ref_vel_config_path
        self._reference_path = ReferencePathGenerator.get_reference_path(ref_path_config_path)
        self._ref_vel_cfg = self._load_config(ref_vel_config_path)
        self._setup_publisher()
        self._register_params()

        self._ref_path_marker_array = MarkerArray()
        self._section_marker_array = MarkerArray()
        self._update_marker()
        self._timer = self._node.create_timer(2.0, self._timer_callback)

    @classmethod
    def _load_config(cls, config_path: str) -> Dict:
        with open(config_path, "r") as f:
            cfg: Dict = yaml.safe_load(f) # type: ignore
        return cfg["ref_vel_configulator"]

    def _register_params(self):
        def declatre_parameters():
            for section_name, info in self._ref_vel_cfg.items():
                for key, value in info.items():
                    if key != "wp_id" and key != "ref_vel":
                        raise ValueError(f"Invalid key: {key}")
                    self._node.declare_parameter(f"ref_vel/{section_name}/{key}", value)

            self._node.declare_parameter(f"ref_vel/save", False)

        def param_cb(parameters):
            for param in parameters:
                if not param.name.startswith("ref_vel/"):
                    continue

                if param.name == "ref_vel/save":
                    # backup current config file
                    current_datetime = datetime.now().strftime('%Y%m%d_%H%M%S')
                    backup_filename = f"{self._ref_vel_config_path}.{current_datetime}"
                    if os.path.exists(self._ref_vel_config_path):
                        shutil.copy2(self._ref_vel_config_path, backup_filename)

                    cfg_for_save = { "ref_vel_configulator": self._ref_vel_cfg }
                    # save current config
                    with open(self._ref_vel_config_path, 'w', encoding='utf-8') as file:
                        yaml.dump(cfg_for_save, file, allow_unicode=True, default_flow_style=False)
                    continue
                else:
                    _, section_name, param_name = param.name.split("/")
                    self._ref_vel_cfg[section_name][param_name] = param.value

            self._update_marker()
            return SetParametersResult(successful=True)

        declatre_parameters()
        self._node.add_on_set_parameters_callback(param_cb)

    def _setup_publisher(self) -> None:
        latching_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self._ref_vel_marker_pub = self._node.create_publisher(
            MarkerArray, "/ref_vel_marker", latching_qos)
        self._section_marker_pub = self._node.create_publisher(
            MarkerArray, "/section_marker", latching_qos)

    def _timer_callback(self) -> None:
        self._ref_vel_marker_pub.publish(self._ref_path_marker_array)
        self._section_marker_pub.publish(self._section_marker_array)

    def _update_marker(self):

        class Section:
            def __init__(self, name, ref_vel):
                self.name = name
                self.ref_vel = ref_vel

        def add_ref_path_marker(markers: MarkerArray, wp_id_section_map: Dict[int, Section]) -> None:
            line_base = Marker()
            line_base.header.frame_id = "map"
            line_base.ns = "ref_path"
            line_base.type = Marker.LINE_STRIP
            line_base.action = Marker.ADD
            line_base.pose.position.z = 0.0
            line_base.scale.x = 0.1

            ref_path = self._reference_path
            current_section_idx: int = -1
            wp_id_section_list: List[Tuple[int, Section]] = list(wp_id_section_map.items())
            current_ref_vel = wp_id_section_list[current_section_idx][1].ref_vel

            for i in range(len(ref_path.waypoints) - 1):
                if (current_section_idx < len(wp_id_section_list)-1) and \
                    (i >= wp_id_section_list[current_section_idx + 1][0]):
                    current_section_idx += 1
                    current_ref_vel = wp_id_section_list[current_section_idx][1].ref_vel
                line = copy.deepcopy(line_base)
                line.id = i
                line.color = self.create_vel_heat_color(current_ref_vel)
                start = Point()
                start.x = ref_path.waypoints[i].x
                start.y = ref_path.waypoints[i].y
                end = Point()
                end.x = ref_path.waypoints[i + 1].x
                end.y = ref_path.waypoints[i + 1].y
                line.points.append(start) # type: ignore
                line.points.append(end) # type: ignore
                markers.markers.append(line) # type: ignore

        def add_section_markers(markers: MarkerArray, wp_id_section_map: Dict) -> None:
            spheres = Marker()
            spheres.header.frame_id = "map"
            spheres.ns = "section_start_point"
            spheres.type = Marker.SPHERE_LIST
            spheres.action = Marker.ADD
            radius = 1.0
            spheres.scale = Vector3(x=radius, y=radius, z=radius)
            spheres.color = YELLOW

            text_base = Marker()
            text_base.header.frame_id = "map"
            text_base.type = Marker.TEXT_VIEW_FACING
            text_base.action = Marker.ADD
            text_base.pose.position.z = 0.0
            text_base.scale.z = 1.5

            for wp_id, section in wp_id_section_map.items():
                p = Point()
                p.x = self._reference_path.waypoints[wp_id].x
                p.y = self._reference_path.waypoints[wp_id].y
                p.z = 10.
                spheres.points.append(p) #type: ignore

                text = copy.deepcopy(text_base)
                text.ns = f"ref_vel_sect_{section.name}"
                text.pose.position = copy.deepcopy(p)
                text.pose.position.x += 2.0
                text.pose.position.y += 2.0
                text.text = f"{section.name}/wp{wp_id}:\n{section.ref_vel:.2f} kmph"
                text.color = self.create_vel_heat_color(section.ref_vel)
                markers.markers.append(text) # type: ignore

            markers.markers.append(spheres) # type: ignore

        # {wp_id: ref_vel}の　dict であり、wp_idが昇順になるように並べる
        sorted_dict: List[Tuple[int, Section]] = sorted(
            (
                ( int(v['wp_id']), Section(k, v['ref_vel']) ) for k, v in self._ref_vel_cfg.items()
            ),
            key=lambda item: int(item[0]) # sort by wp_id
        )
        wp_id_section_map: OrderedDict[int, Section] = OrderedDict(sorted_dict)

        self._ref_path_marker_array = MarkerArray()
        add_ref_path_marker(self._ref_path_marker_array, wp_id_section_map)
        self._section_marker_array = MarkerArray()
        add_section_markers(self._section_marker_array, wp_id_section_map)

        # get_ref_vel 用の wp_id と ref_vel の map を作成
        self._sorted_wp_id_ref_vel_map = OrderedDict(
            sorted(
                (
                    ( int(v['wp_id']), v['ref_vel'] ) for k, v in self._ref_vel_cfg.items()
                ),
                key=lambda item: int(item[0]) # sort by wp_id
            )
        )

    def get_ref_vel(self, current_wp_id: int) -> float:
        # セクションの始点となる waypoint ID を昇順にソート
        sorted_keys = sorted(self._sorted_wp_id_ref_vel_map.keys())
        num_keys = len(sorted_keys)

        for i in range(num_keys):
            start = sorted_keys[i]
            end = sorted_keys[(i + 1) % num_keys]  # 次のキー。最後は最初のキーに戻る
            target_speed = self._sorted_wp_id_ref_vel_map[start]

            if start <= end:
                # セクションが通常の順序の場合
                if start <= current_wp_id < end:
                    return target_speed
            else:
                # セクションがコースを一周する場合
                if current_wp_id >= start or current_wp_id < end:
                    return target_speed

        # どのセクションにも該当しない場合 (通常はここには到達しない)
        raise ValueError("Current waypoint ID does not fall into any section.")

    @classmethod
    def create_vel_heat_color(cls, vel_norm: float, alpha: float = 1.0) -> ColorRGBA:
        normalized_velocity = max(
            0.0,
            min(
                (vel_norm - cls.MIN_VELOCITY) / (cls.MAX_VELOCITY - cls.MIN_VELOCITY),
                1.0)
            )
        r = normalized_velocity
        g = 0.2
        b = 1.0 - normalized_velocity
        return ColorRGBA(r=r, g=g, b=b, a=alpha)

def main(args=None):
    rclpy.init(args=args)
    config_path = "config/ref_vel.yaml"
    node = ReferencePathGenerator(config_path)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()