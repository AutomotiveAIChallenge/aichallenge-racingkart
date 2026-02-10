#!/usr/bin/env python3

import yaml
from typing import List, Tuple, Optional, NamedTuple
import dataclasses
from scipy import sparse
from scipy.sparse import dia_matrix
import numpy as np

# ROS 2
import rclpy
from rclpy.node import Node
from ament_index_python.packages import get_package_share_directory
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, QoSDurabilityPolicy

from std_msgs.msg import  Float64MultiArray
from nav_msgs.msg import Odometry

# autoware
from autoware_auto_planning_msgs.msg import Trajectory

# Multi_Purpose_MPC
from multi_purpose_mpc_ros.core.map import Map, Obstacle
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.core.MPC import MPC
from multi_purpose_mpc_ros.core.utils import load_waypoints, kmh_to_m_per_sec, load_ref_path

# Project
from multi_purpose_mpc_ros.common import convert_to_namedtuple, file_exists
from multi_purpose_mpc_ros.obstacle_manager import ObstacleManager
from multi_purpose_mpc_ros_msgs.msg import PathConstraints, BorderCells


@dataclasses.dataclass
class MPCConfig:
    N: int
    Q: dia_matrix
    R: dia_matrix
    QN: dia_matrix
    v_max: float
    a_min: float
    a_max: float
    ay_max: float
    delta_max: float
    control_rate: float


