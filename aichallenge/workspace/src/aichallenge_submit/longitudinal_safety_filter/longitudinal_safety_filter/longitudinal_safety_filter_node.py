#!/usr/bin/env python3
import copy
import json
import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import rclpy
from autoware_auto_control_msgs.msg import AckermannControlCommand
from autoware_auto_planning_msgs.msg import Trajectory
from geometry_msgs.msg import Point
from nav_msgs.msg import Odometry, Path
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import ColorRGBA
from std_msgs.msg import String
from v2x_msgs.msg import V2XVehiclePositionArray
from visualization_msgs.msg import Marker, MarkerArray


@dataclass
class ObstacleState:
    vehicle_id: str
    stamp: float
    x: float
    y: float
    vx: float
    vy: float


@dataclass
class SafetyDecision:
    state: str = "CLEAR"
    reason: str = "clear"
    front_distance_m: float = math.inf
    front_distance_current_m: float = math.inf
    ttc_s: float = math.inf
    min_distance_m: float = math.inf
    predicted_collision_distance_m: float = math.inf
    warning_distance_m: float = math.inf
    brake_distance_m: float = math.inf
    speed_limit_mps: float = math.inf
    acceleration_limit_mps2: float = math.inf
    risk_class: str = "SAFE"
    prediction_source: str = "none"
    opponent_prediction_source: str = "none"
    opponent_observed_speed_mps: float = math.inf
    relative_speed_mps: float = math.inf
    follow_speed_limit_mps: float = math.inf
    follow_distance_m: float = math.inf


@dataclass
class EgoPredictionPoint:
    t: float
    x: float
    y: float
    yaw: float
    distance_m: float
    source: str
    speed_mps: float = 0.0


@dataclass
class RiskMetrics:
    front_distance_current_m: float = math.inf
    predicted_ttc_s: float = math.inf
    min_predicted_distance_m: float = math.inf
    predicted_collision_distance_m: float = math.inf
    prediction_source: str = "none"
    opponent_prediction_source: str = "none"
    collision_x: float = math.inf
    collision_y: float = math.inf
    opponent_observed_speed_mps: float = math.inf
    relative_speed_mps: float = math.inf
    follow_speed_limit_mps: float = math.inf
    follow_distance_m: float = math.inf


class V2XTracker:
    def __init__(
        self,
        v_max_mps: float,
        jump_threshold_m: float,
        history_size: int,
        min_velocity_dt_s: float,
        velocity_smoothing_gain: float,
    ) -> None:
        self._v_max_mps = v_max_mps
        self._jump_threshold_m = jump_threshold_m
        self._min_velocity_dt_s = min_velocity_dt_s
        self._velocity_smoothing_gain = min(max(velocity_smoothing_gain, 0.0), 1.0)
        self._samples: Dict[str, Deque[Tuple[float, float, float]]] = {}
        self._states: Dict[str, ObstacleState] = {}
        self._history_size = max(2, history_size)

    def update(self, msg: V2XVehiclePositionArray, receive_stamp: float) -> None:
        for vehicle in msg.vehicles:
            measurement_stamp = float(vehicle.header.stamp.sec) + float(vehicle.header.stamp.nanosec) * 1.0e-9
            if measurement_stamp <= 0.0:
                measurement_stamp = receive_stamp

            x = float(vehicle.position.x)
            y = float(vehicle.position.y)
            samples = self._samples.setdefault(vehicle.vehicle_id, deque(maxlen=self._history_size))
            if samples and math.hypot(x - samples[-1][1], y - samples[-1][2]) > self._jump_threshold_m:
                samples.clear()
            samples.append((measurement_stamp, x, y))

            vx, vy = self._estimate_velocity(vehicle.vehicle_id, samples)

            self._states[vehicle.vehicle_id] = ObstacleState(
                vehicle.vehicle_id, receive_stamp, x, y, vx, vy
            )

    def active_states(self, now_sec: float, timeout_s: float) -> List[ObstacleState]:
        return [state for state in self._states.values() if now_sec - state.stamp <= timeout_s]

    def _estimate_velocity(
        self, vehicle_id: str, samples: Deque[Tuple[float, float, float]]
    ) -> Tuple[float, float]:
        if len(samples) < 2:
            previous = self._states.get(vehicle_id)
            return (previous.vx, previous.vy) if previous is not None else (0.0, 0.0)

        t1, x1, y1 = samples[-1]
        velocity_samples: List[Tuple[float, float]] = []
        for t0, x0, y0 in list(samples)[:-1]:
            dt = t1 - t0
            if dt >= self._min_velocity_dt_s:
                vx = (x1 - x0) / dt
                vy = (y1 - y0) / dt
                if math.hypot(vx, vy) <= self._v_max_mps:
                    velocity_samples.append((vx, vy))

        if not velocity_samples:
            previous = self._states.get(vehicle_id)
            return (previous.vx, previous.vy) if previous is not None else (0.0, 0.0)

        raw_vx = sum(vx for vx, _ in velocity_samples) / len(velocity_samples)
        raw_vy = sum(vy for _, vy in velocity_samples) / len(velocity_samples)
        previous = self._states.get(vehicle_id)
        if previous is None:
            return raw_vx, raw_vy

        gain = self._velocity_smoothing_gain
        return (
            previous.vx + (raw_vx - previous.vx) * gain,
            previous.vy + (raw_vy - previous.vy) * gain,
        )


