#!/usr/bin/env python3
"""
Offline check of the reference speed profile.

Builds the same ReferencePath the controller builds from config.yaml, computes
the forward-backward speed profile for the given limits and prints the lap time
that profile implies, so a tune can be sanity-checked without running AWSIM.

Usage (inside the autoware container):
  python3 check_speed_profile.py [--ay 12] [--a-max 3] [--a-min -3] [--v-max 45]
"""

import argparse
import numpy as np

from multi_purpose_mpc_ros.core.utils import kmh_to_m_per_sec
from multi_purpose_mpc_ros.tools.reference_path_generator import ReferencePathGenerator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/aichallenge/workspace/src/aichallenge_submit/"
                                            "multi_purpose_mpc_ros/config/config.yaml")
    parser.add_argument("--ay", type=float, default=12.0)
    parser.add_argument("--a-max", type=float, default=3.0)
    parser.add_argument("--a-min", type=float, default=-3.0)
    parser.add_argument("--v-max", type=float, default=45.0, help="km/h")
    parser.add_argument("--kappa-smoothing", type=int, default=2)
    args = parser.parse_args()

    ref_path = ReferencePathGenerator.get_reference_path(args.config)
    constraints = {
        "a_min": args.a_min, "a_max": args.a_max, "v_min": 0.0,
        "v_max": kmh_to_m_per_sec(args.v_max), "ay_max": args.ay}

    ref_path.compute_speed_profile_forward_backward(constraints, args.kappa_smoothing)
    v = np.array([wp.v_ref for wp in ref_path.waypoints])
    ds = np.array(ref_path.segment_lengths[1:] + [ref_path.waypoints[0] - ref_path.waypoints[-1]])

    # Trapezoidal time over each segment
    t = float(np.sum(2 * ds / (v + np.roll(v, -1))))
    length = float(np.sum(ds))

    print(f"waypoints      : {ref_path.n_waypoints}")
    print(f"path length    : {length:.1f} m")
    print(f"speed          : min {v.min()*3.6:.1f} / mean {v.mean()*3.6:.1f} / max {v.max()*3.6:.1f} km/h")
    print(f"implied laptime: {t:.2f} s  (avg {length/t*3.6:.1f} km/h)")

    # Where the profile spends its time, in 10 buckets around the lap
    n_bucket = 10
    edges = np.linspace(0, ref_path.n_waypoints, n_bucket + 1).astype(int)
    print("\n  wp range        mean km/h   min km/h")
    for a, b in zip(edges[:-1], edges[1:]):
        print(f"  {a:4d}-{b:4d}      {v[a:b].mean()*3.6:7.1f}    {v[a:b].min()*3.6:7.1f}")


if __name__ == "__main__":
    main()