class PathConstraintsProvider(Node):
    PKG_PATH: str = get_package_share_directory('multi_purpose_mpc_ros') + "/"
    USE_BUG_ACC = True
    BUG_VEL = 40.0 # km/h
    BUG_ACC = 400.0

    def __init__(self, config_path: str) -> None:
        super().__init__("path_constraints_proveder") # type: ignore

        # declare parameters
        self.declare_parameter("use_boost_acceleration", False)

        # get parameters
        self.USE_BUG_ACC = self.get_parameter("use_boost_acceleration").get_parameter_value().bool_value

        self._cfg = self._load_config(config_path)
        self._odom: Optional[Odometry] = None
        self._enable_control = None
        self._initialize()
        self._setup_pub_sub()

        # set use_sim_time parameter
        param = Parameter("use_sim_time", Parameter.Type.BOOL, True)
        self.set_parameters([param])

    def destroy(self) -> None:
        self._timer.destroy() # type: ignore
        self._obstacles_sub.shutdown() # type: ignore
        self._path_constraints_pub.shutdown() # type: ignore
        self._border_cells_pub.shutdown() # type: ignore
        self._group.destroy() # type: ignore
        super().destroy_node()

    def _load_config(self, config_path: str) -> NamedTuple:
        with open(config_path, "r") as f:
            cfg: NamedTuple = convert_to_namedtuple(yaml.safe_load(f)) # type: ignore

        # Check if the files exist
        mandatory_files = [cfg.map.yaml_path, cfg.waypoints.csv_path] # type: ignore
        for file_path in mandatory_files:
            file_exists(self.in_pkg_share(file_path))
        return cfg

    def _setup_pub_sub(self) -> None:
        # Subscribers
        self._obstacles_sub = self.create_subscription(
            Float64MultiArray, "/aichallenge/objects", self._obstacles_callback, 1)

        latching_qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self._path_constraints_pub = self.create_publisher(
            PathConstraints, "~/path_constraints", latching_qos)
        self._border_cells_pub = self.create_publisher(
            BorderCells, "~/border_cells", latching_qos)

    def _obstacles_callback(self, msg: Float64MultiArray) -> None:
        obstacles_updated = (self._last_obstacles_msgs_raw != msg.data) and (len(msg.data) > 0)
        if obstacles_updated:
            self._last_obstacles_msgs_raw = msg.data
            self._obstacles = []
            for i in range(0, len(msg.data), 4):
                x = msg.data[i]
                y = msg.data[i + 1]
                self._obstacles.append(Obstacle(cx=x, cy=y, radius=self._cfg.obstacles.radius)) # type: ignore
            # NOTE: This flag should be set to True only after the obstacles are updated
            self._obstacles_updated = True

    def _initialize(self) -> None:
        def create_map() -> Map:
            return Map(self.in_pkg_share(self._cfg.map.yaml_path)) # type: ignore

        def create_ref_path(map: Map) -> ReferencePath:
            cfg_ref_path = self._cfg.reference_path # type: ignore

            is_ref_path_given = cfg_ref_path.csv_path != "" # type: ignore
            if is_ref_path_given:
                print("Using given reference path")
                wp_x, wp_y, _, _ = load_ref_path(self.in_pkg_share(self._cfg.reference_path.csv_path)) # type: ignore
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
                wp_x, wp_y = load_waypoints(self.in_pkg_share(self._cfg.waypoints.csv_path)) # type: ignore

                return ReferencePath(
                    map,
                    wp_x,
                    wp_y,
                    cfg_ref_path.resolution,
                    cfg_ref_path.smoothing_distance,
                    cfg_ref_path.max_width,
                    cfg_ref_path.circular)


        def create_obstacles() -> List[Obstacle]:
            use_csv_obstacles = self._cfg.obstacles.csv_path != "" # type: ignore
            if use_csv_obstacles:
                obstacles_file_path = self.in_pkg_share(self._cfg.obstacles.csv_path) # type: ignore
                obs_x, obs_y = load_waypoints(obstacles_file_path)
                obstacles = []
                for cx, cy in zip(obs_x, obs_y):
                    obstacles.append(Obstacle(cx=cx, cy=cy, radius=self._cfg.obstacles.radius)) # type: ignore
                self._obstacle_manager = ObstacleManager(self._map, obstacles)
                return obstacles
            else:
                return []

        def create_car(ref_path: ReferencePath) -> BicycleModel:
            cfg_model = self._cfg.bicycle_model # type: ignore
            return BicycleModel(
                ref_path,
                cfg_model.length,
                cfg_model.width,
                1.0 / self._cfg.mpc.control_rate) # type: ignore

        def create_mpc(car: BicycleModel) -> Tuple[MPCConfig, MPC]:
            cfg_mpc = self._cfg.mpc # type: ignore
            mpc_cfg = MPCConfig(
                cfg_mpc.N,
                sparse.diags(cfg_mpc.Q),
                sparse.diags(cfg_mpc.R),
                sparse.diags(cfg_mpc.QN),
                kmh_to_m_per_sec(self.BUG_VEL if self.USE_BUG_ACC else cfg_mpc.v_max),
                cfg_mpc.a_min,
                cfg_mpc.a_max,
                cfg_mpc.ay_max,
                np.deg2rad(cfg_mpc.delta_max_deg),
                cfg_mpc.control_rate)

            state_constraints = {
                "xmin": np.array([-np.inf, -np.inf, -np.inf]),
                "xmax": np.array([np.inf, np.inf, np.inf])}
            input_constraints = {
                "umin": np.array([0.0, -np.tan(mpc_cfg.delta_max) / car.length]),
                "umax": np.array([mpc_cfg.v_max, np.tan(mpc_cfg.delta_max) / car.length])}
            mpc = MPC(
                car,
                mpc_cfg.N,
                mpc_cfg.Q,
                mpc_cfg.R,
                mpc_cfg.QN,
                state_constraints,
                input_constraints,
                mpc_cfg.ay_max,
                True,
                True)
            return mpc_cfg, mpc

        def compute_speed_profile(car: BicycleModel, mpc_config: MPCConfig) -> None:
            speed_profile_constraints = {
                "a_min": mpc_config.a_min, "a_max": mpc_config.a_max,
                "v_min": 0.0, "v_max": mpc_config.v_max, "ay_max": mpc_config.ay_max}
            car.reference_path.compute_speed_profile(speed_profile_constraints)

        self._map = create_map()
        self._reference_path = create_ref_path(self._map)
        self._obstacles = create_obstacles()
        self._car = create_car(self._reference_path)
        self._mpc_cfg, self._mpc = create_mpc(self._car)
        compute_speed_profile(self._car, self._mpc_cfg)

        self._trajectory: Optional[Trajectory] = None

        # Obstacles
        self._use_obstacles_topic = self._obstacles == []
        self._obstacles_updated = False
        self._last_obstacles_msgs_raw = None

    def run(self) -> None:

        self._path_constraints = PathConstraints()
        self._path_constraints.cols = self._mpc_cfg.N
        self._path_constraints.rows = self._reference_path.n_waypoints - 1

        border_cells = BorderCells()
        border_cells.cols = self._mpc_cfg.N
        border_cells.rows = self._reference_path.n_waypoints - 1

        pose = None

        rate = self.create_rate(0.5)
        while rclpy.ok():
            self._path_constraints.upper_bounds = []
            self._path_constraints.lower_bounds = []
            border_cells.dynamic_upper_bounds = []
            border_cells.dynamic_lower_bounds = []
            for wp_id in range(self._reference_path.n_waypoints-1):

                if self._obstacles_updated:
                    self._obstacles_updated = False
                    self._map.reset_map()
                    self._map.add_obstacles(self._obstacles)
                    self._reference_path.reset_dynamic_constraints()

                ub_hor, lb_hor, border_cells_hor_sm = self._car.reference_path.update_path_constraints(
                    wp_id + 1, pose, self._mpc_cfg.N,
                    self._car.length, self._car.width, self._car.safety_margin
                )
                ub_pw, lb_pw = np.array(border_cells_hor_sm[1])
                pose = (np.array(ub_pw) + np.array(lb_pw)) / 2.

                self._path_constraints.upper_bounds.extend(ub_hor)
                self._path_constraints.lower_bounds.extend(lb_hor)
                # if wp_id == 0:
                #     print("-------------")
                #     print(border_cells_hor_sm[:,0])
                #     print("-------------")
                border_cells.dynamic_upper_bounds.extend(border_cells_hor_sm[:,0].reshape(-1))
                border_cells.dynamic_lower_bounds.extend(border_cells_hor_sm[:,1].reshape(-1))
            self._path_constraints_pub.publish(self._path_constraints)
            self._border_cells_pub.publish(border_cells)
            rate.sleep()

    @classmethod
    def in_pkg_share(cls, file_path: str) -> str:
        return cls.PKG_PATH + file_path
