#!/usr/bin/env python3
import copy
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import rclpy
import yaml
from autoware_auto_planning_msgs.msg import Trajectory, TrajectoryPoint
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from v2x_msgs.msg import V2XVehiclePositionArray
from visualization_msgs.msg import Marker, MarkerArray


@dataclass
class Candidate:
    offset: float
    points: List[TrajectoryPoint]
    cost: float
    min_obstacle_distance: float


@dataclass
class ObstacleState:
    vehicle_id: str
    stamp: float
    x: float
    y: float
    vx: float
    vy: float


class OccupancyGrid:
    def __init__(self, yaml_path: str, occupied_thresh: float) -> None:
        with open(yaml_path, "r") as file:
            metadata = yaml.safe_load(file)

        image_path = metadata["image"]
        if not os.path.isabs(image_path):
            image_path = os.path.join(os.path.dirname(yaml_path), image_path)

        image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if image is None:
            raise RuntimeError(f"failed to load occupancy image: {image_path}")
        if image.ndim == 3:
            image = image[:, :, 0]

        self.resolution = float(metadata["resolution"])
        self.origin_x = float(metadata["origin"][0])
        self.origin_y = float(metadata["origin"][1])
        self.height, self.width = image.shape[:2]

        normalized = image.astype(np.float32)
        if normalized.max() > 1.0:
            normalized /= 255.0
        threshold = float(metadata.get("occupied_thresh", occupied_thresh))
        self.free = normalized >= threshold

    def is_circle_free(self, x: float, y: float, radius_m: float) -> bool:
        cx, cy = self.world_to_map(x, y)
        radius_px = max(1, int(math.ceil(radius_m / self.resolution)))
        x0 = cx - radius_px
        x1 = cx + radius_px
        y0 = cy - radius_px
        y1 = cy + radius_px
        if x0 < 0 or y0 < 0 or x1 >= self.width or y1 >= self.height:
            return False

        patch = self.free[y0 : y1 + 1, x0 : x1 + 1]
        yy, xx = np.ogrid[-radius_px : radius_px + 1, -radius_px : radius_px + 1]
        mask = xx * xx + yy * yy <= radius_px * radius_px
        return bool(np.all(patch[mask]))

    def world_to_map(self, x: float, y: float) -> Tuple[int, int]:
        mx = int((x - self.origin_x) / self.resolution + 0.5)
        my = int((self.height - 1) - (y - self.origin_y) / self.resolution + 0.5)
        return mx, my


class V2XTracker:
    def __init__(self, v_max: float, jump_threshold: float) -> None:
        self._v_max = v_max
        self._jump_threshold = jump_threshold
        self._samples: Dict[str, Deque[Tuple[float, float, float]]] = {}
        self._states: Dict[str, ObstacleState] = {}

    def update(self, msg: V2XVehiclePositionArray) -> None:
        for vehicle in msg.vehicles:
            stamp = float(vehicle.header.stamp.sec) + float(vehicle.header.stamp.nanosec) * 1e-9
            x = float(vehicle.position.x)
            y = float(vehicle.position.y)
            samples = self._samples.setdefault(vehicle.vehicle_id, deque(maxlen=2))

            if samples and math.hypot(x - samples[-1][1], y - samples[-1][2]) > self._jump_threshold:
                samples.clear()
            samples.append((stamp, x, y))

            vx = 0.0
            vy = 0.0
            if len(samples) == 2:
                t0, x0, y0 = samples[0]
                t1, x1, y1 = samples[1]
                dt = t1 - t0
                if dt > 1e-3:
                    vx = (x1 - x0) / dt
                    vy = (y1 - y0) / dt
                    if math.hypot(vx, vy) > self._v_max:
                        vx = 0.0
                        vy = 0.0

            self._states[vehicle.vehicle_id] = ObstacleState(vehicle.vehicle_id, stamp, x, y, vx, vy)

    def active_states(self, now_sec: float, timeout_sec: float) -> List[ObstacleState]:
        return [state for state in self._states.values() if now_sec - state.stamp <= timeout_sec]


