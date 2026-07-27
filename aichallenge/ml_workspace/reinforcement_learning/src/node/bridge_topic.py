#!/usr/bin/env python3
"""
ROS2 Domain Bridge
DOMAIN=1 の /awsim/reset を受け取り、DOMAIN=0 の /admin/awsim/reset に転送する
"""

import os
import threading
import rclpy
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from rclpy.context import Context
from std_msgs.msg import Empty


def main():
    # --- DOMAIN=0 context (publisher側) ---
    ctx0 = Context()
    rclpy.init(context=ctx0, domain_id=0)

    # --- DOMAIN=1 context (subscriber側) ---
    ctx1 = Context()
    rclpy.init(context=ctx1, domain_id=1)

    # --- DOMAIN=0: publisher ノード ---
    class PubNode(Node):
        def __init__(self):
            super().__init__('domain_bridge_publisher', context=ctx0)
            self.pub = self.create_publisher(Empty, '/admin/awsim/reset', 10)
            self.get_logger().info("DOMAIN=0 publisher ready: /admin/awsim/reset")

        def forward_reset(self):
            self.pub.publish(Empty())
            self.get_logger().info("Forwarded reset -> DOMAIN=0 /admin/awsim/reset")

    pub_node = PubNode()

    # --- DOMAIN=1: subscriber ノード ---
    class SubNode(Node):
        def __init__(self):
            super().__init__('domain_bridge_subscriber', context=ctx1)
            self.create_subscription(
                Empty,
                '/awsim/reset',
                self._cb,
                10
            )
            self.get_logger().info("DOMAIN=1 subscriber ready: /awsim/reset")

        def _cb(self, msg):
            self.get_logger().info("Received /awsim/reset on DOMAIN=1 -> forwarding...")
            pub_node.forward_reset()

    sub_node = SubNode()

    # --- DOMAIN=1 を別スレッドで spin ---
    exec1 = SingleThreadedExecutor(context=ctx1)
    exec1.add_node(sub_node)
    t = threading.Thread(target=exec1.spin, daemon=True)
    t.start()

    # --- DOMAIN=0 をメインスレッドで spin ---
    exec0 = SingleThreadedExecutor(context=ctx0)
    exec0.add_node(pub_node)
    print("Bridge running. Ctrl+C to stop.")
    try:
        exec0.spin()
    except KeyboardInterrupt:
        pass
    finally:
        exec0.shutdown()
        exec1.shutdown()
        pub_node.destroy_node()
        sub_node.destroy_node()
        rclpy.shutdown(context=ctx0)
        rclpy.shutdown(context=ctx1)
        print("Bridge stopped.")


if __name__ == '__main__':
    main()
