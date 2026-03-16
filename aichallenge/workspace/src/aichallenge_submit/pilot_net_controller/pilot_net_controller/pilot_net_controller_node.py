#!/usr/bin/env python3
import time
import numpy as np
import cv2

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from autoware_auto_control_msgs.msg import AckermannControlCommand

from pilot_net_controller_core import PilotNetCore


class PilotNetNode(Node):
    """ROS 2 Node for PilotNet autonomous driving control.

    Subscribes to camera Image messages, processes them using PilotNetCore,
    and publishes AckermannControlCommand messages.
    """

    def __init__(self):
        super().__init__('pilot_net_node')

        # --- Parameter Declaration (same pattern as TinyLidarNetNode) ---
        self.declare_parameter('log_interval_sec', 5.0)
        self.declare_parameter('model.image_height', 256)
        self.declare_parameter('model.image_width', 384)
        self.declare_parameter('model.output_dim', 2)
        self.declare_parameter('model.ckpt_path', '')
        self.declare_parameter('acceleration', 0.1)
        self.declare_parameter('control_mode', 'ai')
        self.declare_parameter('debug', False)

        # --- Initialization ---
        image_height = self.get_parameter('model.image_height').value
        image_width = self.get_parameter('model.image_width').value
        output_dim = self.get_parameter('model.output_dim').value
        ckpt_path = self.get_parameter('model.ckpt_path').value
        acceleration = self.get_parameter('acceleration').value
        control_mode = self.get_parameter('control_mode').value

        self.debug = self.get_parameter('debug').value
        self.log_interval = self.get_parameter('log_interval_sec').value

        try:
            self.core = PilotNetCore(
                image_height=image_height,
                image_width=image_width,
                output_dim=output_dim,
                ckpt_path=ckpt_path,
                acceleration=acceleration,
                control_mode=control_mode,
            )
            self.get_logger().info(
                f"Core initialized. Image: {image_height}x{image_width}, Mode: {control_mode}"
            )
        except Exception as e:
            self.get_logger().error(f"Failed to initialize core logic: {e}")
            raise

        # --- Communication Setup ---
        self.inference_times = []
        self.last_log_time = self.get_clock().now()

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        self.sub_image = self.create_subscription(
            Image, "/image_raw", self.image_callback, qos
        )
        self.pub_control = self.create_publisher(
            AckermannControlCommand, "/awsim/control_cmd", 1
        )

        self.get_logger().info("PilotNetNode is ready.")

    def image_callback(self, msg: Image):
        """Callback for Image subscription."""
        start_time = time.monotonic()

        # 1. Convert ROS Image to NumPy array
        image = self._image_msg_to_numpy(msg)
        if image is None:
            return

        # 2. Process via Core Logic
        accel, steer = self.core.process(image)

        # 3. Publish Command
        cmd = AckermannControlCommand()
        cmd.stamp = self.get_clock().now().to_msg()
        cmd.longitudinal.acceleration = float(accel)
        cmd.lateral.steering_tire_angle = float(steer)
        self.pub_control.publish(cmd)

        # 4. Debug Logging
        if self.debug:
            duration_ms = (time.monotonic() - start_time) * 1000.0
            self.inference_times.append(duration_ms)
            self._log_performance_metrics()

    def _image_msg_to_numpy(self, msg: Image) -> np.ndarray:
        """Converts a ROS Image message to a NumPy array (H, W, 3) RGB uint8."""
        try:
            # Get raw data as numpy array
            if msg.encoding == 'bgr8':
                img = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            elif msg.encoding == 'rgb8':
                img = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3).copy()
            elif msg.encoding == 'bgra8':
                img = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 4)
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
            elif msg.encoding == 'rgba8':
                img = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 4)
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
            else:
                self.get_logger().warn(f"Unsupported image encoding: {msg.encoding}", throttle_duration_sec=5.0)
                return None
            return img
        except Exception as e:
            self.get_logger().error(f"Image conversion failed: {e}", throttle_duration_sec=5.0)
            return None

    def _log_performance_metrics(self):
        """Logs performance metrics at fixed intervals (same as TinyLidarNetNode)."""
        now = self.get_clock().now()
        elapsed_sec = (now - self.last_log_time).nanoseconds / 1e9

        if elapsed_sec > self.log_interval:
            if self.inference_times:
                avg_time = np.mean(self.inference_times)
                max_time = np.max(self.inference_times)
                fps = 1000.0 / avg_time if avg_time > 0 else 0.0

                self.get_logger().info(
                    f"DEBUG: Avg Inference: {avg_time:.2f}ms ({fps:.2f}Hz) | "
                    f"Max: {max_time:.2f}ms"
                )
                self.inference_times.clear()

            self.last_log_time = now


def main(args=None):
    rclpy.init(args=args)
    node = PilotNetNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