class V2XAvoidancePlannerNode(Node):
    """Frenet lattice local planner with occupancy-grid and V2X collision checks."""

    def __init__(self) -> None:
        super().__init__("v2x_avoidance_planner_node")
        self._declare_parameters()

        self._reference: Optional[Trajectory] = None
        self._ego: Optional[Odometry] = None
        self._last_offset = 0.0
        self._last_publish_wall_time = 0.0
        self._previous_output: Optional[Trajectory] = None
        self._previous_output_wall_time = 0.0
        self._tracker = V2XTracker(
            v_max=float(self._p("obstacle_v_max_mps")),
            jump_threshold=float(self._p("obstacle_jump_threshold_m")),
        )

        self._grid = OccupancyGrid(str(self._p("map_yaml_path")), occupied_thresh=0.65)

        trajectory_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        data_qos = QoSProfile(depth=1)

        self._subscriptions = [
            self.create_subscription(
                Trajectory,
                self._p("reference_trajectory_topic"),
                self._on_reference_trajectory,
                trajectory_qos,
            ),
            self.create_subscription(
                V2XVehiclePositionArray, self._p("v2x_topic"), self._on_v2x, data_qos
            ),
            self.create_subscription(Odometry, self._p("ego_odom_topic"), self._on_ego, data_qos),
        ]
        self._pub = self.create_publisher(Trajectory, self._p("output_trajectory_topic"), trajectory_qos)
        marker_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._marker_pub = self.create_publisher(MarkerArray, self._p("visualization_marker_topic"), marker_qos)

        timer_period = 1.0 / max(1.0, float(self._p("publish_rate_hz")))
        self._timer = self.create_timer(timer_period, self._on_timer)

        self.get_logger().info(
            "V2X avoidance planner ready: "
            f"reference={self._p('reference_trajectory_topic')}, "
            f"v2x={self._p('v2x_topic')}, output={self._p('output_trajectory_topic')}"
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("reference_trajectory_topic", "/planning/scenario_planning/reference_trajectory")
        self.declare_parameter("output_trajectory_topic", "/planning/scenario_planning/trajectory")
        self.declare_parameter(
            "visualization_marker_topic", "/planning/scenario_planning/v2x_avoidance_planner/markers"
        )
        self.declare_parameter("v2x_topic", "/v2x/vehicle_positions")
        self.declare_parameter("ego_odom_topic", "/localization/kinematic_state")
        self.declare_parameter(
            "map_yaml_path",
            "/aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/env/final_ver3/occupancy_grid_map.yaml",
        )
        self.declare_parameter("publish_rate_hz", 5.0)
        self.declare_parameter("horizon_points", 90)
        self.declare_parameter("transition_points", 12)
        self.declare_parameter("candidate_offsets_m", [0.0, -0.5, 0.5, -1.0, 1.0, -1.5, 1.5])
        self.declare_parameter("return_to_reference", True)
        self.declare_parameter("initial_offset_ratio", 0.8)
        self.declare_parameter("emergency_lateral_escape_distance_m", 6.0)
        self.declare_parameter("emergency_lateral_offsets_m", [-1.5, 1.5, -1.0, 1.0])
        self.declare_parameter("reuse_previous_trajectory", True)
        self.declare_parameter("previous_trajectory_reuse_duration_sec", 2.0)
        self.declare_parameter("previous_trajectory_min_offset_m", 0.2)
        self.declare_parameter("min_output_velocity_mps", 0.0)
        self.declare_parameter("wheel_base_m", 1.087)
        self.declare_parameter("max_steering_angle_rad", 0.64)
        self.declare_parameter("max_lateral_accel_mps2", 3.0)
        self.declare_parameter("max_curvature_rate", 0.8)
        self.declare_parameter("min_velocity_mps", 1.0)
        self.declare_parameter("reduce_velocity_for_curvature", True)
        self.declare_parameter("ego_radius_m", 0.55)
        self.declare_parameter("wall_margin_m", 0.15)
        self.declare_parameter("wall_check_lateral_offsets_m", [-0.45, 0.0, 0.45])
        self.declare_parameter("max_wall_collision_points", 0)
        self.declare_parameter("obstacle_radius_m", 1.2)
        self.declare_parameter("obstacle_margin_m", 0.0)
        self.declare_parameter("use_spatial_obstacle_check", True)
        self.declare_parameter("obstacle_timeout_sec", 2.5)
        self.declare_parameter("obstacle_v_max_mps", 30.0)
        self.declare_parameter("obstacle_jump_threshold_m", 8.0)
        self.declare_parameter("offset_cost_weight", 1.0)
        self.declare_parameter("smoothness_cost_weight", 0.35)
        self.declare_parameter("obstacle_cost_weight", 1.0)
        self.declare_parameter("reuse_previous_offset_cost_weight", 0.1)
        self.declare_parameter("publish_reference_when_blocked", True)
        self.declare_parameter("publish_stop_when_blocked", False)
        self.declare_parameter("stop_buffer_points", 4)
        self.declare_parameter("marker_z_offset_m", 0.3)

    def _p(self, name: str):
        return self.get_parameter(name).value

    def _on_reference_trajectory(self, msg: Trajectory) -> None:
        self._reference = msg
        self._try_publish_plan()

    def _on_v2x(self, msg: V2XVehiclePositionArray) -> None:
        self._tracker.update(msg)
        self._try_publish_plan()

    def _on_ego(self, msg: Odometry) -> None:
        self._ego = msg
        self._try_publish_plan()

    def _on_timer(self) -> None:
        self._try_publish_plan(force=True)

    def _try_publish_plan(self, force: bool = False) -> None:
        now_wall = time.monotonic()
        min_period = 1.0 / max(1.0, float(self._p("publish_rate_hz")))
        if not force and now_wall - self._last_publish_wall_time < min_period:
            return

        if self._reference is None or not self._reference.points:
            return

        now_sec = self.get_clock().now().nanoseconds * 1e-9
        obstacles = self._tracker.active_states(now_sec, float(self._p("obstacle_timeout_sec")))
        if self._ego is None:
            self._publish_markers(self._reference, None, 0, obstacles)
            self._last_publish_wall_time = now_wall
            return

        start_index = self._closest_reference_index(self._reference, self._ego)
        planning_base = self._planning_base_trajectory(self._reference, start_index, now_wall)

        if not obstacles:
            planning_base.header.stamp = self.get_clock().now().to_msg()
            self._last_offset = 0.0
            self._previous_output = None
            self._pub.publish(planning_base)
            self._publish_markers(self._reference, None, start_index, obstacles)
            self._last_publish_wall_time = now_wall
            return

        candidate = self._select_candidate(planning_base, start_index, obstacles)
        if candidate is None:
            if bool(self._p("publish_stop_when_blocked")):
                stop_index = self._stop_index_for_obstacles(planning_base, start_index, obstacles)
                output = self._stopped_trajectory(planning_base, stop_index)
                self._raise_nonzero_velocities(output, start_index, stop_index)
                output.header.stamp = self.get_clock().now().to_msg()
                self._pub.publish(output)
                self._publish_markers(self._reference, None, start_index, obstacles)
                self._last_publish_wall_time = now_wall
            elif bool(self._p("publish_reference_when_blocked")):
                output = copy.deepcopy(planning_base)
                self._raise_nonzero_velocities(output, 0)
                output.header.stamp = self.get_clock().now().to_msg()
                self._previous_output = copy.deepcopy(output)
                self._previous_output_wall_time = now_wall
                self._pub.publish(output)
                self._publish_markers(self._reference, None, start_index, obstacles)
                self._last_publish_wall_time = now_wall
            self.get_logger().warn("No collision-free V2X avoidance candidate", throttle_duration_sec=1.0)
            return

        output = copy.deepcopy(planning_base)
        output.header.stamp = self.get_clock().now().to_msg()
        end_index = min(len(output.points), start_index + len(candidate.points))
        output.points[start_index:end_index] = candidate.points[: end_index - start_index]
        self._raise_nonzero_velocities(output, 0)
        self._last_offset = candidate.offset
        self._previous_output = copy.deepcopy(output)
        self._previous_output_wall_time = now_wall
        self._pub.publish(output)
        self._publish_markers(self._reference, candidate, start_index, obstacles)
        self._last_publish_wall_time = now_wall

    def _planning_base_trajectory(
        self, reference: Trajectory, start_index: int, now_wall: float
    ) -> Trajectory:
        if not bool(self._p("reuse_previous_trajectory")) or self._previous_output is None:
            return reference
        if now_wall - self._previous_output_wall_time > float(self._p("previous_trajectory_reuse_duration_sec")):
            return reference
        if len(self._previous_output.points) != len(reference.points):
            return reference

        max_offset = self._max_deviation_from_reference(self._previous_output, reference, start_index)
        if max_offset < float(self._p("previous_trajectory_min_offset_m")):
            return reference
        return self._previous_output

    @staticmethod
    def _max_deviation_from_reference(
        trajectory: Trajectory, reference: Trajectory, start_index: int
    ) -> float:
        end_index = min(len(reference.points), start_index + 90)
        if end_index <= start_index:
            return 0.0
        return max(
            math.hypot(
                trajectory.points[index].pose.position.x - reference.points[index].pose.position.x,
                trajectory.points[index].pose.position.y - reference.points[index].pose.position.y,
            )
            for index in range(start_index, end_index)
        )

    def _select_candidate(
        self, reference: Trajectory, start_index: int, obstacles: Sequence[ObstacleState]
    ) -> Optional[Candidate]:
        candidates = []
        for offset in self._candidate_offsets():
            candidate = self._build_candidate(reference, start_index, offset, obstacles)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate.cost)

    def _build_candidate(
        self,
        reference: Trajectory,
        start_index: int,
        offset: float,
        obstacles: Sequence[ObstacleState],
    ) -> Optional[Candidate]:
        horizon = int(self._p("horizon_points"))
        transition = max(1, int(self._p("transition_points")))
        end_index = min(len(reference.points), start_index + horizon)
        if end_index <= start_index:
            return None

        shifted_points: List[TrajectoryPoint] = []
        min_obstacle_distance = float("inf")
        wall_radius = float(self._p("ego_radius_m")) + float(self._p("wall_margin_m"))
        max_wall_collision_points = int(self._p("max_wall_collision_points"))
        wall_collision_points = 0
        collision_radius = float(self._p("obstacle_radius_m")) + float(self._p("obstacle_margin_m"))
        cumulative_time = 0.0
        previous_x = reference.points[start_index].pose.position.x
        previous_y = reference.points[start_index].pose.position.y

        for i in range(start_index, end_index):
            point = copy.deepcopy(reference.points[i])
            normal_x, normal_y = self._normal_at(reference.points, i)
            local_index = i - start_index
            local_horizon = end_index - start_index
            smooth_offset = offset * self._offset_profile(local_index, local_horizon, transition)

            x = float(point.pose.position.x) + normal_x * smooth_offset
            y = float(point.pose.position.y) + normal_y * smooth_offset
            point.pose.position.x = x
            point.pose.position.y = y

            if not self._course_footprint_is_free(x, y, normal_x, normal_y, wall_radius):
                wall_collision_points += 1
                if wall_collision_points > max_wall_collision_points:
                    return None

            if i > start_index:
                ds = math.hypot(x - previous_x, y - previous_y)
                velocity = max(1.0, float(point.longitudinal_velocity_mps))
                cumulative_time += ds / velocity
            previous_x = x
            previous_y = y

            obstacle_distance = self._min_obstacle_distance(x, y, cumulative_time, obstacles)
            min_obstacle_distance = min(min_obstacle_distance, obstacle_distance)
            if obstacle_distance < collision_radius:
                return None

            shifted_points.append(point)

        if not self._apply_dynamics_limits(shifted_points):
            return None

        cost = self._candidate_cost(offset, shifted_points, min_obstacle_distance)
        return Candidate(offset=offset, points=shifted_points, cost=cost, min_obstacle_distance=min_obstacle_distance)

    def _course_footprint_is_free(
        self, x: float, y: float, normal_x: float, normal_y: float, radius: float
    ) -> bool:
        for lateral_offset in self._p("wall_check_lateral_offsets_m"):
            check_x = x + normal_x * float(lateral_offset)
            check_y = y + normal_y * float(lateral_offset)
            if not self._grid.is_circle_free(check_x, check_y, radius):
                return False
        return True

    def _candidate_cost(
        self, offset: float, points: Sequence[TrajectoryPoint], min_obstacle_distance: float
    ) -> float:
        offset_cost = abs(offset) * float(self._p("offset_cost_weight"))
        reuse_cost = abs(offset - self._last_offset) * float(self._p("reuse_previous_offset_cost_weight"))
        smoothness_cost = self._heading_change_cost(points) * float(self._p("smoothness_cost_weight"))
        obstacle_cost = 0.0
        if math.isfinite(min_obstacle_distance):
            obstacle_cost = float(self._p("obstacle_cost_weight")) / max(0.1, min_obstacle_distance)
        return offset_cost + reuse_cost + smoothness_cost + obstacle_cost

    def _stopped_trajectory(self, reference: Trajectory, start_index: int) -> Trajectory:
        output = copy.deepcopy(reference)
        for index, point in enumerate(output.points):
            if index >= start_index:
                point.longitudinal_velocity_mps = 0.0
                point.acceleration_mps2 = min(float(point.acceleration_mps2), -2.0)
        return output

    def _raise_nonzero_velocities(
        self, trajectory: Trajectory, start_index: int, end_index: Optional[int] = None
    ) -> None:
        min_velocity = float(self._p("min_output_velocity_mps"))
        end = len(trajectory.points) if end_index is None else min(end_index, len(trajectory.points))
        for point in trajectory.points[start_index:end]:
            if point.longitudinal_velocity_mps > 0.0:
                point.longitudinal_velocity_mps = max(float(point.longitudinal_velocity_mps), min_velocity)

    def _stop_index_for_obstacles(
        self, reference: Trajectory, start_index: int, obstacles: Sequence[ObstacleState]
    ) -> int:
        if not obstacles:
            return start_index

        horizon_end = min(len(reference.points), start_index + int(self._p("horizon_points")))
        if horizon_end <= start_index:
            return start_index

        best_index = horizon_end - 1
        best_distance = float("inf")
        for obstacle in obstacles:
            for index in range(start_index, horizon_end):
                point = reference.points[index].pose.position
                distance = math.hypot(point.x - obstacle.x, point.y - obstacle.y)
                if distance < best_distance:
                    best_distance = distance
                    best_index = index

        stop_buffer = max(0, int(self._p("stop_buffer_points")))
        return max(start_index, best_index - stop_buffer)

    def _apply_dynamics_limits(self, points: List[TrajectoryPoint]) -> bool:
        if len(points) < 3:
            return True

        curvatures = self._curvatures(points)
        wheel_base = float(self._p("wheel_base_m"))
        max_curvature = math.tan(float(self._p("max_steering_angle_rad"))) / max(wheel_base, 1e-3)
        max_lat_accel = float(self._p("max_lateral_accel_mps2"))
        min_velocity = float(self._p("min_velocity_mps"))
        reduce_velocity = bool(self._p("reduce_velocity_for_curvature"))

        for index, curvature in enumerate(curvatures):
            if abs(curvature) > max_curvature:
                return False

            point = points[index]
            velocity = max(min_velocity, float(point.longitudinal_velocity_mps))
            if abs(curvature) > 1e-6:
                feasible_velocity = math.sqrt(max_lat_accel / abs(curvature))
                if velocity * velocity * abs(curvature) > max_lat_accel:
                    if not reduce_velocity:
                        return False
                    point.longitudinal_velocity_mps = max(min_velocity, min(velocity, feasible_velocity))

        if not self._curvature_rate_is_feasible(points, curvatures):
            return False

        self._update_path_orientation(points)
        return True

    def _curvature_rate_is_feasible(
        self, points: Sequence[TrajectoryPoint], curvatures: Sequence[float]
    ) -> bool:
        max_curvature_rate = float(self._p("max_curvature_rate"))
        for index in range(1, len(curvatures)):
            p0 = points[index - 1].pose.position
            p1 = points[index].pose.position
            ds = max(1e-3, math.hypot(p1.x - p0.x, p1.y - p0.y))
            if abs(curvatures[index] - curvatures[index - 1]) / ds > max_curvature_rate:
                return False
        return True

    @staticmethod
    def _curvatures(points: Sequence[TrajectoryPoint]) -> List[float]:
        curvatures = [0.0] * len(points)
        for index in range(1, len(points) - 1):
            p0 = points[index - 1].pose.position
            p1 = points[index].pose.position
            p2 = points[index + 1].pose.position
            a = math.hypot(p1.x - p0.x, p1.y - p0.y)
            b = math.hypot(p2.x - p1.x, p2.y - p1.y)
            c = math.hypot(p2.x - p0.x, p2.y - p0.y)
            denom = a * b * c
            if denom < 1e-6:
                continue
            cross = (p1.x - p0.x) * (p2.y - p0.y) - (p1.y - p0.y) * (p2.x - p0.x)
            curvatures[index] = 2.0 * cross / denom
        if len(points) >= 3:
            curvatures[0] = curvatures[1]
            curvatures[-1] = curvatures[-2]
        return curvatures

    @staticmethod
    def _update_path_orientation(points: Sequence[TrajectoryPoint]) -> None:
        for index, point in enumerate(points):
            if index < len(points) - 1:
                p0 = point.pose.position
                p1 = points[index + 1].pose.position
            else:
                p0 = points[index - 1].pose.position
                p1 = point.pose.position
            yaw = math.atan2(p1.y - p0.y, p1.x - p0.x)
            point.pose.orientation.x = 0.0
            point.pose.orientation.y = 0.0
            point.pose.orientation.z = math.sin(yaw * 0.5)
            point.pose.orientation.w = math.cos(yaw * 0.5)

    @staticmethod
    def _quintic_smoothstep(alpha: float) -> float:
        alpha = min(1.0, max(0.0, alpha))
        return alpha**3 * (10.0 - 15.0 * alpha + 6.0 * alpha * alpha)

    def _offset_profile(self, index: int, horizon: int, transition: int) -> float:
        if horizon <= 1:
            return 0.0
        initial_ratio = min(1.0, max(0.0, float(self._p("initial_offset_ratio"))))
        if index < transition:
            alpha = initial_ratio + (1.0 - initial_ratio) * index / float(transition)
            return self._quintic_smoothstep(alpha)
        if bool(self._p("return_to_reference")) and index > horizon - transition:
            remaining = max(0, horizon - 1 - index)
            return self._quintic_smoothstep(remaining / float(transition))
        return 1.0

    @staticmethod
    def _heading_change_cost(points: Sequence[TrajectoryPoint]) -> float:
        if len(points) < 3:
            return 0.0
        cost = 0.0
        previous_yaw = None
        for i in range(1, len(points)):
            p0 = points[i - 1].pose.position
            p1 = points[i].pose.position
            yaw = math.atan2(p1.y - p0.y, p1.x - p0.x)
            if previous_yaw is not None:
                cost += abs(math.atan2(math.sin(yaw - previous_yaw), math.cos(yaw - previous_yaw)))
            previous_yaw = yaw
        return cost

    def _min_obstacle_distance(self, x: float, y: float, dt: float, obstacles: Sequence[ObstacleState]) -> float:
        if not obstacles:
            return float("inf")
        distances = []
        for obs in obstacles:
            distances.append(math.hypot(x - (obs.x + obs.vx * dt), y - (obs.y + obs.vy * dt)))
            if bool(self._p("use_spatial_obstacle_check")):
                distances.append(math.hypot(x - obs.x, y - obs.y))
        return min(distances)

    def _candidate_offsets(self) -> List[float]:
        if self._ego is not None and self._near_obstacle(self._ego, self._tracker._states.values()):
            return [float(value) for value in self._p("emergency_lateral_offsets_m")]
        values = [float(value) for value in self._p("candidate_offsets_m")]
        return sorted(values, key=lambda value: abs(value - self._last_offset))

    def _near_obstacle(self, ego: Odometry, obstacles: Sequence[ObstacleState]) -> bool:
        threshold = float(self._p("emergency_lateral_escape_distance_m"))
        ego_x = ego.pose.pose.position.x
        ego_y = ego.pose.pose.position.y
        return any(math.hypot(ego_x - obstacle.x, ego_y - obstacle.y) <= threshold for obstacle in obstacles)

    def _publish_markers(
        self,
        reference: Trajectory,
        selected: Optional[Candidate],
        start_index: int,
        obstacles: Sequence[ObstacleState],
    ) -> None:
        markers = MarkerArray()
        clear = Marker()
        clear.action = Marker.DELETEALL
        markers.markers.append(clear)

        stamp = self.get_clock().now().to_msg()
        frame_id = reference.header.frame_id or "map"
        horizon = int(self._p("horizon_points"))
        end_index = min(len(reference.points), start_index + horizon)
        reference_points = reference.points[start_index:end_index]

        markers.markers.append(
            self._line_marker(
                marker_id=1,
                namespace="reference_trajectory",
                frame_id=frame_id,
                stamp=stamp,
                points=reference_points,
                color=(0.55, 0.55, 0.55, 0.7),
                scale=0.12,
            )
        )

        if selected is not None:
            markers.markers.append(
                self._line_marker(
                    marker_id=2,
                    namespace="selected_v2x_avoidance_trajectory",
                    frame_id=frame_id,
                    stamp=stamp,
                    points=selected.points,
                    color=(0.0, 1.0, 0.0, 0.95),
                    scale=0.22,
                )
            )

        for index, obstacle in enumerate(obstacles):
            markers.markers.append(self._obstacle_marker(index + 100, frame_id, stamp, obstacle))

        self._marker_pub.publish(markers)

    def _line_marker(
        self,
        marker_id: int,
        namespace: str,
        frame_id: str,
        stamp,
        points: Sequence[TrajectoryPoint],
        color: Tuple[float, float, float, float],
        scale: float,
    ) -> Marker:
        marker = Marker()
        marker.header.frame_id = frame_id
        marker.header.stamp = stamp
        marker.ns = namespace
        marker.id = marker_id
        marker.type = Marker.LINE_STRIP
        marker.action = Marker.ADD
        marker.pose.orientation.w = 1.0
        marker.scale.x = scale
        marker.color.r = color[0]
        marker.color.g = color[1]
        marker.color.b = color[2]
        marker.color.a = color[3]
        z_offset = float(self._p("marker_z_offset_m"))
        for trajectory_point in points:
            point = trajectory_point.pose.position
            marker_point = copy.deepcopy(point)
            marker_point.z += z_offset
            marker.points.append(marker_point)
        return marker

    def _obstacle_marker(self, marker_id: int, frame_id: str, stamp, obstacle: ObstacleState) -> Marker:
        marker = Marker()
        marker.header.frame_id = frame_id
        marker.header.stamp = stamp
        marker.ns = "v2x_obstacles"
        marker.id = marker_id
        marker.type = Marker.SPHERE
        marker.action = Marker.ADD
        marker.pose.position.x = obstacle.x
        marker.pose.position.y = obstacle.y
        marker.pose.position.z = float(self._p("marker_z_offset_m"))
        marker.pose.orientation.w = 1.0
        diameter = 2.0 * (float(self._p("obstacle_radius_m")) + float(self._p("obstacle_margin_m")))
        marker.scale.x = diameter
        marker.scale.y = diameter
        marker.scale.z = 0.4
        marker.color.r = 1.0
        marker.color.g = 0.0
        marker.color.b = 0.0
        marker.color.a = 0.55
        return marker

    @staticmethod
    def _closest_reference_index(reference: Trajectory, ego: Odometry) -> int:
        ego_x = ego.pose.pose.position.x
        ego_y = ego.pose.pose.position.y
        distances = [
            (point.pose.position.x - ego_x) ** 2 + (point.pose.position.y - ego_y) ** 2
            for point in reference.points
        ]
        return int(np.argmin(distances))

    @staticmethod
    def _normal_at(points: Sequence[TrajectoryPoint], index: int) -> Tuple[float, float]:
        prev_index = max(0, index - 1)
        next_index = min(len(points) - 1, index + 1)
        prev_point = points[prev_index].pose.position
        next_point = points[next_index].pose.position
        yaw = math.atan2(next_point.y - prev_point.y, next_point.x - prev_point.x)
        return -math.sin(yaw), math.cos(yaw)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = V2XAvoidancePlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
