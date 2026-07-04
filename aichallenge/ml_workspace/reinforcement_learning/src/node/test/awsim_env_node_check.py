#!/usr/bin/env python3
"""
AWSIMEnvNode publish/subscribe health checker.

使い方例:
  python3 node/awsim_env_node_check.py --duration 20

確認内容:
- 購読対象トピック:
  - publisher が存在するか
  - AWSIMEnvNode 側で実際に受信できているか
- 発行対象トピック:
  - subscriber が存在するか
  - （任意）テスト publish を実行
"""

import argparse
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import rclpy
from std_msgs.msg import Bool, Empty

from awsim_env_node import (
    AWSIMEnvNode,
    AWSIM_STATUS_FIELDS,
    TOPIC_AWSIM_STATE,
    TOPIC_AWSIM_STATUS,
    TOPIC_CAMERA_IMAGE,
    TOPIC_CAMERA_INFO,
    TOPIC_CLOCK,
    TOPIC_GEAR_STATUS,
    TOPIC_GNSS,
    TOPIC_IMU,
    TOPIC_KINEMATIC_STATE,
    TOPIC_LIDAR,
    TOPIC_TRAJECTORY,
    TOPIC_VELOCITY_STATUS,
    TOPIC_STEERING_STATUS,
    TOPIC_CONTROL_CMD,
    TOPIC_ALT_CONTROL_CMD,
    TOPIC_RESET,
    TOPIC_CONTROL_MODE,
    TOPIC_V2X_GNSS,
)


@dataclass
class TopicCheck:
    name: str
    has_data: Callable[[AWSIMEnvNode], bool]


def _bool_mark(value: bool) -> str:
    return "OK" if value else "NG"


def _print_section(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _check_subscribed_topics(node: AWSIMEnvNode, checks: List[TopicCheck]):
    _print_section("[SUBSCRIPTIONS] publisher presence and message reception")
    print(f"{'topic':50} {'pub':>5} {'recv':>6}")
    print("-" * 72)

    all_ok = True
    for c in checks:
        pub_count = len(node.get_publishers_info_by_topic(c.name))
        received = c.has_data(node)
        ok = (pub_count > 0) and received
        all_ok = all_ok and ok
        print(f"{c.name:50} {pub_count:5d} {_bool_mark(received):>6}")

    print("-" * 72)
    print(f"subscriptions overall: {_bool_mark(all_ok)}")
    return all_ok


def _check_published_topics(node: AWSIMEnvNode, topics: List[str]):
    _print_section("[PUBLISHERS] subscriber connection status")
    print(f"{'topic':50} {'sub':>5}")
    print("-" * 72)

    all_ok = True
    for topic in topics:
        sub_count = len(node.get_subscriptions_info_by_topic(topic))
        ok = sub_count > 0
        all_ok = all_ok and ok
        print(f"{topic:50} {sub_count:5d}")

    print("-" * 72)
    print(f"publishers overall: {_bool_mark(all_ok)}")
    return all_ok


def _run_optional_publish_test(node: AWSIMEnvNode, enable: bool):
    if not enable:
        return

    _print_section("[PUBLISH TEST] sending one-shot control and mode/reset")
    print("Publishing zero control command once...")
    node.publish_control(steering=0.0, acceleration=0.0)

    print("Publishing alt zero control command once...")
    node.publish_alt_control(steering=0.0, acceleration=0.0)

    print("Publishing control mode Auto=True once...")
    node.pub_control_mode.publish(Bool(data=True))

    print("Publishing empty reset once...")
    node.pub_reset.publish(Empty())

    print("Note: reset publish can affect running simulation state.")


def _build_subscription_checks() -> List[TopicCheck]:
    return [
        TopicCheck(TOPIC_CAMERA_IMAGE, lambda n: n.sensing_camera_image_raw is not None),
        TopicCheck(TOPIC_CAMERA_INFO, lambda n: n.sensing_camera_camera_info is not None),
        TopicCheck(TOPIC_CLOCK, lambda n: n.clock is not None),
        TopicCheck(TOPIC_GNSS, lambda n: n.sensing_gnss_nav_sat_fix is not None),
        TopicCheck(TOPIC_IMU, lambda n: n.sensing_imu_imu_raw is not None),
        TopicCheck(TOPIC_LIDAR, lambda n: n.sensing_lidar_scan is not None),
        TopicCheck(TOPIC_GEAR_STATUS, lambda n: n.vehicle_status_gear_status is not None),
        TopicCheck(TOPIC_STEERING_STATUS, lambda n: n.vehicle_status_steering_status is not None),
        TopicCheck(TOPIC_VELOCITY_STATUS, lambda n: n.vehicle_status_velocity_status is not None),
        TopicCheck(
            TOPIC_AWSIM_STATUS,
            lambda n: n.awsim_status is not None and all(k in n.awsim_status for k in AWSIM_STATUS_FIELDS),
        ),
        TopicCheck(TOPIC_AWSIM_STATE, lambda n: n.awsim_state is not None and n.awsim_state.data != ""),
        TopicCheck(TOPIC_KINEMATIC_STATE, lambda n: n.localization_kinematic_state is not None),
        TopicCheck(TOPIC_TRAJECTORY, lambda n: n.planning_scenario_planning_trajectory is not None),
    ]


def main():
    parser = argparse.ArgumentParser(description="AWSIMEnvNode topic connectivity checker")
    parser.add_argument("--duration", type=float, default=15.0, help="spin duration in seconds")
    parser.add_argument("--spin-timeout", type=float, default=0.1, help="spin_once timeout in seconds")
    parser.add_argument("--publish-test", action="store_true", help="send one-shot test publish")
    args = parser.parse_args()

    rclpy.init()
    node: Optional[AWSIMEnvNode] = None

    try:
        node = AWSIMEnvNode()

        print(f"Spinning for {args.duration:.1f}s to collect incoming messages...")
        end_t = time.time() + args.duration
        while rclpy.ok() and time.time() < end_t:
            rclpy.spin_once(node, timeout_sec=args.spin_timeout)

        sub_checks = _build_subscription_checks()
        pub_topics = [
            TOPIC_CONTROL_CMD,
            TOPIC_ALT_CONTROL_CMD,
            TOPIC_RESET,
            TOPIC_CONTROL_MODE,
            TOPIC_V2X_GNSS,
        ]

        sub_ok = _check_subscribed_topics(node, sub_checks)
        pub_ok = _check_published_topics(node, pub_topics)
        _run_optional_publish_test(node, args.publish_test)

        _print_section("[SUMMARY]")
        print(f"subscriptions: {_bool_mark(sub_ok)}")
        print(f"publishers:    {_bool_mark(pub_ok)}")

        if sub_ok and pub_ok:
            print("All checks passed.")
        else:
            print("Some checks failed. Verify simulator nodes and topic remapping/QoS.")

    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
