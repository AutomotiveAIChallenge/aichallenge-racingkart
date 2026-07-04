#!/usr/bin/env python3
"""
ROS2 Domain Bridge Node
DOMAIN=1 の /awsim/reset を受け取り、DOMAIN=0 の /admin/awsim/reset に転送する
.launch.xml から起動することを想定
"""

import threading

import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Empty


class DomainBridgeNode(Node):
    """ROS2 Domain Bridge Node for forwarding reset commands.

    Subscribes to /awsim/reset on DOMAIN=1,
    and publishes to /admin/awsim/reset on DOMAIN=0.
    """

    def __init__(self, ctx0: Context, ctx1: Context):
        # このノード自体は DOMAIN=1 側（subscriber）
        super().__init__('rl_train_node', context=ctx1)

        self.declare_parameter('src_topic',  '/awsim/reset')
        self.declare_parameter('dst_topic',  '/admin/awsim/reset')

        src_topic = self.get_parameter('src_topic').value
        dst_topic = self.get_parameter('dst_topic').value

        # --- DOMAIN=0 側の publisher ノード ---
        self._pub_node = rclpy.create_node('rl_train_node_pub', context=ctx0)
        self._pub = self._pub_node.create_publisher(Empty, dst_topic, 10)

        # --- DOMAIN=1 側の subscriber ---
        self.create_subscription(Empty, src_topic, self._reset_cb, 10)

        self.get_logger().info(
            f"DomainBridgeNode ready. "
            f"DOMAIN=1:{src_topic} -> DOMAIN=0:{dst_topic}"
        )

    def _reset_cb(self, msg: Empty):
        """Callback for /awsim/reset on DOMAIN=1."""
        self.get_logger().info("Received reset on DOMAIN=1 -> forwarding to DOMAIN=0")
        self._pub.publish(Empty())

    def destroy_node(self):
        self._pub_node.destroy_node()
        super().destroy_node()


def main(args=None):
    # --- 2つの Context を用意し、それぞれ別の DOMAIN で init ---
    ctx0 = Context()
    rclpy.init(context=ctx0, domain_id=0, args=args)

    ctx1 = Context()
    rclpy.init(context=ctx1, domain_id=1, args=args)

    node = DomainBridgeNode(ctx0=ctx0, ctx1=ctx1)

    # --- DOMAIN=0 側の publisher ノードを別スレッドで spin ---
    exec0 = SingleThreadedExecutor(context=ctx0)
    exec0.add_node(node._pub_node)
    t = threading.Thread(target=exec0.spin, daemon=True)
    t.start()

    # --- DOMAIN=1 側（このノード）をメインスレッドで spin ---
    exec1 = SingleThreadedExecutor(context=ctx1)
    exec1.add_node(node)

    try:
        exec1.spin()
    except KeyboardInterrupt:
        pass
    finally:
        exec0.shutdown()
        exec1.shutdown()
        node.destroy_node()
        rclpy.shutdown(context=ctx0)
        rclpy.shutdown(context=ctx1)


if __name__ == '__main__':
    main()
