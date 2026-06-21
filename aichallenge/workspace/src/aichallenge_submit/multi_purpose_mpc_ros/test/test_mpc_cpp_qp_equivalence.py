import csv
import subprocess

import numpy as np
import pytest
import yaml
from ament_index_python.packages import get_package_share_directory

scipy_sparse = pytest.importorskip("scipy.sparse")

from multi_purpose_mpc_ros.core.MPC import MPC
from multi_purpose_mpc_ros.core.map import Map
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.core.utils import kmh_to_m_per_sec, load_ref_path


def _read_cpp_dump(path):
    rows = {}
    with open(path) as stream:
        for row in csv.reader(stream):
            rows[row[0]] = row[1:]
    return rows


def _float_array(values):
    return np.array([float(value) for value in values], dtype=np.float64)


def _int_array(values):
    return np.array([int(float(value)) for value in values], dtype=np.int64)


def _build_python_problem(wp_id):
    package_path = get_package_share_directory("multi_purpose_mpc_ros") + "/"
    with open(package_path + "config/config.yaml") as stream:
        cfg = yaml.safe_load(stream)

    ref_cfg = cfg["reference_path"]
    mpc_cfg = cfg["mpc"]
    wp_x, wp_y, _, _ = load_ref_path(package_path + ref_cfg["csv_path"])
    ref_path = ReferencePath(
        Map(package_path + cfg["map"]["yaml_path"]),
        wp_x,
        wp_y,
        ref_cfg["resolution"],
        ref_cfg["smoothing_distance"],
        ref_cfg["max_width"],
        ref_cfg["circular"],
    )
    car = BicycleModel(
        ref_path,
        cfg["bicycle_model"]["length"],
        cfg["bicycle_model"]["width"],
        1.0 / mpc_cfg["control_rate"],
    )
    ref_path.compute_speed_profile(
        {
            "a_min": mpc_cfg["a_min"],
            "a_max": mpc_cfg["a_max"],
            "v_min": 0.0,
            "v_max": kmh_to_m_per_sec(mpc_cfg["v_max"]),
            "ay_max": mpc_cfg["ay_max"],
        }
    )
    ref_path.update_simple_path_constraints(mpc_cfg["N"], car.safety_margin)

    initial_waypoint = ref_path.get_waypoint(wp_id)
    car.update_states(initial_waypoint.x, initial_waypoint.y, initial_waypoint.psi)
    mpc = MPC(
        car,
        mpc_cfg["N"],
        scipy_sparse.diags(mpc_cfg["Q"]),
        scipy_sparse.diags(mpc_cfg["R"]),
        scipy_sparse.diags(mpc_cfg["QN"]),
        {"xmin": np.array([-np.inf, -np.inf, -np.inf]), "xmax": np.array([np.inf, np.inf, np.inf])},
        {
            "umin": np.array(
                [0.0, -np.tan(np.deg2rad(mpc_cfg["delta_max_deg"])) / car.length]
            ),
            "umax": np.array(
                [
                    kmh_to_m_per_sec(mpc_cfg["v_max"]),
                    np.tan(np.deg2rad(mpc_cfg["delta_max_deg"])) / car.length,
                ]
            ),
        },
        mpc_cfg["ay_max"],
        mpc_cfg["steer_rate_max"] / mpc_cfg["steering_tire_angle_gain_var"],
        mpc_cfg["wp_id_offset"],
        False,
        ref_cfg["use_path_constraints_topic"],
        mpc_cfg["use_max_kappa_pred"],
    )
    car.get_current_waypoint()
    car.spatial_state = car.t2s(
        reference_state=car.temporal_state, reference_waypoint=car.current_waypoint
    )
    mpc._init_problem(mpc.N, car.safety_margin)
    return mpc._osqp_last_problem, car.length, mpc.N


