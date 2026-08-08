#!/usr/bin/env python3

import sys
import time

# ROS 2
import rclpy
from rclpy.parameter import Parameter
from geometry_msgs.msg import PoseWithCovarianceStamped


def main(argv = sys.argv) -> None:

    rclpy.init(args=argv)

    try:
        node = rclpy.create_node('initialpose_pub_node')
        pub = node.create_publisher(PoseWithCovarianceStamped, '/localization/initial_pose3d', 1)

        # set use_sim_time parameter
        param = Parameter("use_sim_time", Parameter.Type.BOOL, True)
        node.set_parameters([param])

        # wait for clock received
        rclpy.spin_once(node, timeout_sec=1)

        while rclpy.ok() and pub.get_subscription_count() == 0:
            node.get_logger().info("Waiting for subscriber...")
            time.sleep(0.5)

        # create initialpose message
        initialpose = PoseWithCovarianceStamped()
        initialpose.header.stamp = node.get_clock().now().to_msg()
        initialpose.header.frame_id = 'map'
        initialpose.pose.pose.position.x = 89633.29
        initialpose.pose.pose.position.y = 43127.57
        initialpose.pose.pose.position.z = 0.0
        initialpose.pose.pose.orientation.x= 0.0
        initialpose.pose.pose.orientation.y= -0.0
        initialpose.pose.pose.orientation.z= 0.8778
        initialpose.pose.pose.orientation.w= 0.4788
        initialpose.pose.covariance[0] = 0.25
        initialpose.pose.covariance[7] = 0.25
        initialpose.pose.covariance[35] = 0.06853891909122467

        # publish initialpose
        for _ in range(5):
            pub.publish(initialpose)
            time.sleep(0.2)

    except KeyboardInterrupt:
        node.get_logger().info("Shutdown requested")
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main(sys.argv)
