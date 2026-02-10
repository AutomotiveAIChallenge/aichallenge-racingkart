#!/usr/bin/env python3

import yaml
from typing import NamedTuple

# ROS 2
from ament_index_python.packages import get_package_share_directory

# Multi_Purpose_MPC
from multi_purpose_mpc_ros.core.map import Map
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.utils import load_waypoints, load_ref_path

# Project
from multi_purpose_mpc_ros.common import convert_to_namedtuple, file_exists


class ReferencePathGenerator():
    PKG_PATH: str = get_package_share_directory('multi_purpose_mpc_ros') + "/"

    @classmethod
    def __new__(cls, *args, **kwargs):
        raise TypeError(f"Cannot instantiate {cls.__name__}")

    @classmethod
    def get_reference_path(cls, config_path: str) -> ReferencePath:
        cfg = cls.__load_config(config_path)
        return cls.__generate_ref_path(cfg)

    @classmethod
    def __load_config(cls, config_path: str) -> NamedTuple:
        with open(config_path, "r") as f:
            cfg: NamedTuple = convert_to_namedtuple(yaml.safe_load(f))

        # Check if the files exist
        mandatory_files = [cfg.map.yaml_path, cfg.waypoints.csv_path]
        for file_path in mandatory_files:
            file_exists(cls.__in_pkg_share(file_path))
        return cfg

    @classmethod
    def __generate_ref_path(cls, cfg: NamedTuple) -> ReferencePath:
        def create_map() -> Map:
            return Map(cls.__in_pkg_share(cfg.map.yaml_path))

        def create_ref_path(map: Map) -> ReferencePath:
            cfg_ref_path = cfg.reference_path

            is_ref_path_given = cfg_ref_path.csv_path != ""
            if is_ref_path_given:
                print("Using given reference path")
                wp_x, wp_y, _, _ = load_ref_path(cls.__in_pkg_share(cfg.reference_path.csv_path))
                return ReferencePath(
                    map,
                    wp_x,
                    wp_y,
                    cfg_ref_path.resolution,
                    cfg_ref_path.smoothing_distance,
                    cfg_ref_path.max_width,
                    cfg_ref_path.circular)
            else:
                print("Using waypoints to create reference path")
                wp_x, wp_y = load_waypoints(cls.__in_pkg_share(cfg.waypoints.csv_path))
                return ReferencePath(
                    map,
                    wp_x,
                    wp_y,
                    cfg_ref_path.resolution,
                    cfg_ref_path.smoothing_distance,
                    cfg_ref_path.max_width,
                    cfg_ref_path.circular)

        map = create_map()
        return create_ref_path(map)

    @classmethod
    def __in_pkg_share(cls, file_path: str) -> str:
        return cls.PKG_PATH + file_path
