#!/usr/bin/env python3
"""
Record the vehicle's speed against its position on the reference path.

Writes a CSV of `t,x,y,v_mps,wp_id,v_ref_mps` so an actual run can be compared
waypoint by waypoint with the speed profile the MPC was given. That comparison
is what localises a slow section: a corner where actual sits well under v_ref is
a tracking problem, one where v_ref itself is low is a profile problem.

Usage (inside the autoware container, same ROS_DOMAIN_ID as the stack):
  python3 trace_speed.py --out /output/trace.csv [--duration 300]
"""

import argparse
import csv
import math

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from nav_msgs.msg import Odometry

from multi_purpose_mpc_ros.core.utils import kmh_to_m_per_sec
from multi_purpose_mpc_ros.tools.reference_path_generator import ReferencePathGenerator


class SpeedTracer(Node):

    def __init__(self, args):
        super().__init__("speed_tracer")
        self._ref_path = ReferencePathGenerator.get_reference_path(args.config)
        self._ref_path.compute_speed_profile_forward_backward(
            {"a_min": args.a_min, "a_max": args.a_max, "v_min": 0.0,
             "v_max": kmh_to_m_per_sec(args.v_max), "ay_max": args.ay},
            args.kappa_smoothing)

        self._wp_xy = [(wp.x, wp.y) for wp in self._ref_path.waypoints]
        self._file = open(args.out, "w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["t", "x", "y", "v_mps", "wp_id", "v_ref_mps"])
        self._t0 = None

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST)
        self.create_subscription(Odometry, "/localization/kinematic_state", self._cb, qos)
        self.get_logger().info(f"tracing to {args.out}")

    def _closest_wp(self, x, y):
        best, best_d = 0, math.inf
        for i, (wx, wy) in enumerate(self._wp_xy):
            d = (wx - x) ** 2 + (wy - y) ** 2
            if d < best_d:
                best, best_d = i, d
        return best

    def _cb(self, msg: Odometry):
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self._t0 is None:
            self._t0 = t
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        v = msg.twist.twist.linear.x
        wp_id = self._closest_wp(x, y)
        self._writer.writerow([f"{t - self._t0:.3f}", f"{x:.3f}", f"{y:.3f}",
                               f"{v:.3f}", wp_id,
                               f"{self._ref_path.waypoints[wp_id].v_ref:.3f}"])
        self._file.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/aichallenge/workspace/src/aichallenge_submit/"
                                            "multi_purpose_mpc_ros/config/config.yaml")
    parser.add_argument("--out", default="/output/trace.csv")
    parser.add_argument("--ay", type=float, default=10.0)
    parser.add_argument("--a-max", type=float, default=3.0)
    parser.add_argument("--a-min", type=float, default=-3.0)
    parser.add_argument("--v-max", type=float, default=45.0)
    parser.add_argument("--kappa-smoothing", type=int, default=1)
    args = parser.parse_args()

    rclpy.init()
    node = SpeedTracer(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
