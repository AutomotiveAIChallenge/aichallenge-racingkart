#!/usr/bin/env python3
"""Race director: start control, independent ranking, result-summary output.

V2X車両位置から全車の進捗/ラップ/順位を独立計算する(車両の自己申告に
依存しない)。/admin/awsim/state と /admin/awsim/start は AWSIM の
StartSyncCoordinator / AwsimGameStatusRos2Publisher 互換。"""
from __future__ import annotations

import json
import os

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String
from v2x_msgs.msg import V2XVehiclePositionArray

from race_judge_py.geometry.osm_map import load_lanelet_map
from race_judge_py.geometry.track import Track
from race_judge_py.logic.lap_counter import LapCounter
from race_judge_py.logic.rank_registry import RankTracker, compute_ranks
from race_judge_py.logic.result_writer import atomic_write_json, build_summary

LATCHED_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


class VehicleTrack:
    """director が管理する1台分の状態。"""

    def __init__(self):
        self.laps = LapCounter()
        self.hint: int | None = None
        self.progress = 0.0
        self.last_seen: float | None = None
        self.finished = False
        self.finish_order: int | None = None


class RaceDirectorNode(Node):
    def __init__(self, **kwargs):
        super().__init__("race_director", **kwargs)
        self.declare_parameter("expected_vehicles", ["d1", "d2", "d3"])
        self.declare_parameter("map_path", "")
        self.declare_parameter("output_dir", "/output/latest")
        self.declare_parameter("required_laps", 6)
        self.declare_parameter("session_timeout", 480.0)
        self.declare_parameter("start_countdown_sec", 10.0)
        self.declare_parameter("rank_persistence_sec", 1.0)
        self.declare_parameter("start_line_position", [0.0, 0.0])

        p = lambda name: self.get_parameter(name).value  # noqa: E731
        self.expected = list(p("expected_vehicles"))
        self.output_dir = p("output_dir")
        self.required_laps = int(p("required_laps"))
        self.session_timeout = float(p("session_timeout"))
        self.countdown_sec = float(p("start_countdown_sec"))

        lmap = load_lanelet_map(p("map_path"))
        self.track = Track(lmap.centerline)
        start_xy = list(p("start_line_position"))
        if start_xy != [0.0, 0.0]:
            self.track.set_origin(start_xy)

        self.vehicles: dict[str, VehicleTrack] = {}
        self.rank_tracker = RankTracker(float(p("rank_persistence_sec")))
        self.admin_state = "selectmode"
        self.go_time: float | None = None
        self.countdown_started: float | None = None
        self.finish_counter = 0
        self.summary_written = False

        self.pub_admin = self.create_publisher(String, "/admin/awsim/state", LATCHED_QOS)
        self.pub_ranking = self.create_publisher(String, "/judge/ranking", 10)
        self.create_subscription(V2XVehiclePositionArray, "/v2x/vehicle_positions", self.on_v2x, 10)
        self.create_subscription(Bool, "/admin/awsim/start", self.on_start_flag, 10)
        self.create_timer(0.1, self.on_tick)
        self.create_timer(1.0, lambda: self.pub_admin.publish(String(data=self.admin_state)))
        self.get_logger().info(f"race_director ready: expecting {self.expected}")

    def now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def race_time(self) -> float:
        return 0.0 if self.go_time is None else self.now_sec() - self.go_time

    def set_admin_state(self, state: str) -> None:
        if state != self.admin_state:
            self.get_logger().info(f"admin state: {self.admin_state} -> {state}")
            self.admin_state = state
            self.pub_admin.publish(String(data=state))

    def on_start_flag(self, msg: Bool) -> None:
        if msg.data and self.admin_state == "waitstart" and self.countdown_started is None:
            self.countdown_started = self.now_sec()
            self.set_admin_state("ready")

    def on_v2x(self, msg: V2XVehiclePositionArray) -> None:
        now = self.now_sec()
        for v in msg.vehicles:
            vt = self.vehicles.setdefault(v.vehicle_id, VehicleTrack())
            vt.last_seen = now
            vt.progress, vt.hint = self.track.progress_at(
                (v.position.x, v.position.y), hint=vt.hint)
            if self.go_time is not None and not vt.finished:
                if vt.laps.update(vt.progress, self.race_time()):
                    self.get_logger().info(
                        f"{v.vehicle_id}: lap {vt.laps.lap_count} "
                        f"({vt.laps.lap_times[-1]:.3f}s)")
                if vt.laps.lap_count >= self.required_laps:
                    vt.finished = True
                    self.finish_counter += 1
                    vt.finish_order = self.finish_counter
                    self.set_admin_state("finish")

    def on_tick(self) -> None:
        now = self.now_sec()
        if self.admin_state == "selectmode":
            if all(v in self.vehicles for v in self.expected):
                self.set_admin_state("waitstart")
        elif self.admin_state == "ready" and self.countdown_started is not None:
            if now - self.countdown_started >= self.countdown_sec:
                self.go_time = now
                for vt in self.vehicles.values():
                    vt.laps.start(0.0)
                self.set_admin_state("start")
        elif self.go_time is not None and not self.summary_written:
            self.publish_ranking()
            all_finished = self.expected and all(
                self.vehicles.get(v) is not None and self.vehicles[v].finished
                for v in self.expected)
            if all_finished or self.race_time() > self.session_timeout:
                self.set_admin_state("finishall")
                self.write_summary()

    def total_progress(self) -> dict:
        return {
            int(vid.lstrip("d") or 0): vt.laps.lap_count + vt.progress
            for vid, vt in self.vehicles.items()
        }

    def publish_ranking(self) -> None:
        ranks = self.rank_tracker.update(compute_ranks(self.total_progress()), self.now_sec())
        snapshot = {
            f"d{num}": {
                "rank": rank,
                "lap": self.vehicles.get(f"d{num}", VehicleTrack()).laps.lap_count,
                "progress": round(self.vehicles.get(f"d{num}", VehicleTrack()).progress, 4),
            }
            for num, rank in ranks.items()
        }
        self.pub_ranking.publish(String(data=json.dumps(snapshot)))

    def final_positions(self) -> dict:
        """完走車は完走順、未完走車は totalProgress 降順で後ろに並べる。"""

        def sort_key(item):
            vid, vt = item
            num = int(vid.lstrip("d") or 0)
            if vt.finished:
                return (0, vt.finish_order, num)
            return (1, -(vt.laps.lap_count + vt.progress), num)

        ordered = sorted(self.vehicles.items(), key=sort_key)
        return {vid: i + 1 for i, (vid, _) in enumerate(ordered)}

    def write_summary(self) -> None:
        if self.summary_written:
            return
        self.summary_written = True
        positions = self.final_positions()
        vehicles = []
        for vid, vt in self.vehicles.items():
            num = int(vid.lstrip("d") or 0)
            vehicles.append({
                "vehicle_number": num,
                "vehicle_name": f"GoKart{num}",
                "final_position": positions[vid],
                "finished": vt.finished,
                "laps": vt.laps.lap_times,
            })
        data = build_summary(self.required_laps, self.session_timeout, vehicles)
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, "result-summary.json")
        atomic_write_json(path, data)
        self.get_logger().info(f"summary written: {path}")


def main(args=None):
    rclpy.init(args=args)
    node = RaceDirectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.go_time is not None:
            node.write_summary()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
