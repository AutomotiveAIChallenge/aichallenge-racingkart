#!/usr/bin/env python3
"""Offline closed-loop benchmark & golden-equivalence harness for the MPC core.

Runs the vendored MPC (no ROS) on the real final_ver3 track in closed loop
(model.drive), measuring per-cycle wall time and optionally comparing the
control sequence / trajectory against a saved golden run.

Construction mirrors mpc_controller.py:325-437 (create_ref_path / create_car /
create_mpc / compute_speed_profile), with values transcribed verbatim from
config/config.yaml (mpc section, "中速" block, which is the active config)
and the MPCController defaults use_obstacle_avoidance=False,
use_path_constraints_topic=False.
"""
import argparse
import cProfile
import pstats
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import numpy as np
from scipy import sparse

PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from multi_purpose_mpc_ros.core.map import Map
from multi_purpose_mpc_ros.core.reference_path import ReferencePath
from multi_purpose_mpc_ros.core.spatial_bicycle_models import BicycleModel
from multi_purpose_mpc_ros.core.MPC import MPC
from multi_purpose_mpc_ros.core.utils import load_ref_path, kmh_to_m_per_sec


# ---- Values transcribed verbatim from config/config.yaml (mpc: block, the
# active "中速" tuning) and mpc_controller.py's create_mpc()/create_car(). ----
N = 20
Q = [3000000.0, 90000000.0, 100000.0]
R = [100000.0, 0.0]
QN = [1000000.0, 1000.0, 10000.0]
V_MAX_KMH = 20.0
A_MIN = -1.6
A_MAX = 0.7
AY_MAX = 6.5
DELTA_MAX_DEG = 32.0
STEER_RATE_MAX = 0.35
CONTROL_RATE = 40.0
STEERING_TIRE_ANGLE_GAIN_VAR = 1.639
WP_ID_OFFSET = 2
USE_MAX_KAPPA_PRED = True

BICYCLE_LENGTH = 1.087
BICYCLE_WIDTH = 2.30

REF_PATH_RESOLUTION = 0.6
REF_PATH_SMOOTHING_DISTANCE = 2
REF_PATH_MAX_WIDTH = 6.0
REF_PATH_CIRCULAR = True

USE_OBSTACLE_AVOIDANCE = False
USE_PATH_CONSTRAINTS_TOPIC = False


def build():
    map_ = Map(str(PKG / "env/final_ver3/occupancy_grid_map.yaml"))
    wp_x, wp_y, _, _ = load_ref_path(str(PKG / "env/final_ver3/traj_mincurv.csv"))
    ref_path = ReferencePath(
        map_,
        wp_x,
        wp_y,
        REF_PATH_RESOLUTION,
        REF_PATH_SMOOTHING_DISTANCE,
        REF_PATH_MAX_WIDTH,
        REF_PATH_CIRCULAR,
    )

    car = BicycleModel(
        ref_path,
        BICYCLE_LENGTH,
        BICYCLE_WIDTH,
        1.0 / CONTROL_RATE,
    )

    v_max_mps = kmh_to_m_per_sec(V_MAX_KMH)

    speed_profile_constraints = {
        "a_min": A_MIN,
        "a_max": A_MAX,
        "v_min": 0.0,
        "v_max": v_max_mps,
        "ay_max": AY_MAX,
    }
    if not ref_path.compute_speed_profile(speed_profile_constraints):
        raise RuntimeError("compute_speed_profile failed")

    delta_max = np.deg2rad(DELTA_MAX_DEG)
    state_constraints = {
        "xmin": np.array([-np.inf, -np.inf, -np.inf]),
        "xmax": np.array([np.inf, np.inf, np.inf]),
    }
    input_constraints = {
        "umin": np.array([0.0, -np.tan(delta_max) / car.length]),
        "umax": np.array([v_max_mps, np.tan(delta_max) / car.length]),
    }

    # mpc's steer command output is scaled by steering_tire_angle_gain_var
    # downstream before the vehicle-side steer-rate limit is applied, so the
    # limit fed into the MPC itself must be pre-divided by that gain
    # (mirrors mpc_controller.py's create_mpc()).
    scaled_steer_rate_max = STEER_RATE_MAX / STEERING_TIRE_ANGLE_GAIN_VAR

    mpc = MPC(
        car,
        N,
        sparse.diags(Q),
        sparse.diags(R),
        sparse.diags(QN),
        state_constraints,
        input_constraints,
        AY_MAX,
        scaled_steer_rate_max,
        WP_ID_OFFSET,
        USE_OBSTACLE_AVOIDANCE,
        USE_PATH_CONSTRAINTS_TOPIC,
        USE_MAX_KAPPA_PRED,
    )

    # Place the car at waypoint 0 of the reference path explicitly (this is
    # already implied by BicycleModel's constructor, which initializes the
    # spatial state at e_y=e_psi=0 relative to waypoint 0, but we do it
    # explicitly here for clarity/determinism).
    wp0 = ref_path.waypoints[0]
    car.update_states(wp0.x, wp0.y, wp0.psi)

    return car, mpc