@pytest.mark.parametrize("wp_id", [0, 25, 55, 100, 155, 170, 190, 265, 320])
def test_cpp_qp_dump_matches_python_problem(tmp_path, wp_id):
    dump_path = tmp_path / "cpp_qp_dump.csv"
    result = subprocess.run(
        [
            "ros2",
            "run",
            "multi_purpose_mpc_ros",
            "mpc_controller_cpp",
            "--dump_qp",
            str(dump_path),
            "--dump_wp_id",
            str(wp_id),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr

    cpp = _read_cpp_dump(dump_path)
    (py_p, py_q, py_a, py_l, py_u), wheel_base, horizon = _build_python_problem(wp_id)
    py_solution = _solve_python_problem(py_p, py_q, py_a, py_l, py_u)
    py_raw_control = py_solution[-horizon * 2 :]
    py_steer_control = py_raw_control.copy()
    py_steer_control[1::2] = np.arctan(py_steer_control[1::2] * wheel_base)
    comparisons = [
        ("P_x", py_p.data, _float_array(cpp["P_x"])),
        ("P_i", py_p.indices, _int_array(cpp["P_i"])),
        ("P_p", py_p.indptr, _int_array(cpp["P_p"])),
        ("A_x", py_a.data, _float_array(cpp["A_x"])),
        ("A_i", py_a.indices, _int_array(cpp["A_i"])),
        ("A_p", py_a.indptr, _int_array(cpp["A_p"])),
        ("q", py_q, _float_array(cpp["q"])),
        ("l", py_l, _float_array(cpp["l"])),
        ("u", py_u, _float_array(cpp["u"])),
        ("solution", py_solution, _float_array(cpp["solution"])),
        ("raw_control", py_raw_control, _float_array(cpp["raw_control"])),
        ("steer_control", py_steer_control, _float_array(cpp["steer_control"])),
    ]
    for name, expected, actual in comparisons:
        assert expected.shape == actual.shape, name
        finite = np.isfinite(expected) & np.isfinite(actual)
        assert np.allclose(expected[finite], actual[finite], rtol=5.0e-6, atol=5.0e-6), name


def _solve_python_problem(p, q, a, l, u):
    osqp = pytest.importorskip("osqp")
    solver = osqp.OSQP()
    solver.setup(P=p, q=q, A=a, l=l, u=u, verbose=False, eps_abs=1.0e-8, eps_rel=1.0e-8)
    result = solver.solve()
    assert result.x is not None
    return result.x


def _build_python_controller_at(wp_id):
    package_path = get_package_share_directory("multi_purpose_mpc_ros") + "/"
    with open(package_path + "config/config.yaml") as stream:
        cfg = yaml.safe_load(stream)

    ref_cfg = cfg["reference_path"]
    mpc_cfg = cfg["mpc"]
    wp_x, wp_y, _, _ = load_ref_path(package_path + ref_cfg["csv_path"])
    ref_path = ReferencePath(
        Map(package_path + cfg["map"]["yaml_path"]),
        wp_x,
        wp_y,
        ref_cfg["resolution"],
        ref_cfg["smoothing_distance"],
        ref_cfg["max_width"],
        ref_cfg["circular"],
    )
    car = BicycleModel(
        ref_path,
        cfg["bicycle_model"]["length"],
        cfg["bicycle_model"]["width"],
        1.0 / mpc_cfg["control_rate"],
    )
    ref_path.compute_speed_profile(
        {
            "a_min": mpc_cfg["a_min"],
            "a_max": mpc_cfg["a_max"],
            "v_min": 0.0,
            "v_max": kmh_to_m_per_sec(mpc_cfg["v_max"]),
            "ay_max": mpc_cfg["ay_max"],
        }
    )
    ref_path.update_simple_path_constraints(mpc_cfg["N"], car.safety_margin)
    initial_waypoint = ref_path.get_waypoint(wp_id)
    car.update_states(initial_waypoint.x, initial_waypoint.y, initial_waypoint.psi)
    mpc = MPC(
        car,
        mpc_cfg["N"],
        scipy_sparse.diags(mpc_cfg["Q"]),
        scipy_sparse.diags(mpc_cfg["R"]),
        scipy_sparse.diags(mpc_cfg["QN"]),
        {"xmin": np.array([-np.inf, -np.inf, -np.inf]), "xmax": np.array([np.inf, np.inf, np.inf])},
        {
            "umin": np.array(
                [0.0, -np.tan(np.deg2rad(mpc_cfg["delta_max_deg"])) / car.length]
            ),
            "umax": np.array(
                [
                    kmh_to_m_per_sec(mpc_cfg["v_max"]),
                    np.tan(np.deg2rad(mpc_cfg["delta_max_deg"])) / car.length,
                ]
            ),
        },
        mpc_cfg["ay_max"],
        mpc_cfg["steer_rate_max"] / mpc_cfg["steering_tire_angle_gain_var"],
        mpc_cfg["wp_id_offset"],
        False,
        ref_cfg["use_path_constraints_topic"],
        mpc_cfg["use_max_kappa_pred"],
    )
    return car, mpc


def _read_sequence(path):
    with open(path) as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("wp_id", [0, 55, 155, 265])
def test_cpp_sequence_matches_python_controller(tmp_path, wp_id):
    steps = 8
    dump_path = tmp_path / "cpp_sequence.csv"
    result = subprocess.run(
        [
            "ros2",
            "run",
            "multi_purpose_mpc_ros",
            "mpc_controller_cpp",
            "--dump_sequence",
            str(dump_path),
            "--dump_wp_id",
            str(wp_id),
            "--sequence_steps",
            str(steps),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    cpp_rows = _read_sequence(dump_path)

    car, mpc = _build_python_controller_at(wp_id)
    for step, cpp_row in enumerate(cpp_rows):
        u, max_delta = mpc.get_control()
        state = car.temporal_state
        expected = np.array(
            [step, mpc.model.wp_id, state.x, state.y, state.psi, u[0], u[1], max_delta],
            dtype=np.float64,
        )
        actual = np.array(
            [
                float(cpp_row["step"]),
                float(cpp_row["wp_id"]),
                float(cpp_row["x"]),
                float(cpp_row["y"]),
                float(cpp_row["psi"]),
                float(cpp_row["v_cmd"]),
                float(cpp_row["delta_cmd"]),
                float(cpp_row["max_delta"]),
            ],
            dtype=np.float64,
        )
        assert np.allclose(expected, actual, rtol=5.0e-6, atol=5.0e-6), (expected, actual)
        car.drive([u[0], u[1]])
