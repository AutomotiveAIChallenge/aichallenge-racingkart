#!/usr/bin/env python3
"""On-vehicle race judge: lap / wall / collision judgment from sensors.

AWSIM互換トピック(/awsim/state, /awsim/status)をpublishするため、既存の
autostart_orchestrator_py を無改修で再利用できる。"""
from __future__ import annotations

import math
import os

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Imu
from std_msgs.msg import Bool, Float32MultiArray, String
from v2x_msgs.msg import V2XVehiclePositionArray

from race_judge_py.geometry.footprint import footprint_corners
from race_judge_py.geometry.osm_map import load_lanelet_map
from race_judge_py.geometry.track import Track
from race_judge_py.logic.boundary_judge import BoundaryJudge
from race_judge_py.logic.collision_judge import CollisionJudge, OtherVehicle
from race_judge_py.logic.lap_counter import LapCounter
from race_judge_py.logic.penalty import PenaltyKind, PenaltyTracker
from race_judge_py.logic.result_writer import atomic_write_json, build_details

LATCHED_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


def yaw_from_quaternion(q) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


class VehicleJudgeNode(Node):
    def __init__(self, **kwargs):
        super().__init__("vehicle_judge", **kwargs)
        self.declare_parameter("vehicle_id", "d1")
        self.declare_parameter("map_path", "")
        self.declare_parameter("output_dir", "/output/latest")
        self.declare_parameter("required_laps", 6)
        self.declare_parameter("session_timeout", 480.0)
        self.declare_parameter("penalty_cooldown_sec", 2.0)
        self.declare_parameter("collision_distance_m", 1.5)
        self.declare_parameter("v2x_timeout_sec", 1.0)
        self.declare_parameter("use_imu_confirmation", False)
        self.declare_parameter("imu_impact_threshold", 14.7)
        self.declare_parameter("imu_window_sec", 0.2)
        self.declare_parameter("vehicle_length", 2.0)
        self.declare_parameter("vehicle_width", 1.3)
        self.declare_parameter("antenna_offset_x", 0.0)
        self.declare_parameter("start_line_position", [0.0, 0.0])
        self.declare_parameter("pose_covariance_gate", 1.0)
        self.declare_parameter("ready_pose_count", 20)
        self.declare_parameter("publish_awsim_compat", True)
        self.declare_parameter("judge_rate_hz", 20.0)

        p = lambda name: self.get_parameter(name).value  # noqa: E731
        self.vehicle_id = p("vehicle_id")
        self.vehicle_number = int(self.vehicle_id.lstrip("d") or 1)
        self.output_dir = p("output_dir")
        self.required_laps = int(p("required_laps"))
        self.session_timeout = float(p("session_timeout"))
        self.use_imu = bool(p("use_imu_confirmation"))
        self.imu_threshold = float(p("imu_impact_threshold"))
        self.imu_window = float(p("imu_window_sec"))
        self.vehicle_length = float(p("vehicle_length"))
        self.vehicle_width = float(p("vehicle_width"))
        self.antenna_offset_x = float(p("antenna_offset_x"))
        self.cov_gate = float(p("pose_covariance_gate"))
        self.ready_pose_count = int(p("ready_pose_count"))
        self.awsim_compat = bool(p("publish_awsim_compat"))

        map_path = p("map_path")
        lmap = load_lanelet_map(map_path)
        self.track = Track(lmap.centerline)
        start_xy = list(p("start_line_position"))
        if start_xy != [0.0, 0.0]:
            self.track.set_origin(start_xy)
        self.boundary = BoundaryJudge(lmap.polygons)
        self.collision = CollisionJudge(float(p("collision_distance_m")), float(p("v2x_timeout_sec")))
        self.laps = LapCounter()
        self.penalty = PenaltyTracker(float(p("penalty_cooldown_sec")))

        self.state = "Spawned"
        self.go_time: float | None = None
        self.finished = False
        self.details_written = False
        self.pose = None            # (x, y, yaw, stamp_sec)
        self.pose_count = 0
        self.hint: int | None = None
        self.others: list[OtherVehicle] = []
        self.last_imu_spike: float | None = None

        prefix = "/awsim" if self.awsim_compat else "/judge/awsim"
        self.pub_state = self.create_publisher(String, f"{prefix}/state", LATCHED_QOS)
        self.pub_status = self.create_publisher(Float32MultiArray, f"{prefix}/status", 10)
        self.pub_lap = self.create_publisher(Float32MultiArray, "/judge/lap", 10)
        self.pub_penalty_event = self.create_publisher(String, "/judge/penalty_events", 10)
        self.pub_penalty_active = self.create_publisher(Bool, "/judge/penalty_active", 10)
        self.pub_deviation = self.create_publisher(Bool, "/vehicle/emergency/is_route_deviation", 10)

        self.create_subscription(Odometry, "/localization/kinematic_state", self.on_odom, 10)
        self.create_subscription(V2XVehiclePositionArray, "/v2x/vehicle_positions", self.on_v2x, 10)
        self.create_subscription(Imu, "/sensing/imu/imu_data", self.on_imu, 50)
        self.create_subscription(String, "/admin/awsim/state", self.on_admin_state, LATCHED_QOS)

        rate = float(p("judge_rate_hz"))
        self.create_timer(1.0 / rate, self.on_judge_timer)
        self.create_timer(0.1, self.publish_status)
        self.create_timer(1.0, self.publish_state)
        self.get_logger().info(
            f"vehicle_judge ready: id={self.vehicle_id} track={self.track.total_length:.1f}m "
            f"polygons={len(self.boundary.polygons)}"
        )

    # --- helpers -----------------------------------------------------
    def now_sec(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def race_time(self) -> float:
        return 0.0 if self.go_time is None else self.now_sec() - self.go_time

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.get_logger().info(f"state: {self.state} -> {state}")
            self.state = state
            self.publish_state()

    # --- callbacks ---------------------------------------------------
    def on_odom(self, msg: Odometry) -> None:
        cov = msg.pose.covariance
        if max(cov[0], cov[7]) > self.cov_gate:
            return  # 位置品質が悪いサンプルは判定に使わない
        q = msg.pose.pose.orientation
        self.pose = (
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            yaw_from_quaternion(q),
            msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
        )
        self.pose_count += 1
        if self.state == "Spawned":
            self.set_state("Grounded")
        elif self.state == "Grounded" and self.pose_count >= self.ready_pose_count:
            self.set_state("Ready")

    def on_v2x(self, msg: V2XVehiclePositionArray) -> None:
        now = self.now_sec()
        self.others = [
            OtherVehicle(v.vehicle_id, v.position.x, v.position.y, now)
            for v in msg.vehicles
            if v.vehicle_id != self.vehicle_id
        ]

    def on_imu(self, msg: Imu) -> None:
        a = msg.linear_acceleration
        if math.sqrt(a.x * a.x + a.y * a.y) >= self.imu_threshold:
            self.last_imu_spike = self.now_sec()

    def on_admin_state(self, msg: String) -> None:
        if msg.data.lower() == "start" and self.go_time is None and self.state in ("Ready", "Grounded"):
            self.go_time = self.now_sec()
            self.laps.start(0.0)
            self.set_state("Start")

    # --- judgment loop -----------------------------------------------
    def on_judge_timer(self) -> None:
        if self.pose is None:
            return
        x, y, yaw, _ = self.pose
        progress, self.hint = self.track.progress_at((x, y), hint=self.hint)
        rt = self.race_time()

        outside = self.boundary.footprint_outside(
            footprint_corners(x, y, yaw, self.vehicle_length, self.vehicle_width,
                              self.antenna_offset_x)
        )
        self.pub_deviation.publish(Bool(data=outside))

        if self.state != "Start" or self.finished:
            return

        if self.laps.update(progress, rt):
            self.get_logger().info(
                f"lap {self.laps.lap_count} completed: {self.laps.lap_times[-1]:.3f}s")

        lap_now = self.laps.lap_count + 1
        if outside:
            self.penalty.trigger(PenaltyKind.WALL, lap_now, rt)
        if self.imu_ok():
            for other_id in self.collision.rear_ended_ids(x, y, yaw, self.now_sec(), self.others):
                self.get_logger().warn(f"collision with {other_id} at race_time={rt:.2f}")
                self.penalty.trigger(PenaltyKind.CRASH, lap_now, rt)

        for event in self.penalty.update(rt):
            self.pub_penalty_event.publish(String(data=str(event.to_dict())))
        self.pub_penalty_active.publish(Bool(data=self.penalty.is_active(rt)))

        lap_msg = Float32MultiArray()
        lap_msg.data = [
            float(self.laps.lap_count),
            float(progress),
            float(self.laps.lap_times[-1]) if self.laps.lap_times else 0.0,
            float(self.laps.lap_count) + float(progress),
        ]
        self.pub_lap.publish(lap_msg)

        if self.laps.lap_count >= self.required_laps:
            self.finished = True
            self.set_state("Finish")
            self.write_details()
        elif rt > self.session_timeout:
            self.set_state("Finish")
            self.write_details()

    def imu_ok(self) -> bool:
        if not self.use_imu:
            return True
        return (self.last_imu_spike is not None
                and self.now_sec() - self.last_imu_spike <= self.imu_window)

    # --- outputs -----------------------------------------------------
    def publish_status(self) -> None:
        if self.state != "Start":
            return
        rt = self.race_time()
        msg = Float32MultiArray()
        # AWSIM互換: [sessionTime, lapCount, lapTime, section, timeScale, boost, boostActive]
        msg.data = [rt, float(self.laps.lap_count),
                    self.laps.current_lap_elapsed(rt), 0.0, 1.0, 0.0, 0.0]
        self.pub_status.publish(msg)

    def publish_state(self) -> None:
        self.pub_state.publish(String(data=self.state))

    def write_details(self) -> None:
        if self.details_written:
            return
        self.details_written = True
        self.penalty.finalize_all()
        data = build_details(
            vehicle_name=f"GoKart{self.vehicle_number}",
            vehicle_number=self.vehicle_number,
            finished=self.finished,
            laps=self.laps.lap_times,
            required_laps=self.required_laps,
            session_timeout=self.session_timeout,
            penalty_events=self.penalty.events,
            penalty_by_kind=self.penalty.by_kind(),
            penalty_total_seconds=self.penalty.union_total_seconds(),
        )
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, f"d{self.vehicle_number}-result-details.json")
        atomic_write_json(path, data)
        self.get_logger().info(f"details written: {path}")


def main(args=None):
    rclpy.init(args=args)
    node = VehicleJudgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.go_time is not None:
            node.write_details()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