def run(car, mpc, cycles):
    times, controls, traj = [], [], []
    for _ in range(cycles):
        t0 = time.perf_counter()
        u, _ = mpc.get_control()
        times.append(time.perf_counter() - t0)
        car.drive(u)
        car.update_states(
            car.temporal_state.x, car.temporal_state.y, car.temporal_state.psi
        )
        controls.append(np.array(u, dtype=np.float64).copy())
        traj.append([car.temporal_state.x, car.temporal_state.y])
    return np.array(times), np.array(controls), np.array(traj)


def print_stats(times):
    ms = times * 1000.0
    print("Per-cycle get_control() timing (ms):")
    print(f"  mean = {ms.mean():.4f}")
    print(f"  p50  = {np.percentile(ms, 50):.4f}")
    print(f"  p95  = {np.percentile(ms, 95):.4f}")
    print(f"  max  = {ms.max():.4f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=800)
    parser.add_argument("--save-golden", type=str, default=None)
    parser.add_argument("--check-golden", type=str, default=None)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--rms-tol", type=float, default=1e-9)
    parser.add_argument("--traj-rms-tol", type=float, default=0.0)
    args = parser.parse_args()

    car, mpc = build()

    profiler = None
    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()

    times, controls, traj = run(car, mpc, args.cycles)

    if profiler is not None:
        profiler.disable()

    print_stats(times)

    print()
    print(f"Sanity check: final s = {car.s:.3f} m over {args.cycles} cycles")
    print(f"Sanity check: mpc.infeasibility_counter = {mpc.infeasibility_counter}")

    exit_code = 0

    if args.save_golden:
        golden_path = Path(args.save_golden)
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(golden_path, controls=controls, traj=traj)
        print(f"\nSaved golden run to {golden_path}")

    if args.check_golden:
        golden = np.load(args.check_golden)
        golden_controls = golden["controls"]
        golden_traj = golden["traj"]

        n = min(len(controls), len(golden_controls))
        if len(controls) != len(golden_controls):
            print(
                f"WARNING: controls length mismatch "
                f"(this run={len(controls)}, golden={len(golden_controls)}); "
                f"truncating to common prefix ({n}) before comparing",
                file=sys.stderr,
            )
        du = controls[:n] - golden_controls[:n]
        max_abs_du = np.max(np.abs(du)) if n > 0 else 0.0

        nt = min(len(traj), len(golden_traj))
        if len(traj) != len(golden_traj):
            print(
                f"WARNING: traj length mismatch "
                f"(this run={len(traj)}, golden={len(golden_traj)}); "
                f"truncating to common prefix ({nt}) before comparing",
                file=sys.stderr,
            )
        traj_diff = traj[:nt] - golden_traj[:nt]
        traj_rms = (
            np.sqrt(np.mean(np.sum(traj_diff ** 2, axis=1))) if nt > 0 else 0.0
        )

        print()
        print(f"Golden comparison against {args.check_golden}:")
        print(f"  max|delta u| = {max_abs_du:.6e}")
        print(f"  RMS(traj deviation) = {traj_rms:.6e}")

        if max_abs_du > args.rms_tol or traj_rms > args.traj_rms_tol:
            print(
                f"FAIL: tolerance exceeded "
                f"(rms-tol={args.rms_tol:.3e}, traj-rms-tol={args.traj_rms_tol:.3e})"
            )
            exit_code = 1
        else:
            print("PASS: within tolerance")

    if profiler is not None:
        print("\ncProfile (cumulative time, top 25):")
        stats = pstats.Stats(profiler, stream=sys.stdout)
        stats.sort_stats("cumulative")
        stats.print_stats(25)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
