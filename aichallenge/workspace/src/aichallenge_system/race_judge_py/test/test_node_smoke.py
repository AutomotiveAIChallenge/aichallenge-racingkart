"""vehicle_judge_node の結合スモーク: 合成オドメトリで2周走らせ、
状態遷移(Grounded→Ready→Start→Finish)とラップ計測、details JSON出力を検証する。"""
import json
import math
import time
from pathlib import Path

import pytest

rclpy = pytest.importorskip("rclpy")

from nav_msgs.msg import Odometry  # noqa: E402
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from std_msgs.msg import String  # noqa: E402

LATCHED_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini_course.osm"


def make_odom(x, y, yaw, t_sec):
    msg = Odometry()
    msg.header.frame_id = "map"
    msg.header.stamp.sec = int(t_sec)
    msg.header.stamp.nanosec = int((t_sec % 1.0) * 1e9)
    msg.pose.pose.position.x = float(x)
    msg.pose.pose.position.y = float(y)
    msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
    return msg


def loop_points(n=40):
    """mini_course のセンターライン(y=8 から x=0→20、y=2 から x=20→0 の閉ループ)上の点列。

    センターライン原点は (0, 8) (progress=0)。
    各ループ終端は (0, 2) (progress≈0.88)。
    ループをまたぐ遷移(次ループ先頭 (0, 8) progress=0)で high(>0.70)→low(<0.30) が発生し
    LapCounter がラップ完了を検出する。"""
    pts = []
    for i in range(n // 2):
        pts.append((20.0 * i / (n // 2 - 1), 8.0, 0.0))
    for i in range(n // 2):
        pts.append((20.0 - 20.0 * i / (n // 2 - 1), 2.0, math.pi))
    return pts


def test_vehicle_judge_two_laps(tmp_path):
    pytest.importorskip("v2x_msgs")
    from rclpy.parameter import Parameter

    from race_judge_py.vehicle_judge_node import VehicleJudgeNode

    rclpy.init()
    try:
        node = VehicleJudgeNode(parameter_overrides=[
            Parameter("map_path", value=str(FIXTURE)),
            Parameter("output_dir", value=str(tmp_path)),
            Parameter("required_laps", value=2),
            Parameter("ready_pose_count", value=3),
        ])
        helper = rclpy.create_node("test_helper")
        pub_odom = helper.create_publisher(Odometry, "/localization/kinematic_state", 10)
        pub_admin = helper.create_publisher(String, "/admin/awsim/state", LATCHED_QOS)
        states = []
        helper.create_subscription(String, "/awsim/state", lambda m: states.append(m.data), 10)

        executor = rclpy.executors.SingleThreadedExecutor()
        executor.add_node(node)
        executor.add_node(helper)

        def spin(sec):
            end = time.time() + sec
            while time.time() < end:
                executor.spin_once(timeout_sec=0.02)

        # Grounded → Ready: publish 5 odometry samples at (1, 8) (ready_pose_count=3)
        for i in range(5):
            pub_odom.publish(make_odom(1.0, 8.0, 0.0, float(i)))
            spin(0.1)
        assert node.state == "Ready"

        # スタート指示
        pub_admin.publish(String(data="start"))
        spin(0.3)
        assert node.state == "Start"

        # 2周走行
        # ループ構造: loop_points() は (0,8)→(20,8)→(20,2)→(0,2) の40点
        # ラップ検出タイミング:
        #   - ループ1終端(0,2) progress≈0.88 → ループ2先頭(0,8) progress=0 で lap1完了
        #   - ループ2終端(0,2) progress≈0.88 → extra sample(0,8) progress=0 で lap2完了
        t = 0.0
        for _ in range(2):
            for (x, y, yaw) in loop_points():
                t += 0.05
                pub_odom.publish(make_odom(x, y, yaw, t))
                spin(0.06)
        # extra sample: ループ2終端から progress=0 へ跨いで2周目ラップを完了させる
        pub_odom.publish(make_odom(0.0, 8.0, 0.0, t + 0.05))
        spin(0.3)

        assert node.laps.lap_count >= 2, f"lap_count={node.laps.lap_count}"
        assert node.state == "Finish", f"state={node.state}"

        details_path = tmp_path / "d1-result-details.json"
        assert details_path.exists(), f"details file not written: {details_path}"
        details = json.loads(details_path.read_text())
        assert details["schema_version"] == "v3"
        assert details["finished"] is True
        assert details["lap_count"] >= 2

        executor.remove_node(node)
        executor.remove_node(helper)
        node.destroy_node()
        helper.destroy_node()
    finally:
        if rclpy.ok():
            rclpy.shutdown()
