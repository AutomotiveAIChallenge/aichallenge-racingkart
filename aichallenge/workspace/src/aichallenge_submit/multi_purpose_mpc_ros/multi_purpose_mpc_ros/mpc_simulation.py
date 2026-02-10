#!/usr/bin/env python3

import sys
from typing import List, Optional
import copy
import matplotlib.pyplot as plt
import numpy as np

# ROS 2
import rclpy
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Quaternion, Pose2D, Point

# Project
from multi_purpose_mpc_ros.core.map import Map, Obstacle
from multi_purpose_mpc_ros.core.MPC import MPC
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.mpc_controller import MPCController, odom_to_pose_2d
from multi_purpose_mpc_ros.simulation_logger import SimulationLogger
from multi_purpose_mpc_ros.obstacle_manager import ObstacleManager


class MPCSimulation:
    def __init__(self, controller: MPCController):
        self._controller = controller

    def run(self):
        SHOW_SIM_ANIMATION = True
        SHOW_PLOT_ANIMATION = True
        PLOT_RESULTS = True
        ANIMATION_INTERVAL = 5
        PRINT_INTERVAL = 0
        MAX_LAPS = 6

        INIT_POSE_X = 89632.69740105038
        INIT_POSE_Y = 43128.52083434229
        INIT_POSE_ORIENTATION_X = 0.005626088670913863
        INIT_POSE_ORIENTATION_Y = -0.007700708218150311
        INIT_POSE_ORIENTATION_Z = 0.8743736422331192
        INIT_POSE_ORIENTATION_W = 0.4851595407566416

        mpc: MPC = self._controller._mpc
        map: Map = self._controller._map
        car: BicycleModel = mpc.model

        init_odom = Odometry()
        init_odom.pose.pose.position.x = INIT_POSE_X
        init_odom.pose.pose.position.y = INIT_POSE_Y
        init_odom.pose.pose.orientation.x = INIT_POSE_ORIENTATION_X
        init_odom.pose.pose.orientation.y = INIT_POSE_ORIENTATION_Y
        init_odom.pose.pose.orientation.z = INIT_POSE_ORIENTATION_Z
        init_odom.pose.pose.orientation.w = INIT_POSE_ORIENTATION_W

        pose = odom_to_pose_2d(init_odom)
        pose.x = 89648.61780001601
        pose.y = 43162.71519930651
        pose.theta = -np.pi / 4.

        car.update_states(pose.x, pose.y, pose.theta)

        def plot_reference_path(car):
            fig, ax = plt.subplots(1, 1)
            car.reference_path.show(ax)
            plt.show()
            sys.exit(1)
        # plot_reference_path(car)

        obstacles: List[Obstacle] = copy.deepcopy(self._controller._obstacles)
        obstacle_manager = ObstacleManager(map, obstacles)

        obstacle_manager.push_all_obstacles()
        obstacle_manager.update_map()

        logger = self._controller.get_logger()
        sim_logger = SimulationLogger(
            logger,
            car.temporal_state.x, car.temporal_state.y, SHOW_SIM_ANIMATION, SHOW_PLOT_ANIMATION, PLOT_RESULTS, ANIMATION_INTERVAL)

        t = 0.0
        loop = 0
        last_u = np.array([0.0, 0.0])
        current_laps = 1
        lap_times = [None] * (MAX_LAPS + 1)
        next_lap_start = False

        mpc_cfg = self._controller._mpc_cfg

        while rclpy.ok() and (not sim_logger.stop_requested()) and current_laps <= MAX_LAPS:
            if PRINT_INTERVAL != 0 and loop % PRINT_INTERVAL == 0:
                logger.info(f"t = {t}, s = {car.s}, x = {car.temporal_state.x}, y = {car.temporal_state.y}")
            loop += 1

            # Get control signals
            u, _ = mpc.get_control()
            if len(u) == 0:
                self.get_logger().error("No control signal", throttle_duration_sec=1)
                u = [0.0, 0.0]
            else:
                # limit acceleration
                acc = (u[0] - last_u[0]) / car.Ts
                acc = np.clip(acc, mpc_cfg.a_min, mpc_cfg.a_max)
                u[0] = last_u[0] + acc * car.Ts

            last_u = u

            # Simulate car
            car.drive(u)

            # Increment simulation time
            t += car.Ts

            # Log states
            sim_logger.log(car, u, t)

            # Plot animation
            is_colliding = False
            sim_logger.plot_animation(t, loop, current_laps, lap_times, is_colliding, u, mpc, car)

            # Push next obstacle
            if loop % 50 == 0:
                # obstacle_manager.push_next_obstacle_random()
                obstacle_manager.push_all_obstacles()
                obstacle_manager.update_map()

                # circular == True のときは周回のために reference_path を定期的に更新する
                if self._controller._cfg.reference_path.circular:
                    car.update_reference_path(car.reference_path)

            # Check if a lap has been completed
            if (next_lap_start and car.s >= car.reference_path.length or next_lap_start and car.s < car.reference_path.length / 20.0):
                if len(lap_times) > 0:
                    valid_lap_times = [lap_time for lap_time in lap_times if lap_time is not None]
                    total_time = sum(valid_lap_times) if valid_lap_times != [] else 0.
                    lap_time = t - total_time
                else:
                    lap_time = t

                logger.info(f'Lap {current_laps} completed! Lap time: {lap_time} s')
                lap_times[current_laps] = lap_time
                current_laps += 1
                next_lap_start = False

            # LAPインクリメント直後にゴール付近WPを最近傍WPとして認識してしまうと、 s>=lengthとなり
            # 次の周回がすぐに終了したと判定されてしまう場合があるため、
            # 誤判定防止のために少しだけ余計に走行した後に次の周回が開始したと判定する
            if not next_lap_start and (car.reference_path.length / 10.0 < car.s and car.s < car.reference_path.length / 10.0 * 2.0):
                next_lap_start = True
                logger.info(f'Next lap start!')

        # show results
        sim_logger.show_results(current_laps, lap_times, car)