class LongitudinalSafetyFilterNode(Node):
    def __init__(self) -> None:
        super().__init__("longitudinal_safety_filter")
        self._declare_parameters()

        self._odom: Optional[Odometry] = None
        self._trajectory: Optional[Trajectory] = None
        self._mpc_prediction: Optional[Path] = None
        self._last_mpc_prediction_stamp: Optional[float] = None
        self._last_cmd_stamp: Optional[float] = None
        self._last_v2x_stamp: Optional[float] = None
        self._last_ego_prediction: List[EgoPredictionPoint] = []
        self._last_opponent_predictions: List[List[EgoPredictionPoint]] = []
        self._last_collision_point: Optional[Point] = None
        self._tracker = V2XTracker(
            v_max_mps=float(self._p("obstacle_v_max_mps")),
            jump_threshold_m=float(self._p("obstacle_jump_threshold_m")),
            history_size=int(self._p("obstacle_velocity_history_size")),
            min_velocity_dt_s=float(self._p("obstacle_min_velocity_dt_s")),
            velocity_smoothing_gain=float(self._p("obstacle_velocity_smoothing_gain")),
        )

        self.create_subscription(
            AckermannControlCommand,
            str(self._p("input_control_cmd_topic")),
            self._on_control_cmd,
            1,
        )
        self.create_subscription(Odometry, str(self._p("odom_topic")), self._on_odom, 1)
        self.create_subscription(
            V2XVehiclePositionArray, str(self._p("v2x_topic")), self._on_v2x, 1
        )
        trajectory_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            Trajectory, str(self._p("trajectory_topic")), self._on_trajectory, trajectory_qos
        )
        self.create_subscription(Path, str(self._p("ego_prediction_topic")), self._on_mpc_prediction, 1)

        self._pub = self.create_publisher(
            AckermannControlCommand, str(self._p("output_control_cmd_topic")), 1
        )
        self._status_pub = self.create_publisher(String, str(self._p("status_topic")), 1)
        self._marker_pub = self.create_publisher(MarkerArray, str(self._p("marker_topic")), 1)
        self._timer = self.create_timer(0.1, self._on_timer)

        self.get_logger().info(
            "longitudinal_safety_filter ready: "
            f"{self._p('input_control_cmd_topic')} -> {self._p('output_control_cmd_topic')}"
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("input_control_cmd_topic", "/control/command/control_cmd_raw")
        self.declare_parameter("output_control_cmd_topic", "/control/command/control_cmd")
        self.declare_parameter("odom_topic", "/localization/kinematic_state")
        self.declare_parameter("trajectory_topic", "/planning/scenario_planning/trajectory")
        self.declare_parameter("ego_prediction_topic", "/mpc/predicted_path")
        self.declare_parameter("v2x_topic", "/v2x/vehicle_positions")
        self.declare_parameter("status_topic", "/safety/longitudinal_state")
        self.declare_parameter("marker_topic", "/safety/longitudinal_debug/markers")
        self.declare_parameter("control_timeout_s", 0.3)
        self.declare_parameter("obstacle_timeout_s", 1.0)
        self.declare_parameter("use_mpc_prediction", True)
        self.declare_parameter("mpc_prediction_timeout_s", 0.5)
        self.declare_parameter("opponent_default_speed_mps", 5.0)
        self.declare_parameter("use_selected_trajectory_prediction", False)
        self.declare_parameter("trajectory_circular", True)
        self.declare_parameter("prediction_horizon_s", 8.0)
        self.declare_parameter("prediction_dt_s", 0.1)
        self.declare_parameter("ego_radius_m", 0.75)
        self.declare_parameter("obstacle_radius_m", 0.85)
        self.declare_parameter("corridor_half_width_m", 1.2)
        self.declare_parameter("avoid_with_slowdown_ttc_s", 4.0)
        self.declare_parameter("brake_ttc_s", 2.0)
        self.declare_parameter("comfortable_decel_mps2", 1.6)
        self.declare_parameter("emergency_decel_mps2", 2.5)
        self.declare_parameter("max_brake_decel_mps2", 2.5)
        self.declare_parameter("latency_margin_s", 0.3)
        self.declare_parameter("distance_margin_m", 3.0)
        self.declare_parameter("hard_stop_distance_m", 1.0)
        self.declare_parameter("min_race_speed_kmh", 5.0)
        self.declare_parameter("fail_slow_speed_kmh", 5.0)
        self.declare_parameter("no_odom_policy", "slow")
        self.declare_parameter("v2x_timeout_policy", "pass_through")
        self.declare_parameter("obstacle_v_max_mps", 30.0)
        self.declare_parameter("obstacle_jump_threshold_m", 8.0)
        self.declare_parameter("obstacle_velocity_history_size", 5)
        self.declare_parameter("obstacle_min_velocity_dt_s", 0.05)
        self.declare_parameter("obstacle_velocity_smoothing_gain", 0.5)
        self.declare_parameter("enable_following_speed_control", True)
        self.declare_parameter("follow_distance_m", 8.0)
        self.declare_parameter("follow_time_gap_s", 0.8)
        self.declare_parameter("relative_speed_allowance_mps", 0.3)
        self.declare_parameter("min_follow_speed_mps", 0.0)

    def _p(self, name: str):
        return self.get_parameter(name).value

    def _now_sec(self) -> float:
        now = self.get_clock().now()
        return float(now.nanoseconds) * 1.0e-9

    def _on_odom(self, msg: Odometry) -> None:
        self._odom = msg

    def _on_v2x(self, msg: V2XVehiclePositionArray) -> None:
        now_sec = self._now_sec()
        self._last_v2x_stamp = now_sec
        self._tracker.update(msg, now_sec)

    def _on_trajectory(self, msg: Trajectory) -> None:
        self._trajectory = msg

    def _on_mpc_prediction(self, msg: Path) -> None:
        self._mpc_prediction = msg
        self._last_mpc_prediction_stamp = self._now_sec()

    def _on_timer(self) -> None:
        if self._last_cmd_stamp is None:
            return
        age = self._now_sec() - self._last_cmd_stamp
        if age > float(self._p("control_timeout_s")):
            self._publish_status(SafetyDecision(state="STALE", reason="control_timeout"), None, None)

    def _on_control_cmd(self, msg: AckermannControlCommand) -> None:
        now_sec = self._now_sec()
        self._last_cmd_stamp = now_sec
        decision = self._evaluate(now_sec)
        filtered = self._apply_decision(msg, decision)
        self._pub.publish(filtered)
        self._publish_status(decision, msg, filtered)
        self._publish_debug_markers(decision)

    def _evaluate(self, now_sec: float) -> SafetyDecision:
        if self._odom is None:
            return self._decision_for_fail_policy("no_odom", str(self._p("no_odom_policy")))

        ego = self._odom.pose.pose.position
        q = self._odom.pose.pose.orientation
        yaw = self._yaw_from_quaternion(q.x, q.y, q.z, q.w)
        speed = max(0.0, float(self._odom.twist.twist.linear.x))

        obstacles = self._tracker.active_states(now_sec, float(self._p("obstacle_timeout_s")))
        if not obstacles:
            self._last_ego_prediction = self._ego_prediction(float(ego.x), float(ego.y), yaw, speed)
            self._last_opponent_predictions = []
            self._last_collision_point = None
            timed_out = (
                self._last_v2x_stamp is None
                or now_sec - self._last_v2x_stamp > float(self._p("obstacle_timeout_s"))
            )
            if timed_out:
                return self._decision_for_fail_policy(
                    "v2x_timeout", str(self._p("v2x_timeout_policy"))
                )
            return SafetyDecision(state="CLEAR", reason="no_obstacle", risk_class="SAFE")

        metrics = self._evaluate_risk(float(ego.x), float(ego.y), yaw, speed, obstacles)
        return self._decision_from_metrics(speed, metrics)

    def _evaluate_risk(
        self,
        ego_x: float,
        ego_y: float,
        ego_yaw: float,
        speed: float,
        obstacles: List[ObstacleState],
    ) -> RiskMetrics:
        collision_distance = float(self._p("ego_radius_m")) + float(self._p("obstacle_radius_m"))
        corridor_half_width = float(self._p("corridor_half_width_m")) + collision_distance

        front_distance_current = self._current_front_distance(
            ego_x, ego_y, ego_yaw, obstacles, corridor_half_width
        )
        ego_prediction = self._ego_prediction(ego_x, ego_y, ego_yaw, speed)
        self._last_ego_prediction = ego_prediction
        self._last_opponent_predictions = []
        self._last_collision_point = None

        predicted_ttc = math.inf
        min_predicted_distance = math.inf
        predicted_collision_distance = math.inf
        opponent_source = "none"
        collision_x = math.inf
        collision_y = math.inf
        closest_opponent_speed = math.inf
        closest_relative_speed = math.inf
        follow_distance = math.inf
        follow_speed_limit = math.inf
        for obstacle in obstacles:
            opponent_prediction = self._opponent_prediction(obstacle, ego_prediction)
            self._last_opponent_predictions.append(opponent_prediction)
            if opponent_prediction:
                opponent_source = opponent_prediction[0].source
            for point, opponent_point in zip(ego_prediction, opponent_prediction):
                distance = math.hypot(opponent_point.x - point.x, opponent_point.y - point.y)
                if distance < min_predicted_distance:
                    min_predicted_distance = distance
                    closest_opponent_speed = opponent_point.speed_mps
                    closest_relative_speed = point.speed_mps - opponent_point.speed_mps
                    follow_distance = point.distance_m
                if distance <= collision_distance and math.isinf(predicted_ttc):
                    predicted_ttc = point.t
                    predicted_collision_distance = point.distance_m
                    collision_x = point.x
                    collision_y = point.y
                    self._last_collision_point = Point(x=point.x, y=point.y, z=0.9)

        if bool(self._p("enable_following_speed_control")) and math.isfinite(closest_relative_speed):
            follow_range = max(
                0.0,
                float(self._p("follow_distance_m")) + speed * max(0.0, float(self._p("follow_time_gap_s"))),
            )
            follow_corridor = collision_distance + max(0.0, float(self._p("corridor_half_width_m")))
            if (
                closest_relative_speed > 0.0
                and follow_distance <= follow_range
                and min_predicted_distance <= follow_corridor
            ):
                follow_speed_limit = max(
                    float(self._p("min_follow_speed_mps")),
                    closest_opponent_speed + max(0.0, float(self._p("relative_speed_allowance_mps"))),
                )

        return RiskMetrics(
            front_distance_current_m=front_distance_current,
            predicted_ttc_s=predicted_ttc,
            min_predicted_distance_m=min_predicted_distance,
            predicted_collision_distance_m=predicted_collision_distance,
            prediction_source=ego_prediction[0].source if ego_prediction else "none",
            opponent_prediction_source=opponent_source,
            collision_x=collision_x,
            collision_y=collision_y,
            opponent_observed_speed_mps=closest_opponent_speed,
            relative_speed_mps=closest_relative_speed,
            follow_speed_limit_mps=follow_speed_limit,
            follow_distance_m=follow_distance,
        )

    def _current_front_distance(
        self,
        ego_x: float,
        ego_y: float,
        ego_yaw: float,
        obstacles: List[ObstacleState],
        corridor_half_width: float,
    ) -> float:
        dir_x = math.cos(ego_yaw)
        dir_y = math.sin(ego_yaw)
        lat_x = -math.sin(ego_yaw)
        lat_y = math.cos(ego_yaw)
        front_distance = math.inf
        for obstacle in obstacles:
            rel_x = obstacle.x - ego_x
            rel_y = obstacle.y - ego_y
            forward = rel_x * dir_x + rel_y * dir_y
            lateral = abs(rel_x * lat_x + rel_y * lat_y)
            if forward > 0.0 and lateral <= corridor_half_width:
                front_distance = min(front_distance, forward)
        return front_distance

    def _ego_prediction(
        self, ego_x: float, ego_y: float, ego_yaw: float, speed: float
    ) -> List[EgoPredictionPoint]:
        horizon = float(self._p("prediction_horizon_s"))
        dt = max(0.05, float(self._p("prediction_dt_s")))
        step_count = max(1, int(math.ceil(horizon / dt)))
        if bool(self._p("use_mpc_prediction")) and self._mpc_prediction_is_fresh():
            return self._mpc_path_prediction(step_count, dt)
        if bool(self._p("use_selected_trajectory_prediction")) and self._trajectory_has_points():
            return self._trajectory_prediction(ego_x, ego_y, speed, step_count, dt)
        return self._straight_prediction(ego_x, ego_y, ego_yaw, speed, step_count, dt)

    def _mpc_prediction_is_fresh(self) -> bool:
        if self._mpc_prediction is None or len(self._mpc_prediction.poses) < 2:
            return False
        if self._last_mpc_prediction_stamp is None:
            return False
        return self._now_sec() - self._last_mpc_prediction_stamp <= float(self._p("mpc_prediction_timeout_s"))

    def _mpc_path_prediction(self, step_count: int, dt: float) -> List[EgoPredictionPoint]:
        assert self._mpc_prediction is not None
        poses = self._mpc_prediction.poses
        cumulative = [0.0]
        for i in range(1, len(poses)):
            prev = poses[i - 1].pose.position
            cur = poses[i].pose.position
            cumulative.append(cumulative[-1] + math.hypot(cur.x - prev.x, cur.y - prev.y))
        prediction: List[EgoPredictionPoint] = []
        for index, pose in enumerate(poses[: step_count + 1]):
            position = pose.pose.position
            if index > 0:
                segment_dt = max(1.0e-6, dt)
                point_speed = (cumulative[index] - cumulative[index - 1]) / segment_dt
            elif len(cumulative) > 1:
                point_speed = (cumulative[1] - cumulative[0]) / max(1.0e-6, dt)
            else:
                point_speed = 0.0
            if index + 1 < len(poses):
                next_position = poses[index + 1].pose.position
                yaw = math.atan2(next_position.y - position.y, next_position.x - position.x)
            elif index > 0:
                prev_position = poses[index - 1].pose.position
                yaw = math.atan2(position.y - prev_position.y, position.x - prev_position.x)
            else:
                yaw = 0.0
            prediction.append(
                EgoPredictionPoint(
                    t=index * dt,
                    x=float(position.x),
                    y=float(position.y),
                    yaw=yaw,
                    distance_m=cumulative[index],
                    source="mpc_prediction",
                    speed_mps=point_speed,
                )
            )
        return prediction

    def _trajectory_has_points(self) -> bool:
        return self._trajectory is not None and len(self._trajectory.points) >= 2

    def _straight_prediction(
        self,
        ego_x: float,
        ego_y: float,
        ego_yaw: float,
        speed: float,
        step_count: int,
        dt: float,
    ) -> List[EgoPredictionPoint]:
        dir_x = math.cos(ego_yaw)
        dir_y = math.sin(ego_yaw)
        prediction = []
        for step in range(step_count + 1):
            t = step * dt
            distance = speed * t
            prediction.append(
                EgoPredictionPoint(
                    t=t,
                    x=ego_x + distance * dir_x,
                    y=ego_y + distance * dir_y,
                    yaw=ego_yaw,
                    distance_m=distance,
                    source="fallback_straight_prediction",
                    speed_mps=speed,
                )
            )
        return prediction

    def _trajectory_prediction(
        self, ego_x: float, ego_y: float, speed: float, step_count: int, dt: float
    ) -> List[EgoPredictionPoint]:
        assert self._trajectory is not None
        points = self._trajectory.points
        nearest_index = min(
            range(len(points)),
            key=lambda i: math.hypot(
                float(points[i].pose.position.x) - ego_x,
                float(points[i].pose.position.y) - ego_y,
            ),
        )
        cumulative = [0.0]
        for i in range(1, len(points)):
            prev = points[i - 1].pose.position
            cur = points[i].pose.position
            cumulative.append(cumulative[-1] + math.hypot(cur.x - prev.x, cur.y - prev.y))
        total_length = cumulative[-1]
        start_s = cumulative[nearest_index]
        prediction = []
        for step in range(step_count + 1):
            t = step * dt
            distance = speed * t
            x, y, yaw = self._sample_trajectory(points, cumulative, start_s + distance, total_length)
            prediction.append(
                EgoPredictionPoint(
                    t=t,
                    x=x,
                    y=y,
                    yaw=yaw,
                    distance_m=distance,
                    source="selected_trajectory",
                    speed_mps=speed,
                )
            )
        return prediction

    def _opponent_prediction(
        self, obstacle: ObstacleState, ego_prediction: List[EgoPredictionPoint]
    ) -> List[EgoPredictionPoint]:
        if self._trajectory_has_points():
            return self._opponent_trajectory_prediction(obstacle, ego_prediction)
        return self._opponent_linear_prediction(obstacle, ego_prediction)

    def _opponent_trajectory_prediction(
        self, obstacle: ObstacleState, ego_prediction: List[EgoPredictionPoint]
    ) -> List[EgoPredictionPoint]:
        assert self._trajectory is not None
        points = self._trajectory.points
        nearest_index = min(
            range(len(points)),
            key=lambda i: math.hypot(
                float(points[i].pose.position.x) - obstacle.x,
                float(points[i].pose.position.y) - obstacle.y,
            ),
        )
        cumulative = [0.0]
        for i in range(1, len(points)):
            prev = points[i - 1].pose.position
            cur = points[i].pose.position
            cumulative.append(cumulative[-1] + math.hypot(cur.x - prev.x, cur.y - prev.y))
        total_length = cumulative[-1]
        start_s = cumulative[nearest_index]
        _, _, start_yaw = self._sample_trajectory(points, cumulative, start_s, total_length)
        speed = max(0.0, obstacle.vx * math.cos(start_yaw) + obstacle.vy * math.sin(start_yaw))

        prediction: List[EgoPredictionPoint] = []
        for ego_point in ego_prediction:
            distance = speed * ego_point.t
            x, y, yaw = self._sample_trajectory(points, cumulative, start_s + distance, total_length)
            prediction.append(
                EgoPredictionPoint(
                    t=ego_point.t,
                    x=x,
                    y=y,
                    yaw=yaw,
                    distance_m=distance,
                    source="published_trajectory",
                    speed_mps=speed,
                )
            )
        return prediction

    @staticmethod
    def _opponent_linear_prediction(
        obstacle: ObstacleState, ego_prediction: List[EgoPredictionPoint]
    ) -> List[EgoPredictionPoint]:
        prediction: List[EgoPredictionPoint] = []
        yaw = math.atan2(obstacle.vy, obstacle.vx) if math.hypot(obstacle.vx, obstacle.vy) > 0.1 else 0.0
        for ego_point in ego_prediction:
            prediction.append(
                EgoPredictionPoint(
                    t=ego_point.t,
                    x=obstacle.x + obstacle.vx * ego_point.t,
                    y=obstacle.y + obstacle.vy * ego_point.t,
                    yaw=yaw,
                    distance_m=math.hypot(obstacle.vx, obstacle.vy) * ego_point.t,
                    source="linear_v2x_fallback",
                    speed_mps=math.hypot(obstacle.vx, obstacle.vy),
                )
            )
        return prediction

    def _sample_trajectory(
        self, points, cumulative: List[float], target_s: float, total_length: float
    ) -> Tuple[float, float, float]:
        if total_length <= 1.0e-6:
            point = points[0].pose.position
            return float(point.x), float(point.y), 0.0
        if bool(self._p("trajectory_circular")):
            target_s = target_s % total_length
        else:
            target_s = min(max(0.0, target_s), total_length)
        upper = 1
        while upper < len(cumulative) and cumulative[upper] < target_s:
            upper += 1
        upper = min(upper, len(cumulative) - 1)
        lower = max(0, upper - 1)
        p0 = points[lower].pose.position
        p1 = points[upper].pose.position
        span = max(1.0e-6, cumulative[upper] - cumulative[lower])
        ratio = (target_s - cumulative[lower]) / span
        x = float(p0.x) + (float(p1.x) - float(p0.x)) * ratio
        y = float(p0.y) + (float(p1.y) - float(p0.y)) * ratio
        yaw = math.atan2(float(p1.y) - float(p0.y), float(p1.x) - float(p0.x))
        return x, y, yaw

    def _decision_from_metrics(self, speed: float, metrics: RiskMetrics) -> SafetyDecision:
        decision = SafetyDecision(
            front_distance_m=metrics.front_distance_current_m,
            front_distance_current_m=metrics.front_distance_current_m,
            ttc_s=metrics.predicted_ttc_s,
            min_distance_m=metrics.min_predicted_distance_m,
            predicted_collision_distance_m=metrics.predicted_collision_distance_m,
            prediction_source=metrics.prediction_source,
            opponent_prediction_source=metrics.opponent_prediction_source,
            opponent_observed_speed_mps=metrics.opponent_observed_speed_mps,
            relative_speed_mps=metrics.relative_speed_mps,
            follow_speed_limit_mps=metrics.follow_speed_limit_mps,
            follow_distance_m=metrics.follow_distance_m,
        )

        comfortable = max(0.1, float(self._p("comfortable_decel_mps2")))
        emergency = max(comfortable, float(self._p("emergency_decel_mps2")))
        max_brake = max(emergency, float(self._p("max_brake_decel_mps2")))
        latency_distance = speed * max(0.0, float(self._p("latency_margin_s")))
        margin = max(0.0, float(self._p("distance_margin_m")))
        decision.warning_distance_m = speed * speed / (2.0 * comfortable) + latency_distance + margin
        decision.brake_distance_m = speed * speed / (2.0 * emergency) + latency_distance + margin

        def speed_limit(decel: float) -> float:
            distance = metrics.predicted_collision_distance_m
            if math.isinf(distance):
                distance = metrics.front_distance_current_m
            usable = max(0.0, distance - latency_distance - margin)
            return math.sqrt(2.0 * decel * usable)

        min_race_speed = float(self._p("min_race_speed_kmh")) / 3.6
        if metrics.front_distance_current_m <= float(self._p("hard_stop_distance_m")):
            decision.state = "EMERGENCY_STOP"
            decision.reason = "hard_stop_distance"
            decision.speed_limit_mps = 0.0
            decision.acceleration_limit_mps2 = -max_brake
            decision.risk_class = "BLOCKED"
        elif math.isfinite(metrics.predicted_ttc_s) and (
            metrics.predicted_ttc_s <= float(self._p("brake_ttc_s"))
            or metrics.predicted_collision_distance_m <= decision.brake_distance_m
        ):
            decision.state = "BRAKE_FOR_COMMIT"
            decision.reason = "predicted_collision_requires_brake"
            decision.speed_limit_mps = speed_limit(emergency)
            decision.acceleration_limit_mps2 = -emergency
            decision.risk_class = "COMMITTED"
        elif math.isfinite(metrics.follow_speed_limit_mps):
            decision.state = "FOLLOW"
            decision.reason = "closing_to_observed_v2x_vehicle"
            decision.speed_limit_mps = metrics.follow_speed_limit_mps
            decision.acceleration_limit_mps2 = -comfortable
            decision.risk_class = "RISKY"
        elif math.isfinite(metrics.predicted_ttc_s) and (
            metrics.predicted_ttc_s <= float(self._p("avoid_with_slowdown_ttc_s"))
            or metrics.predicted_collision_distance_m <= decision.warning_distance_m
        ):
            decision.state = "AVOID_WITH_SLOWDOWN"
            decision.reason = "predicted_collision_needs_slowdown"
            decision.speed_limit_mps = max(min_race_speed, speed_limit(comfortable))
            decision.acceleration_limit_mps2 = -comfortable
            decision.risk_class = "RISKY"
        elif math.isfinite(metrics.predicted_ttc_s):
            decision.state = "AVOID"
            decision.reason = "predicted_collision_beyond_slowdown"
            decision.risk_class = "RISKY"
        else:
            decision.state = "AVOID" if math.isfinite(metrics.min_predicted_distance_m) else "CLEAR"
            decision.reason = "path_clear_with_obstacles" if decision.state == "AVOID" else "clear"
            decision.risk_class = "SAFE"
        return decision

    def _decision_for_fail_policy(self, reason: str, policy: str) -> SafetyDecision:
        decision = SafetyDecision(state="CLEAR", reason=reason, risk_class="UNKNOWN")
        if policy == "stop":
            decision.state = "EMERGENCY_STOP"
            decision.speed_limit_mps = 0.0
            decision.acceleration_limit_mps2 = -max(0.1, float(self._p("max_brake_decel_mps2")))
        elif policy == "slow":
            decision.state = "AVOID_WITH_SLOWDOWN"
            decision.speed_limit_mps = float(self._p("fail_slow_speed_kmh")) / 3.6
            decision.acceleration_limit_mps2 = -max(0.1, float(self._p("comfortable_decel_mps2")))
        return decision

    def _apply_decision(
        self, msg: AckermannControlCommand, decision: SafetyDecision
    ) -> AckermannControlCommand:
        filtered = copy.deepcopy(msg)
        if decision.state in ("CLEAR", "AVOID", "STALE"):
            return filtered

        if math.isfinite(decision.speed_limit_mps):
            filtered.longitudinal.speed = min(
                float(filtered.longitudinal.speed), decision.speed_limit_mps
            )
        if math.isfinite(decision.acceleration_limit_mps2):
            filtered.longitudinal.acceleration = min(
                float(filtered.longitudinal.acceleration), decision.acceleration_limit_mps2
            )
        if decision.state == "EMERGENCY_STOP":
            filtered.longitudinal.speed = 0.0
        return filtered

    def _publish_status(
        self,
        decision: SafetyDecision,
        raw: Optional[AckermannControlCommand],
        filtered: Optional[AckermannControlCommand],
    ) -> None:
        payload = {
            "state": decision.state,
            "reason": decision.reason,
            "front_distance_m": self._finite_or_none(decision.front_distance_m),
            "front_distance_current_m": self._finite_or_none(decision.front_distance_current_m),
            "ttc_s": self._finite_or_none(decision.ttc_s),
            "min_distance_m": self._finite_or_none(decision.min_distance_m),
            "predicted_collision_distance_m": self._finite_or_none(
                decision.predicted_collision_distance_m
            ),
            "warning_distance_m": self._finite_or_none(decision.warning_distance_m),
            "brake_distance_m": self._finite_or_none(decision.brake_distance_m),
            "speed_limit_mps": self._finite_or_none(decision.speed_limit_mps),
            "acceleration_limit_mps2": self._finite_or_none(decision.acceleration_limit_mps2),
            "risk_class": decision.risk_class,
            "prediction_source": decision.prediction_source,
            "opponent_prediction_source": decision.opponent_prediction_source,
            "opponent_observed_speed_mps": self._finite_or_none(
                decision.opponent_observed_speed_mps
            ),
            "relative_speed_mps": self._finite_or_none(decision.relative_speed_mps),
            "follow_speed_limit_mps": self._finite_or_none(decision.follow_speed_limit_mps),
            "follow_distance_m": self._finite_or_none(decision.follow_distance_m),
            "raw_speed_mps": None if raw is None else float(raw.longitudinal.speed),
            "filtered_speed_mps": None if filtered is None else float(filtered.longitudinal.speed),
            "raw_acceleration_mps2": None if raw is None else float(raw.longitudinal.acceleration),
            "filtered_acceleration_mps2": None
            if filtered is None
            else float(filtered.longitudinal.acceleration),
        }
        self._status_pub.publish(String(data=json.dumps(payload, sort_keys=True)))

    def _publish_debug_markers(self, decision: SafetyDecision) -> None:
        stamp = self.get_clock().now().to_msg()
        markers = MarkerArray()
        markers.markers.append(self._delete_marker(stamp))

        if self._odom is None:
            self._marker_pub.publish(markers)
            return

        pose = self._odom.pose.pose
        prediction = self._last_ego_prediction
        if prediction:
            markers.markers.append(self._line_marker(stamp, 1, prediction, self._state_color(decision.state)))
            markers.markers.append(
                self._distance_marker(stamp, 2, prediction, decision.warning_distance_m, "warning")
            )
            markers.markers.append(
                self._distance_marker(stamp, 3, prediction, decision.brake_distance_m, "brake")
            )
            markers.markers.append(
                self._distance_marker(
                    stamp, 4, prediction, decision.predicted_collision_distance_m, "predicted_collision"
                )
            )
        marker_id = 10
        for opponent_prediction in self._last_opponent_predictions:
            if opponent_prediction:
                markers.markers.append(
                    self._line_marker(
                        stamp,
                        marker_id,
                        opponent_prediction,
                        ColorRGBA(r=1.0, g=0.35, b=0.0, a=0.75),
                    )
                )
                marker_id += 1

        text = (
            f"{decision.state} ({decision.risk_class})\n"
            f"reason: {decision.reason}\n"
            f"ego: {decision.prediction_source}, opponent: {decision.opponent_prediction_source}\n"
            f"ttc: {self._fmt(decision.ttc_s)} s, "
            f"collision_s: {self._fmt(decision.predicted_collision_distance_m)} m\n"
            f"front_now: {self._fmt(decision.front_distance_current_m)} m\n"
            f"opp_v: {self._fmt(decision.opponent_observed_speed_mps)} m/s, "
            f"rel_v: {self._fmt(decision.relative_speed_mps)} m/s\n"
            f"follow_limit: {self._fmt(decision.follow_speed_limit_mps)} m/s, "
            f"follow_s: {self._fmt(decision.follow_distance_m)} m\n"
            f"warn: {self._fmt(decision.warning_distance_m)} m, "
            f"brake: {self._fmt(decision.brake_distance_m)} m\n"
            f"speed_limit: {self._fmt(decision.speed_limit_mps)} m/s, "
            f"acc_limit: {self._fmt(decision.acceleration_limit_mps2)}"
        )
        markers.markers.append(self._text_marker(stamp, 5, pose.position.x, pose.position.y, text))
        self._marker_pub.publish(markers)

    def _delete_marker(self, stamp) -> Marker:
        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = "map"
        marker.ns = "longitudinal_safety_debug"
        marker.id = 0
        marker.action = Marker.DELETEALL
        return marker

    def _line_marker(
        self, stamp, marker_id: int, prediction: List[EgoPredictionPoint], color: ColorRGBA
    ) -> Marker:
        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = "map"
        marker.ns = "longitudinal_safety_debug"
        marker.id = marker_id
        marker.type = Marker.LINE_STRIP
        marker.action = Marker.ADD
        marker.scale.x = 0.18
        marker.color = color
        marker.points = [Point(x=point.x, y=point.y, z=0.35) for point in prediction]
        return marker

    def _distance_marker(
        self, stamp, marker_id: int, prediction: List[EgoPredictionPoint], distance_m: float, label: str
    ) -> Marker:
        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = "map"
        marker.ns = "longitudinal_safety_debug"
        marker.id = marker_id
        marker.action = Marker.ADD
        marker.type = Marker.SPHERE
        marker.scale.x = 0.8
        marker.scale.y = 0.8
        marker.scale.z = 0.8
        marker.color = self._marker_color_for_label(label)
        if math.isfinite(distance_m):
            point = min(prediction, key=lambda item: abs(item.distance_m - distance_m))
            marker.pose.position = Point(x=point.x, y=point.y, z=0.8)
        else:
            marker.action = Marker.DELETE
        return marker

    def _text_marker(self, stamp, marker_id: int, x: float, y: float, text: str) -> Marker:
        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = "map"
        marker.ns = "longitudinal_safety_debug"
        marker.id = marker_id
        marker.type = Marker.TEXT_VIEW_FACING
        marker.action = Marker.ADD
        marker.pose.position = Point(x=float(x), y=float(y), z=2.2)
        marker.scale.z = 0.45
        marker.color = ColorRGBA(r=1.0, g=1.0, b=1.0, a=0.95)
        marker.text = text
        return marker

    @staticmethod
    def _marker_color_for_label(label: str) -> ColorRGBA:
        if label == "brake":
            return ColorRGBA(r=1.0, g=0.0, b=0.0, a=0.9)
        if label == "predicted_collision":
            return ColorRGBA(r=1.0, g=0.0, b=1.0, a=0.9)
        return ColorRGBA(r=1.0, g=0.8, b=0.0, a=0.9)

    @staticmethod
    def _state_color(state: str) -> ColorRGBA:
        if state in ("EMERGENCY_STOP", "BRAKE_FOR_COMMIT"):
            return ColorRGBA(r=1.0, g=0.0, b=0.0, a=0.8)
        if state == "AVOID_WITH_SLOWDOWN":
            return ColorRGBA(r=1.0, g=0.8, b=0.0, a=0.8)
        if state == "FOLLOW":
            return ColorRGBA(r=0.6, g=1.0, b=0.0, a=0.8)
        if state == "AVOID":
            return ColorRGBA(r=0.0, g=0.8, b=1.0, a=0.75)
        return ColorRGBA(r=0.0, g=1.0, b=0.2, a=0.75)

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{value:.2f}" if math.isfinite(value) else "inf"

    @staticmethod
    def _finite_or_none(value: float):
        return value if math.isfinite(value) else None

    @staticmethod
    def _yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        return math.atan2(siny_cosp, cosy_cosp)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = LongitudinalSafetyFilterNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
