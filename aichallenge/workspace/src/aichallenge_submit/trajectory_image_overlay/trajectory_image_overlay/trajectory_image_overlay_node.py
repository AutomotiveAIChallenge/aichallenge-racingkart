#!/usr/bin/env python3
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
import rclpy
from autoware_auto_planning_msgs.msg import Trajectory
from geometry_msgs.msg import Point, TransformStamped
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray


@dataclass
class PointCloud2D:
    frame_id: str
    points: List[Point]


@dataclass
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float


class TrajectoryImageOverlayNode(Node):
    def __init__(self) -> None:
        super().__init__("trajectory_image_overlay_node")

        self._declare_parameters()
        self._camera_info: Optional[Intrinsics] = None
        self._camera_info_frame = ""
        self._trajectory: Optional[PointCloud2D] = None
        self._mpc_prediction: Optional[PointCloud2D] = None

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        image_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        data_qos = QoSProfile(depth=1)
        trajectory_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        self._topic_subscriptions = [
            self.create_subscription(Image, self._p("image_topic"), self._on_image, image_qos),
            self.create_subscription(
                CameraInfo, self._p("camera_info_topic"), self._on_camera_info, data_qos
            ),
            self.create_subscription(
                Trajectory, self._p("trajectory_topic"), self._on_trajectory, trajectory_qos
            ),
            self.create_subscription(
                MarkerArray,
                self._p("mpc_prediction_topic"),
                self._on_mpc_prediction,
                data_qos,
            ),
        ]
        self._pub = self.create_publisher(Image, self._p("output_image_topic"), image_qos)

        self.get_logger().info(
            "Trajectory image overlay ready: "
            f"image={self._p('image_topic')}, trajectory={self._p('trajectory_topic')}, "
            f"mpc={self._p('mpc_prediction_topic')}, output={self._p('output_image_topic')}"
        )

    def _declare_parameters(self) -> None:
        self.declare_parameter("image_topic", "/sensing/camera/image_raw")
        self.declare_parameter("camera_info_topic", "/sensing/camera/camera_info")
        self.declare_parameter("trajectory_topic", "/planning/scenario_planning/trajectory")
        self.declare_parameter("mpc_prediction_topic", "/mpc/prediction")
        self.declare_parameter("output_image_topic", "/trajectory_image_overlay/image")
        self.declare_parameter("camera_frame", "camera_optical_link")
        self.declare_parameter("max_trajectory_points", 0)
        self.declare_parameter("max_mpc_points", 100)
        self.declare_parameter("min_depth_m", 0.1)
        self.declare_parameter("mpc_prediction_z_from_trajectory", True)
        self.declare_parameter("trajectory_color_bgr", [0, 255, 0])
        self.declare_parameter("mpc_prediction_color_bgr", [0, 0, 255])
        self.declare_parameter("line_thickness", 3)
        self.declare_parameter("point_radius", 4)
        self.declare_parameter("fallback_horizontal_fov_deg", 90.0)
        self.declare_parameter("fallback_vertical_fov_deg", 60.0)

    def _p(self, name: str):
        return self.get_parameter(name).value

    def _on_camera_info(self, msg: CameraInfo) -> None:
        if msg.k[0] <= 0.0 or msg.k[4] <= 0.0:
            return
        self._camera_info = Intrinsics(fx=msg.k[0], fy=msg.k[4], cx=msg.k[2], cy=msg.k[5])
        self._camera_info_frame = msg.header.frame_id

    def _on_trajectory(self, msg: Trajectory) -> None:
        points = [point.pose.position for point in msg.points]
        self._trajectory = PointCloud2D(frame_id=msg.header.frame_id or "map", points=points)

    def _on_mpc_prediction(self, msg: MarkerArray) -> None:
        points: List[Point] = []
        frame_id = "map"
        for marker in msg.markers:
            if marker.action not in (Marker.ADD, Marker.MODIFY):
                continue
            frame_id = marker.header.frame_id or frame_id
            if marker.points:
                points.extend(self._normalize_mpc_points(marker.points))
            else:
                points.extend(self._normalize_mpc_points([marker.pose.position]))
        self._mpc_prediction = PointCloud2D(frame_id=frame_id, points=points)

    def _on_image(self, msg: Image) -> None:
        image = self._image_msg_to_bgr(msg)
        if image is None:
            return

        intrinsics = self._camera_info or self._fallback_intrinsics(msg.width, msg.height)
        camera_frame = self._camera_frame(msg)

        if self._trajectory is not None:
            self._draw_cloud(
                image,
                self._trajectory,
                camera_frame,
                intrinsics,
                self._color("trajectory_color_bgr"),
                int(self._p("max_trajectory_points")),
                draw_points=False,
            )

        if self._mpc_prediction is not None:
            self._draw_cloud(
                image,
                self._mpc_prediction,
                camera_frame,
                intrinsics,
                self._color("mpc_prediction_color_bgr"),
                int(self._p("max_mpc_points")),
                draw_points=True,
            )

        self._pub.publish(self._bgr_to_image_msg(image, msg.header))

    def _camera_frame(self, image_msg: Image) -> str:
        configured = str(self._p("camera_frame")).strip()
        if configured:
            return configured
        if self._camera_info_frame:
            return self._camera_info_frame
        if image_msg.header.frame_id:
            return image_msg.header.frame_id
        return "camera_optical_link"

    def _normalize_mpc_points(self, points: Sequence[Point]) -> List[Point]:
        if not bool(self._p("mpc_prediction_z_from_trajectory")):
            return list(points)

        trajectory_z = self._trajectory_z()
        if trajectory_z is None:
            return list(points)

        normalized = []
        for point in points:
            normalized_point = Point()
            normalized_point.x = point.x
            normalized_point.y = point.y
            normalized_point.z = trajectory_z if abs(point.z) < 1e-6 else point.z
            normalized.append(normalized_point)
        return normalized

    def _trajectory_z(self) -> Optional[float]:
        if self._trajectory is None or not self._trajectory.points:
            return None
        return float(np.median([point.z for point in self._trajectory.points]))

    def _fallback_intrinsics(self, width: int, height: int) -> Intrinsics:
        h_fov = math.radians(float(self._p("fallback_horizontal_fov_deg")))
        v_fov = math.radians(float(self._p("fallback_vertical_fov_deg")))
        fx = width / (2.0 * math.tan(h_fov / 2.0))
        fy = height / (2.0 * math.tan(v_fov / 2.0))
        return Intrinsics(fx=fx, fy=fy, cx=width / 2.0, cy=height / 2.0)

    def _draw_cloud(
        self,
        image: np.ndarray,
        cloud: PointCloud2D,
        camera_frame: str,
        intrinsics: Intrinsics,
        color: Tuple[int, int, int],
        max_points: int,
        draw_points: bool,
    ) -> None:
        transform = self._lookup_transform(camera_frame, cloud.frame_id)
        if transform is None:
            return

        points = cloud.points
        projected = [self._project_point(point, transform, intrinsics, image.shape) for point in points]
        projected = self._limit_projected_points(projected, max_points)
        valid_points = [pixel for pixel in projected if pixel is not None]

        thickness = int(self._p("line_thickness"))
        radius = int(self._p("point_radius"))
        previous: Optional[Tuple[int, int]] = None
        for pixel in projected:
            if pixel is None:
                previous = None
                continue
            if previous is not None:
                cv2.line(image, previous, pixel, color, thickness, lineType=cv2.LINE_AA)
            if draw_points:
                cv2.circle(image, pixel, radius, color, -1, lineType=cv2.LINE_AA)
            previous = pixel

        if len(valid_points) == 1:
            cv2.circle(image, valid_points[0], radius, color, -1, lineType=cv2.LINE_AA)

    @staticmethod
    def _limit_projected_points(
        projected: List[Optional[Tuple[int, int]]], max_points: int
    ) -> List[Optional[Tuple[int, int]]]:
        if max_points <= 0:
            return projected

        limited: List[Optional[Tuple[int, int]]] = []
        valid_count = 0
        for pixel in projected:
            limited.append(pixel)
            if pixel is not None:
                valid_count += 1
                if valid_count >= max_points:
                    break
        return limited

    def _lookup_transform(self, target_frame: str, source_frame: str) -> Optional[TransformStamped]:
        try:
            return self._tf_buffer.lookup_transform(target_frame, source_frame, Time())
        except TransformException as exc:
            self.get_logger().warn(
                f"TF lookup failed: {source_frame} -> {target_frame}: {exc}",
                throttle_duration_sec=5.0,
            )
            return None

    def _project_point(
        self,
        point: Point,
        transform: TransformStamped,
        intrinsics: Intrinsics,
        image_shape: Tuple[int, int, int],
    ) -> Optional[Tuple[int, int]]:
        x, y, z = self._transform_point(point, transform)
        if z <= float(self._p("min_depth_m")):
            return None

        u = int(round(intrinsics.fx * x / z + intrinsics.cx))
        v = int(round(intrinsics.fy * y / z + intrinsics.cy))
        height, width = image_shape[:2]
        if u < 0 or u >= width or v < 0 or v >= height:
            return None
        return (u, v)

    def _transform_point(self, point: Point, transform: TransformStamped) -> Tuple[float, float, float]:
        rotation = transform.transform.rotation
        translation = transform.transform.translation
        matrix = self._quaternion_to_matrix(rotation.x, rotation.y, rotation.z, rotation.w)
        vector = np.array([point.x, point.y, point.z], dtype=np.float64)
        transformed = matrix @ vector
        transformed += np.array([translation.x, translation.y, translation.z], dtype=np.float64)
        return float(transformed[0]), float(transformed[1]), float(transformed[2])

    @staticmethod
    def _quaternion_to_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
        norm = x * x + y * y + z * z + w * w
        if norm < 1e-12:
            return np.identity(3)
        scale = 2.0 / norm
        xx, yy, zz = x * x * scale, y * y * scale, z * z * scale
        xy, xz, yz = x * y * scale, x * z * scale, y * z * scale
        wx, wy, wz = w * x * scale, w * y * scale, w * z * scale
        return np.array(
            [
                [1.0 - yy - zz, xy - wz, xz + wy],
                [xy + wz, 1.0 - xx - zz, yz - wx],
                [xz - wy, yz + wx, 1.0 - xx - yy],
            ],
            dtype=np.float64,
        )

    def _image_msg_to_bgr(self, msg: Image) -> Optional[np.ndarray]:
        try:
            if msg.encoding == "bgr8":
                return np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3).copy()
            if msg.encoding == "rgb8":
                rgb = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)
                return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if msg.encoding == "bgra8":
                bgra = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 4)
                return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
            if msg.encoding == "rgba8":
                rgba = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 4)
                return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
            self.get_logger().warn(
                f"Unsupported image encoding: {msg.encoding}",
                throttle_duration_sec=5.0,
            )
            return None
        except ValueError as exc:
            self.get_logger().warn(f"Image conversion failed: {exc}", throttle_duration_sec=5.0)
            return None

    @staticmethod
    def _bgr_to_image_msg(image: np.ndarray, header) -> Image:
        msg = Image()
        msg.header = header
        msg.height = int(image.shape[0])
        msg.width = int(image.shape[1])
        msg.encoding = "bgr8"
        msg.is_bigendian = 0
        msg.step = int(image.shape[1] * 3)
        msg.data = image.tobytes()
        return msg

    def _color(self, parameter_name: str) -> Tuple[int, int, int]:
        value = self._p(parameter_name)
        if len(value) != 3:
            return (255, 255, 255)
        return (int(value[0]), int(value[1]), int(value[2]))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = TrajectoryImageOverlayNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
