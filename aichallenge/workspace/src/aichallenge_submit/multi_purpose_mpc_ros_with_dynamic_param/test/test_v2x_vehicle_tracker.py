"""Unit tests for V2XVehicleTracker (pure Python, no rclpy)."""

from dataclasses import dataclass
from typing import List

import pytest

from multi_purpose_mpc_ros.v2x_vehicle_tracker import V2XVehicleTracker


# Lightweight stand-ins for v2x_msgs / std_msgs / geometry_msgs so tests
# do not require the ROS message DLLs to be importable.
@dataclass
class _Stamp:
    sec: int
    nanosec: int


@dataclass
class _Header:
    stamp: _Stamp


@dataclass
class _Point:
    x: float
    y: float
    z: float = 0.0


@dataclass
class _V2XVehiclePosition:
    header: _Header
    vehicle_id: str
    position: _Point


@dataclass
class _V2XVehiclePositionArray:
    header: _Header
    vehicles: List[_V2XVehiclePosition]


def _msg(stamp_sec: float, vehicles):
    """Build a fake V2XVehiclePositionArray with the given (vehicle_id, x, y)."""
    sec = int(stamp_sec)
    nanosec = int((stamp_sec - sec) * 1e9)
    array_header = _Header(_Stamp(sec, nanosec))
    out = []
    for vid, x, y in vehicles:
        out.append(_V2XVehiclePosition(
            header=_Header(_Stamp(sec, nanosec)),
            vehicle_id=vid,
            position=_Point(x=x, y=y),
        ))
    return _V2XVehiclePositionArray(header=array_header, vehicles=out)


def test_two_samples_constant_velocity_yields_finite_difference():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=20.0)

    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.5, [("d2", 5.0, 2.5)]))

    vx, vy = tracker.velocity("d2")
    assert vx == pytest.approx(10.0)
    assert vy == pytest.approx(5.0)


def test_single_sample_yields_zero_velocity():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=5.0)

    tracker.update(_msg(0.0, [("d2", 1.0, 2.0)]))

    assert tracker.velocity("d2") == (0.0, 0.0)


def test_unknown_vehicle_velocity_is_zero():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=5.0)
    assert tracker.velocity("d9") == (0.0, 0.0)


def test_predict_positions_constant_velocity():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=20.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.5, [("d2", 5.0, 2.5)]))  # vx=10, vy=5, latest (5,2.5)

    points = tracker.predict_positions("d2", [0.0, 0.5, 1.0])

    assert points[0] == pytest.approx((5.0, 2.5))
    assert points[1] == pytest.approx((10.0, 5.0))
    assert points[2] == pytest.approx((15.0, 7.5))


def test_position_jump_resets_velocity_and_drops_old_sample():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=5.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.1, [("d2", 100.0, 0.0)]))  # 100 m jump > 5 m

    assert tracker.velocity("d2") == (0.0, 0.0)
    # Predictions should anchor at the *new* position with zero velocity.
    points = tracker.predict_positions("d2", [0.0, 0.5])
    assert points[0] == pytest.approx((100.0, 0.0))
    assert points[1] == pytest.approx((100.0, 0.0))


def test_velocity_above_safety_cap_is_zeroed():
    # 50 m / 0.05 s = 1000 m/s, well above v_max_safety=30
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=200.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.05, [("d2", 50.0, 0.0)]))

    assert tracker.velocity("d2") == (0.0, 0.0)


def test_two_vehicles_tracked_independently():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=20.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0), ("d3", 10.0, 10.0)]))
    tracker.update(_msg(0.5, [("d2", 5.0, 0.0), ("d3", 10.0, 12.5)]))

    assert tracker.velocity("d2") == pytest.approx((10.0, 0.0))
    assert tracker.velocity("d3") == pytest.approx((0.0, 5.0))


def test_active_ids_reflect_latest_message_only():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=20.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0), ("d3", 10.0, 10.0)]))
    tracker.update(_msg(0.5, [("d2", 5.0, 0.0)]))  # d3 dropped this tick

    assert tracker.active_vehicle_ids() == ["d2"]
    # d3 is still in the internal state but not reported as active.
    assert tracker.velocity("d3") == pytest.approx((0.0, 0.0))


def test_predict_all_returns_only_active_vehicles():
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=20.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0), ("d3", 10.0, 10.0)]))
    tracker.update(_msg(0.5, [("d2", 5.0, 0.0)]))  # d3 dropped

    out = tracker.predict_all([0.0, 1.0])
    assert set(out.keys()) == {"d2"}
    assert out["d2"][0] == pytest.approx((5.0, 0.0))
    assert out["d2"][1] == pytest.approx((15.0, 0.0))


@dataclass
class _StubObstacle:
    cx: float
    cy: float
    radius: float


def test_predictions_to_obstacles_flattens_with_radius():
    from multi_purpose_mpc_ros.v2x_vehicle_tracker import predictions_to_obstacles

    predictions = {
        "d2": [(1.0, 2.0), (3.0, 4.0)],
        "d3": [(5.0, 6.0)],
    }
    obstacles = predictions_to_obstacles(
        predictions, vehicle_radius=0.5, obstacle_cls=_StubObstacle)

    centers = sorted((o.cx, o.cy, o.radius) for o in obstacles)
    assert centers == sorted([
        (1.0, 2.0, 0.5),
        (3.0, 4.0, 0.5),
        (5.0, 6.0, 0.5),
    ])


def test_predictions_to_obstacles_empty_input():
    from multi_purpose_mpc_ros.v2x_vehicle_tracker import predictions_to_obstacles
    assert predictions_to_obstacles(
        {}, vehicle_radius=0.5, obstacle_cls=_StubObstacle) == []


def test_position_jump_invokes_warn_callback():
    msgs = []
    tracker = V2XVehicleTracker(
        v_max_safety=30.0,
        position_jump_threshold=5.0,
        warn_callback=msgs.append,
    )
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.1, [("d2", 100.0, 0.0)]))

    assert any("position jump" in m for m in msgs)
    assert any("d2" in m for m in msgs)


def test_velocity_cap_invokes_warn_callback():
    msgs = []
    tracker = V2XVehicleTracker(
        v_max_safety=30.0,
        position_jump_threshold=200.0,
        warn_callback=msgs.append,
    )
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.05, [("d2", 50.0, 0.0)]))

    assert any("velocity" in m for m in msgs)
    assert any("d2" in m for m in msgs)


def test_warn_callback_optional_default_is_silent():
    # Construct without a callback; clamp fires must not raise.
    tracker = V2XVehicleTracker(v_max_safety=30.0, position_jump_threshold=5.0)
    tracker.update(_msg(0.0, [("d2", 0.0, 0.0)]))
    tracker.update(_msg(0.1, [("d2", 100.0, 0.0)]))  # would warn if a callback existed

    assert tracker.velocity("d2") == (0.0, 0.0)  # clamp still fires
