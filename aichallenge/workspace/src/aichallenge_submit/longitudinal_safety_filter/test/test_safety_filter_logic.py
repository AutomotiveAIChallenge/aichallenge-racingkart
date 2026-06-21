#!/usr/bin/env python3
import math

import rclpy
from autoware_auto_planning_msgs.msg import Trajectory, TrajectoryPoint
from v2x_msgs.msg import V2XVehiclePosition, V2XVehiclePositionArray

from longitudinal_safety_filter.longitudinal_safety_filter_node import (
    EgoPredictionPoint,
    LongitudinalSafetyFilterNode,
    ObstacleState,
    RiskMetrics,
    V2XTracker,
)


def _make_node():
    if not rclpy.ok():
        rclpy.init()
    return LongitudinalSafetyFilterNode()


def _destroy_node(node):
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()


def _make_v2x_msg(vehicle_id: str, stamp_s: float, x: float, y: float):
    msg = V2XVehiclePositionArray()
    vehicle = V2XVehiclePosition()
    vehicle.vehicle_id = vehicle_id
    vehicle.header.stamp.sec = int(stamp_s)
    vehicle.header.stamp.nanosec = int((stamp_s - int(stamp_s)) * 1.0e9)
    vehicle.position.x = x
    vehicle.position.y = y
    msg.vehicles.append(vehicle)
    return msg


def _make_straight_trajectory() -> Trajectory:
    trajectory = Trajectory()
    for x in (0.0, 10.0, 20.0):
        point = TrajectoryPoint()
        point.pose.position.x = x
        point.pose.position.y = 0.0
        trajectory.points.append(point)
    return trajectory


def test_v2x_tracker_estimates_velocity_from_observations():
    tracker = V2XTracker(
        v_max_mps=30.0,
        jump_threshold_m=8.0,
        history_size=5,
        min_velocity_dt_s=0.05,
        velocity_smoothing_gain=1.0,
    )

    tracker.update(_make_v2x_msg("opponent", 1.0, 0.0, 0.0), 1.0)
    tracker.update(_make_v2x_msg("opponent", 2.0, 2.0, 0.0), 2.0)
    states = tracker.active_states(2.0, 1.0)

    assert len(states) == 1
    assert math.isclose(states[0].vx, 2.0)
    assert math.isclose(states[0].vy, 0.0)


def test_stationary_obstacle_does_not_advance_with_default_speed():
    node = _make_node()
    try:
        node._trajectory = _make_straight_trajectory()
        obstacle = ObstacleState("opponent", 0.0, 0.0, 0.0, 0.0, 0.0)
        ego_prediction = [
            EgoPredictionPoint(0.0, 0.0, 0.0, 0.0, 0.0, "mpc_prediction", 5.0),
            EgoPredictionPoint(1.0, 5.0, 0.0, 0.0, 5.0, "mpc_prediction", 5.0),
        ]

        prediction = node._opponent_trajectory_prediction(obstacle, ego_prediction)

        assert math.isclose(prediction[0].x, prediction[1].x)
        assert prediction[1].speed_mps == 0.0
    finally:
        _destroy_node(node)


def test_predicted_close_distance_does_not_trigger_emergency_stop():
    node = _make_node()
    try:
        speed = 20.0 / 3.6
        decision = node._decision_from_metrics(
            speed,
            RiskMetrics(
                front_distance_current_m=8.0,
                predicted_ttc_s=1.2,
                min_predicted_distance_m=0.5,
                predicted_collision_distance_m=speed * 1.2,
                prediction_source="selected_trajectory",
            ),
        )

        assert decision.state in ("AVOID_WITH_SLOWDOWN", "BRAKE_FOR_COMMIT")
        assert decision.state != "EMERGENCY_STOP"
    finally:
        _destroy_node(node)


def test_current_hard_stop_distance_triggers_emergency_stop():
    node = _make_node()
    try:
        decision = node._decision_from_metrics(
            20.0 / 3.6,
            RiskMetrics(
                front_distance_current_m=0.8,
                predicted_ttc_s=math.inf,
                min_predicted_distance_m=math.inf,
                predicted_collision_distance_m=math.inf,
                prediction_source="selected_trajectory",
            ),
        )

        assert decision.state == "EMERGENCY_STOP"
        assert decision.speed_limit_mps == 0.0
        assert decision.acceleration_limit_mps2 < 0.0
    finally:
        _destroy_node(node)


def test_path_clear_with_obstacle_keeps_avoid_state_without_speed_cap():
    node = _make_node()
    try:
        decision = node._decision_from_metrics(
            20.0 / 3.6,
            RiskMetrics(
                front_distance_current_m=8.0,
                predicted_ttc_s=math.inf,
                min_predicted_distance_m=3.0,
                predicted_collision_distance_m=math.inf,
                prediction_source="selected_trajectory",
            ),
        )

        assert decision.state == "AVOID"
        assert decision.risk_class == "SAFE"
        assert math.isinf(decision.speed_limit_mps)
        assert math.isinf(decision.acceleration_limit_mps2)
    finally:
        _destroy_node(node)


def test_far_ttc_starts_slowdown_before_close_range():
    node = _make_node()
    try:
        speed = 20.0 / 3.6
        decision = node._decision_from_metrics(
            speed,
            RiskMetrics(
                front_distance_current_m=18.0,
                predicted_ttc_s=3.5,
                min_predicted_distance_m=0.5,
                predicted_collision_distance_m=speed * 3.5,
                prediction_source="selected_trajectory",
            ),
        )

        assert decision.state == "AVOID_WITH_SLOWDOWN"
        assert decision.speed_limit_mps >= 5.0 / 3.6
        assert decision.acceleration_limit_mps2 < 0.0
    finally:
        _destroy_node(node)


def test_brake_for_commit_can_command_full_stop_without_race_speed_floor():
    node = _make_node()
    try:
        decision = node._decision_from_metrics(
            20.0 / 3.6,
            RiskMetrics(
                front_distance_current_m=2.0,
                predicted_ttc_s=0.8,
                min_predicted_distance_m=0.4,
                predicted_collision_distance_m=1.0,
                prediction_source="selected_trajectory",
            ),
        )

        assert decision.state == "BRAKE_FOR_COMMIT"
        assert decision.speed_limit_mps == 0.0
        assert decision.acceleration_limit_mps2 < 0.0
    finally:
        _destroy_node(node)


def test_closing_obstacle_uses_following_speed_limit_before_stop():
    node = _make_node()
    try:
        decision = node._decision_from_metrics(
            20.0 / 3.6,
            RiskMetrics(
                front_distance_current_m=8.0,
                predicted_ttc_s=math.inf,
                min_predicted_distance_m=1.0,
                predicted_collision_distance_m=math.inf,
                prediction_source="mpc_prediction",
                opponent_prediction_source="published_trajectory",
                opponent_observed_speed_mps=2.0,
                relative_speed_mps=3.0,
                follow_speed_limit_mps=2.3,
                follow_distance_m=5.0,
            ),
        )

        assert decision.state == "FOLLOW"
        assert decision.speed_limit_mps == 2.3
        assert decision.acceleration_limit_mps2 < 0.0
    finally:
        _destroy_node(node)


def test_no_odom_slow_policy_limits_speed_and_acceleration():
    node = _make_node()
    try:
        decision = node._decision_for_fail_policy("no_odom", "slow")

        assert decision.state == "AVOID_WITH_SLOWDOWN"
        assert decision.reason == "no_odom"
        assert decision.speed_limit_mps > 0.0
        assert decision.acceleration_limit_mps2 < 0.0
    finally:
        _destroy_node(node)
