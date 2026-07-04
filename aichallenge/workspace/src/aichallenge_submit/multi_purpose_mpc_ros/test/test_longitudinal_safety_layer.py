import math
import subprocess


def _dump_safety(speed_kmh, distance_m):
    result = subprocess.run(
        [
            "ros2",
            "run",
            "multi_purpose_mpc_ros",
            "mpc_controller_cpp",
            "--dump_safety",
            "--safety_speed_kmh",
            str(speed_kmh),
            "--safety_distance_m",
            str(distance_m),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    rows = {}
    for line in result.stdout.strip().splitlines():
        key, value = line.split(",", 1)
        rows[key] = value
    return rows


def test_20kmh_requires_braking_before_three_meters():
    rows = _dump_safety(20.0, 8.0)
    assert rows["state"] == "brake"
    assert float(rows["decel_mps2"]) >= 2.5
    assert float(rows["brake_distance_m"]) > 8.0


def test_20kmh_warning_distance_is_physical_stop_distance():
    rows = _dump_safety(20.0, 10.0)
    assert rows["state"] == "slowdown"
    assert float(rows["warning_distance_m"]) > 10.0
    assert float(rows["speed_limit_mps"]) < 20.0 / 3.6
    assert float(rows["speed_limit_mps"]) > 0.0


def test_hard_stop_distance_forces_zero_speed():
    rows = _dump_safety(20.0, 1.0)
    assert rows["state"] == "emergency_stop"
    assert math.isclose(float(rows["speed_limit_mps"]), 0.0)
    assert float(rows["decel_mps2"]) >= 2.5
