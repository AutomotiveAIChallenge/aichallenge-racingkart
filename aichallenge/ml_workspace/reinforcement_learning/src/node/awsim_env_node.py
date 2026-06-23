#!/usr/bin/env python3

import time

import cv2
import numpy as np
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy

from autoware_auto_control_msgs.msg import AckermannControlCommand
from autoware_auto_planning_msgs.msg import Trajectory
from autoware_auto_vehicle_msgs.msg import GearReport, SteeringReport, VelocityReport
from autoware_auto_mapping_msgs.msg import HADMapBin
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, TwistStamped, TwistWithCovarianceStamped, AccelWithCovarianceStamped
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, CameraInfo, NavSatFix, Imu, LaserScan
from tf2_msgs.msg import TFMessage
from tier4_debug_msgs.msg import BoolStamped, Float64Stamped, Float64MultiArrayStamped
from visualization_msgs.msg import MarkerArray
from std_msgs.msg import String, Float32MultiArray, Empty, Bool

# /awsim/status (Float32MultiArray) のフィールド順
# index | field          | 説明
# ------+----------------+-----------------------------
#   0   | session_time   | 残りセッション時間 [s]（カウントダウン）
#   1   | lap_count      | 現在のラップ数（報酬関数: 周回ボーナスに使用）
#   2   | lap_time       | 現在のラップタイム [s]（報酬関数: ログ出力に使用）
#   3   | section        | 現在のセクション番号（報酬関数: セクション通過ボーナスに使用）
#   4   | time_scale     | シミュレーションのタイムスケール
#   5   | boost_remaining| 残りブースト使用回数
#   6   | is_boosting    | ブースト中フラグ (1.0=ブースト中)
AWSIM_STATUS_FIELDS = (
    "session_time",
    "lap_count",
    "lap_time",
    "section",
    "time_scale",
    "boost_remaining",
    "is_boosting",
)

# DOMAIN=1 用の購読/発行トピック
TOPIC_AWSIM_STATE = '/awsim/state'
TOPIC_AWSIM_STATUS = '/awsim/status'
TOPIC_CLOCK = '/clock'
TOPIC_CAMERA_INFO = '/sensing/camera/camera_info'
TOPIC_CAMERA_IMAGE = '/sensing/camera/image_raw'
TOPIC_GNSS = '/sensing/gnss/nav_sat_fix'
TOPIC_GNSS_FIXED = '/sensing/gnss/gnss_fixed'
TOPIC_GNSS_POSE = '/sensing/gnss/pose'
TOPIC_GNSS_POSE_WITH_COV = '/sensing/gnss/pose_with_covariance'
TOPIC_IMU = '/sensing/imu/imu_raw'
TOPIC_IMU_DATA = '/sensing/imu/imu_data'
TOPIC_LIDAR = '/sensing/lidar/scan'
TOPIC_SENSING_VEHICLE_VEL_TWIST_WITH_COV = '/sensing/vehicle_velocity_converter/twist_with_covariance'
TOPIC_GEAR_STATUS = '/vehicle/status/gear_status'
TOPIC_STEERING_STATUS = '/vehicle/status/steering_status'
TOPIC_VELOCITY_STATUS = '/vehicle/status/velocity_status'
TOPIC_KINEMATIC_STATE = '/localization/kinematic_state'
TOPIC_LOCALIZATION_ACCELERATION = '/localization/acceleration'
TOPIC_LOCALIZATION_BIASED_POSE = '/localization/biased_pose'
TOPIC_LOCALIZATION_BIASED_POSE_WITH_COV = '/localization/biased_pose_with_covariance'
TOPIC_LOCALIZATION_DEBUG = '/localization/debug'
TOPIC_LOCALIZATION_ESTIMATED_YAW_BIAS = '/localization/estimated_yaw_bias'
TOPIC_LOCALIZATION_GYRO_TWIST = '/localization/gyro_twist'
TOPIC_LOCALIZATION_GYRO_TWIST_RAW = '/localization/gyro_twist_raw'
TOPIC_LOCALIZATION_IMU_GNSS_POSE_WITH_COV = '/localization/imu_gnss_poser/pose_with_covariance'
TOPIC_LOCALIZATION_POSE = '/localization/pose'
TOPIC_LOCALIZATION_POSE_WITH_COV = '/localization/pose_with_covariance'
TOPIC_LOCALIZATION_TWIST = '/localization/twist'
TOPIC_LOCALIZATION_TWIST_ESTIMATOR_WITH_COV = '/localization/twist_estimator/twist_with_covariance'
TOPIC_LOCALIZATION_TWIST_ESTIMATOR_WITH_COV_RAW = '/localization/twist_estimator/twist_with_covariance_raw'
TOPIC_LOCALIZATION_TWIST_WITH_COV = '/localization/twist_with_covariance'
TOPIC_HEADING_RACELINE_MARKERS = '/heading_pose_initializer/raceline_markers'
TOPIC_MAP_VECTOR_MAP = '/map/vector_map'
TOPIC_MAP_VECTOR_MAP_MARKER = '/map/vector_map_marker'
TOPIC_TRAJECTORY = '/planning/scenario_planning/trajectory'
TOPIC_ROBOT_DESCRIPTION = '/robot_description'
TOPIC_TF = '/tf'
TOPIC_TF_STATIC = '/tf_static'

TOPIC_CONTROL_CMD = '/awsim/control_cmd'
TOPIC_CONTROL_MODE = '/awsim/control_mode_request_topic'
TOPIC_RESET = '/awsim/reset'
TOPIC_ALT_CONTROL_CMD = '/control/command/control_cmd'
TOPIC_V2X_GNSS = '/v2x/gnss/nav_sat_fix'


# ============================================================
# ROS2 通信ノード
# ============================================================

class AWSIMEnvNode(Node):
    """
    AWSIM通信専用ノード（DOMAIN=1用）

    このノードは、RL環境が今すぐ使うトピックだけでなく、将来AWSIMEnvから
    参照したいセンサ/状態トピックもまとめて購読し、最新値を属性として保持する。

    現在のAWSIMEnvで必須のもの:
    - /awsim/state
    - /awsim/status
    - /sensing/camera/image_raw
    - /vehicle/status/velocity_status

    将来利用できるように保持しているもの:
    - /clock
    - /sensing/camera/camera_info
    - /sensing/gnss/nav_sat_fix
    - /sensing/imu/imu_raw
    - /sensing/lidar/scan
    - /vehicle/status/gear_status
    - /vehicle/status/steering_status
    - /localization/kinematic_state
    - /planning/scenario_planning/trajectory

    発行側では /awsim/control_cmd と /awsim/reset を使う。
    /control/command/control_cmd と /v2x/gnss/nav_sat_fix は将来の切替用に
    Publisher だけ用意してあるが、現状のAWSIMEnvでは未使用。
    """

    def __init__(self):
        super().__init__('awsim_rl_env_node')

        # --- 最新値の保持 ---
        self.sensing_camera_image_raw: np.ndarray = None
        self.sensing_camera_camera_info: CameraInfo = None
        self.clock: Clock = None
        self.sensing_gnss_nav_sat_fix: NavSatFix = None
        self.sensing_gnss_gnss_fixed: BoolStamped = None
        self.sensing_gnss_pose: PoseStamped = None
        self.sensing_gnss_pose_with_covariance: PoseWithCovarianceStamped = None
        self.sensing_imu_imu_raw: Imu = None
        self.sensing_imu_imu_data: Imu = None
        self.sensing_lidar_scan: LaserScan = None
        self.sensing_vehicle_velocity_converter_twist_with_covariance: TwistWithCovarianceStamped = None
        self.vehicle_status_gear_status: GearReport = None
        self.vehicle_status_steering_status: SteeringReport = None
        self.vehicle_status_velocity_status: VelocityReport = None
        self.localization_kinematic_state: Odometry = None
        self.localization_acceleration: AccelWithCovarianceStamped = None
        self.localization_biased_pose: PoseStamped = None
        self.localization_biased_pose_with_covariance: PoseWithCovarianceStamped = None
        self.localization_debug: Float64MultiArrayStamped = None
        self.localization_estimated_yaw_bias: Float64Stamped = None
        self.localization_gyro_twist: TwistStamped = None
        self.localization_gyro_twist_raw: TwistStamped = None
        self.localization_imu_gnss_poser_pose_with_covariance: PoseWithCovarianceStamped = None
        self.localization_pose: PoseStamped = None
        self.localization_pose_with_covariance: PoseWithCovarianceStamped = None
        self.localization_twist: TwistStamped = None
        self.localization_twist_estimator_twist_with_covariance: TwistWithCovarianceStamped = None
        self.localization_twist_estimator_twist_with_covariance_raw: TwistWithCovarianceStamped = None
        self.localization_twist_with_covariance: TwistWithCovarianceStamped = None
        self.heading_pose_initializer_raceline_markers: MarkerArray = None
        self.map_vector_map: HADMapBin = None
        self.map_vector_map_marker: MarkerArray = None
        self.planning_scenario_planning_trajectory: Trajectory = None
        self.robot_description: String = None
        self.tf: TFMessage = None
        self.tf_static: TFMessage = None
        self.awsim_status: dict[str, float] = None
        self.awsim_state: String = None
        self.clock_time_sec: float = 0.0  # /clock の latest 秒数
        self.awsim_control_cmd: AckermannControlCommand = None
        self.control_command_control_cmd: AckermannControlCommand = None
        self.awsim_reset: Empty = None
        self.awsim_control_mode_request_topic: Bool = None
        self.v2x_gnss_nav_sat_fix: NavSatFix = None

        # --- QoS設定 ---
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_reliable_transient_local = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # --- Subscribers ---
        self.create_subscription(
            Image,
            TOPIC_CAMERA_IMAGE,
            self._image_cb,
            qos_best_effort
        )
        self.create_subscription(
            CameraInfo,
            TOPIC_CAMERA_INFO,
            self._camera_info_cb,
            qos_best_effort
        )
        self.create_subscription(
            Clock,
            TOPIC_CLOCK,
            self._clock_cb,
            qos_best_effort
        )
        self.create_subscription(
            NavSatFix,
            TOPIC_GNSS,
            self._gnss_cb,
            qos_reliable
        )
        self.create_subscription(
            BoolStamped,
            TOPIC_GNSS_FIXED,
            self._gnss_fixed_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseStamped,
            TOPIC_GNSS_POSE,
            self._gnss_pose_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseWithCovarianceStamped,
            TOPIC_GNSS_POSE_WITH_COV,
            self._gnss_pose_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            Imu,
            TOPIC_IMU,
            self._imu_cb,
            qos_reliable
        )
        self.create_subscription(
            Imu,
            TOPIC_IMU_DATA,
            self._imu_data_cb,
            qos_reliable
        )
        self.create_subscription(
            LaserScan,
            TOPIC_LIDAR,
            self._lidar_cb,
            qos_best_effort
        )
        self.create_subscription(
            TwistWithCovarianceStamped,
            TOPIC_SENSING_VEHICLE_VEL_TWIST_WITH_COV,
            self._sensing_vehicle_vel_twist_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            GearReport,
            TOPIC_GEAR_STATUS,
            self._gear_status_cb,
            qos_reliable
        )
        self.create_subscription(
            SteeringReport,
            TOPIC_STEERING_STATUS,
            self._steering_status_cb,
            qos_reliable
        )
        self.create_subscription(
            VelocityReport,
            TOPIC_VELOCITY_STATUS,
            self._velocity_cb,
            qos_reliable
        )
        self.create_subscription(
            Float32MultiArray,
            TOPIC_AWSIM_STATUS,
            self._awsim_status_cb,
            qos_reliable
        )
        self.create_subscription(
            String,
            TOPIC_AWSIM_STATE,
            self._awsim_state_cb,
            qos_reliable_transient_local
        )
        self.create_subscription(
            Odometry,
            TOPIC_KINEMATIC_STATE,
            self._odometry_cb,
            qos_reliable
        )
        self.create_subscription(
            AccelWithCovarianceStamped,
            TOPIC_LOCALIZATION_ACCELERATION,
            self._localization_acceleration_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseStamped,
            TOPIC_LOCALIZATION_BIASED_POSE,
            self._localization_biased_pose_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseWithCovarianceStamped,
            TOPIC_LOCALIZATION_BIASED_POSE_WITH_COV,
            self._localization_biased_pose_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            Float64MultiArrayStamped,
            TOPIC_LOCALIZATION_DEBUG,
            self._localization_debug_cb,
            qos_reliable
        )
        self.create_subscription(
            Float64Stamped,
            TOPIC_LOCALIZATION_ESTIMATED_YAW_BIAS,
            self._localization_estimated_yaw_bias_cb,
            qos_reliable
        )
        self.create_subscription(
            TwistStamped,
            TOPIC_LOCALIZATION_GYRO_TWIST,
            self._localization_gyro_twist_cb,
            qos_reliable
        )
        self.create_subscription(
            TwistStamped,
            TOPIC_LOCALIZATION_GYRO_TWIST_RAW,
            self._localization_gyro_twist_raw_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseWithCovarianceStamped,
            TOPIC_LOCALIZATION_IMU_GNSS_POSE_WITH_COV,
            self._localization_imu_gnss_pose_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseStamped,
            TOPIC_LOCALIZATION_POSE,
            self._localization_pose_cb,
            qos_reliable
        )
        self.create_subscription(
            PoseWithCovarianceStamped,
            TOPIC_LOCALIZATION_POSE_WITH_COV,
            self._localization_pose_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            TwistStamped,
            TOPIC_LOCALIZATION_TWIST,
            self._localization_twist_cb,
            qos_reliable
        )
        self.create_subscription(
            TwistWithCovarianceStamped,
            TOPIC_LOCALIZATION_TWIST_ESTIMATOR_WITH_COV,
            self._localization_twist_estimator_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            TwistWithCovarianceStamped,
            TOPIC_LOCALIZATION_TWIST_ESTIMATOR_WITH_COV_RAW,
            self._localization_twist_estimator_with_cov_raw_cb,
            qos_reliable
        )
        self.create_subscription(
            TwistWithCovarianceStamped,
            TOPIC_LOCALIZATION_TWIST_WITH_COV,
            self._localization_twist_with_cov_cb,
            qos_reliable
        )
        self.create_subscription(
            MarkerArray,
            TOPIC_HEADING_RACELINE_MARKERS,
            self._raceline_markers_cb,
            qos_reliable_transient_local
        )
        self.create_subscription(
            HADMapBin,
            TOPIC_MAP_VECTOR_MAP,
            self._map_vector_map_cb,
            qos_reliable_transient_local
        )
        self.create_subscription(
            MarkerArray,
            TOPIC_MAP_VECTOR_MAP_MARKER,
            self._map_vector_map_marker_cb,
            qos_reliable_transient_local
        )
        self.create_subscription(
            Trajectory,
            TOPIC_TRAJECTORY,
            self._trajectory_cb,
            qos_best_effort
        )
        self.create_subscription(
            String,
            TOPIC_ROBOT_DESCRIPTION,
            self._robot_description_cb,
            qos_reliable_transient_local
        )
        self.create_subscription(
            TFMessage,
            TOPIC_TF,
            self._tf_cb,
            qos_reliable
        )
        self.create_subscription(
            TFMessage,
            TOPIC_TF_STATIC,
            self._tf_static_cb,
            qos_reliable_transient_local
        )
        self.create_subscription(
            AckermannControlCommand,
            TOPIC_CONTROL_CMD,
            self._awsim_control_cmd_cb,
            qos_reliable
        )
        self.create_subscription(
            AckermannControlCommand,
            TOPIC_ALT_CONTROL_CMD,
            self._control_command_control_cmd_cb,
            qos_reliable
        )
        self.create_subscription(
            Empty,
            TOPIC_RESET,
            self._awsim_reset_cb,
            qos_reliable
        )
        self.create_subscription(
            Bool,
            TOPIC_CONTROL_MODE,
            self._awsim_control_mode_cb,
            qos_reliable
        )
        self.create_subscription(
            NavSatFix,
            TOPIC_V2X_GNSS,
            self._v2x_gnss_cb,
            qos_reliable
        )

        # --- Publishers ---
        self.pub_control = self.create_publisher(
            AckermannControlCommand,
            TOPIC_CONTROL_CMD,
            qos_reliable
        )
        self.pub_alt_control = self.create_publisher(
            AckermannControlCommand,
            TOPIC_ALT_CONTROL_CMD,
            qos_reliable
        )
        self.pub_reset = self.create_publisher(
            Empty,
            TOPIC_RESET,
            qos_reliable
        )
        self.pub_control_mode = self.create_publisher(
            Bool,
            TOPIC_CONTROL_MODE,
            qos_reliable
        )
        self.pub_v2x_gnss = self.create_publisher(
            NavSatFix,
            TOPIC_V2X_GNSS,
            qos_reliable
        )

        self.get_logger().info("AWSIMNode initialized.")

    # ============================================================
    # Callbacks
    # ============================================================

    def _image_cb(self, msg: Image):
        """/sensing/camera/image_raw の最新フレームを RGB 画像として保持する。"""
        img = self._convert_image(msg)
        if img is not None:
            self.sensing_camera_image_raw = img

    def _camera_info_cb(self, msg: CameraInfo):
        """/sensing/camera/camera_info を保持する。AWSIMEnvでは未使用だが将来の前処理で使える。"""
        self.sensing_camera_camera_info = msg

    def _clock_cb(self, msg: Clock):
        """/clock を保持する。シミュレーション時刻が必要になったときに参照できる。"""
        self.clock = msg
        self.clock_time_sec = float(msg.clock.sec) + float(msg.clock.nanosec) * 1e-9

    def _gnss_cb(self, msg: NavSatFix):
        """/sensing/gnss/nav_sat_fix を保持する。現在のAWSIMEnvでは未使用。"""
        self.sensing_gnss_nav_sat_fix = msg

    def _gnss_fixed_cb(self, msg: BoolStamped):
        """/sensing/gnss/gnss_fixed を保持する。"""
        self.sensing_gnss_gnss_fixed = msg

    def _gnss_pose_cb(self, msg: PoseStamped):
        """/sensing/gnss/pose を保持する。"""
        self.sensing_gnss_pose = msg

    def _gnss_pose_with_cov_cb(self, msg: PoseWithCovarianceStamped):
        """/sensing/gnss/pose_with_covariance を保持する。"""
        self.sensing_gnss_pose_with_covariance = msg

    def _imu_cb(self, msg: Imu):
        """/sensing/imu/imu_raw を保持する。姿勢推定や速度補正に将来利用できる。"""
        self.sensing_imu_imu_raw = msg

    def _imu_data_cb(self, msg: Imu):
        """/sensing/imu/imu_data を保持する。"""
        self.sensing_imu_imu_data = msg

    def _lidar_cb(self, msg: LaserScan):
        """/sensing/lidar/scan を保持する。衝突検知や経路評価に将来利用できる。"""
        self.sensing_lidar_scan = msg

    def _sensing_vehicle_vel_twist_with_cov_cb(self, msg: TwistWithCovarianceStamped):
        """/sensing/vehicle_velocity_converter/twist_with_covariance を保持する。"""
        self.sensing_vehicle_velocity_converter_twist_with_covariance = msg

    def _gear_status_cb(self, msg: GearReport):
        """/vehicle/status/gear_status を保持する。ギア依存の制御や制約判定に使える。"""
        self.vehicle_status_gear_status = msg

    def _steering_status_cb(self, msg: SteeringReport):
        """/vehicle/status/steering_status を保持する。操舵追従の評価に使える。"""
        self.vehicle_status_steering_status = msg

    def _velocity_cb(self, msg: VelocityReport):
        """/vehicle/status/velocity_status を生メッセージのまま保持する。"""
        self.vehicle_status_velocity_status = msg

    def _awsim_status_cb(self, msg: Float32MultiArray):
        """
        /awsim/status (Float32MultiArray) を辞書化して保持する。
        このstatusは、/awsim/stateがwaitの状態のときだと、発行されない。

        index | field          | 説明
        ------+----------------+-----------------------------
          0   | sessionTime    | 残りセッション時間 [s]
          1   | lapCount       | 現在のラップ数
          2   | thisLapTime    | 現在のラップタイム [s]
          3   | section        | 現在のセクション番号
          4   | timeScale      | タイムスケール
          5   | boostRemaining | 残りブースト使用回数
          6   | isBoosting     | ブースト中フラグ
        """
        status: dict[str, float] = {}
        for idx, key in enumerate(AWSIM_STATUS_FIELDS):
            status[key] = float(msg.data[idx]) if len(msg.data) > idx else 0.0
        self.awsim_status = status

    def _awsim_state_cb(self, msg: String):
        """/awsim/state を保持する。reset可能判定に使う。"""
        self.awsim_state = msg

    def _odometry_cb(self, msg: Odometry):
        """/localization/kinematic_state を保持する。自己位置・速度の詳細参照に使える。"""
        self.localization_kinematic_state = msg

    def _localization_acceleration_cb(self, msg: AccelWithCovarianceStamped):
        """/localization/acceleration を保持する。"""
        self.localization_acceleration = msg

    def _localization_biased_pose_cb(self, msg: PoseStamped):
        """/localization/biased_pose を保持する。"""
        self.localization_biased_pose = msg

    def _localization_biased_pose_with_cov_cb(self, msg: PoseWithCovarianceStamped):
        """/localization/biased_pose_with_covariance を保持する。"""
        self.localization_biased_pose_with_covariance = msg

    def _localization_debug_cb(self, msg: Float64MultiArrayStamped):
        """/localization/debug を保持する。"""
        self.localization_debug = msg

    def _localization_estimated_yaw_bias_cb(self, msg: Float64Stamped):
        """/localization/estimated_yaw_bias を保持する。"""
        self.localization_estimated_yaw_bias = msg

    def _localization_gyro_twist_cb(self, msg: TwistStamped):
        """/localization/gyro_twist を保持する。"""
        self.localization_gyro_twist = msg

    def _localization_gyro_twist_raw_cb(self, msg: TwistStamped):
        """/localization/gyro_twist_raw を保持する。"""
        self.localization_gyro_twist_raw = msg

    def _localization_imu_gnss_pose_with_cov_cb(self, msg: PoseWithCovarianceStamped):
        """/localization/imu_gnss_poser/pose_with_covariance を保持する。"""
        self.localization_imu_gnss_poser_pose_with_covariance = msg

    def _localization_pose_cb(self, msg: PoseStamped):
        """/localization/pose を保持する。"""
        self.localization_pose = msg

    def _localization_pose_with_cov_cb(self, msg: PoseWithCovarianceStamped):
        """/localization/pose_with_covariance を保持する。"""
        self.localization_pose_with_covariance = msg

    def _localization_twist_cb(self, msg: TwistStamped):
        """/localization/twist を保持する。"""
        self.localization_twist = msg

    def _localization_twist_estimator_with_cov_cb(self, msg: TwistWithCovarianceStamped):
        """/localization/twist_estimator/twist_with_covariance を保持する。"""
        self.localization_twist_estimator_twist_with_covariance = msg

    def _localization_twist_estimator_with_cov_raw_cb(self, msg: TwistWithCovarianceStamped):
        """/localization/twist_estimator/twist_with_covariance_raw を保持する。"""
        self.localization_twist_estimator_twist_with_covariance_raw = msg

    def _localization_twist_with_cov_cb(self, msg: TwistWithCovarianceStamped):
        """/localization/twist_with_covariance を保持する。"""
        self.localization_twist_with_covariance = msg

    def _raceline_markers_cb(self, msg: MarkerArray):
        """/heading_pose_initializer/raceline_markers を保持する。"""
        self.heading_pose_initializer_raceline_markers = msg

    def _map_vector_map_cb(self, msg: HADMapBin):
        """/map/vector_map を保持する。"""
        self.map_vector_map = msg

    def _map_vector_map_marker_cb(self, msg: MarkerArray):
        """/map/vector_map_marker を保持する。"""
        self.map_vector_map_marker = msg

    def _trajectory_cb(self, msg: Trajectory):
        """/planning/scenario_planning/trajectory を保持する。計画軌跡に追従する観測設計へ拡張できる。"""
        self.planning_scenario_planning_trajectory = msg

    def _robot_description_cb(self, msg: String):
        """/robot_description を保持する。"""
        self.robot_description = msg

    def _tf_cb(self, msg: TFMessage):
        """/tf を保持する。"""
        self.tf = msg

    def _tf_static_cb(self, msg: TFMessage):
        """/tf_static を保持する。"""
        self.tf_static = msg

    def _awsim_control_cmd_cb(self, msg: AckermannControlCommand):
        """/awsim/control_cmd を保持する。"""
        self.awsim_control_cmd = msg

    def _control_command_control_cmd_cb(self, msg: AckermannControlCommand):
        """/control/command/control_cmd を保持する。"""
        self.control_command_control_cmd = msg

    def _awsim_reset_cb(self, msg: Empty):
        """/awsim/reset を保持する。"""
        self.awsim_reset = msg

    def _awsim_control_mode_cb(self, msg: Bool):
        """/awsim/control_mode_request_topic を保持する。"""
        self.awsim_control_mode_request_topic = msg

    def _v2x_gnss_cb(self, msg: NavSatFix):
        """/v2x/gnss/nav_sat_fix を保持する。"""
        self.v2x_gnss_nav_sat_fix = msg

    # ============================================================
    # Publish / Service
    # ============================================================

    def publish_control(self, steering: float, acceleration: float):
        """/awsim/control_cmd に AckermannControlCommand を publish する。"""
        cmd = AckermannControlCommand()
        now = self.get_clock().now().to_msg()
        cmd.stamp                              = now
        cmd.longitudinal.stamp                 = now
        cmd.lateral.stamp                      = now
        cmd.longitudinal.acceleration          = float(acceleration)
        cmd.lateral.steering_tire_angle        = float(steering)
        cmd.lateral.steering_tire_rotation_rate = 1.0
        self.awsim_control_cmd = cmd
        self.pub_control.publish(cmd)

    def publish_alt_control(self, steering: float, acceleration: float):
        """/control/command/control_cmd に同じ AckermannControlCommand を publish する。"""
        cmd = AckermannControlCommand()
        now = self.get_clock().now().to_msg()
        cmd.stamp = now
        cmd.longitudinal.stamp = now
        cmd.lateral.stamp = now
        cmd.longitudinal.acceleration = float(acceleration)
        cmd.lateral.steering_tire_angle = float(steering)
        cmd.lateral.steering_tire_rotation_rate = 1.0
        self.pub_alt_control.publish(cmd)

    def publish_v2x_gnss(self, msg: NavSatFix):
        """/v2x/gnss/nav_sat_fix に NavSatFix を publish する。将来のV2X連携用で、現状のAWSIMEnvでは未使用。"""
        self.v2x_gnss_nav_sat_fix = msg
        self.pub_v2x_gnss.publish(msg)

    def call_reset(self):
        """/awsim/reset を publish し、制御モードを Auto に戻す。

        /awsim/start は今回のトピック整理では対象外なので送信しない。
        もし別シーケンスが必要なら、このメソッドに追記する。
        """
        self.pub_reset.publish(Empty())
        self.get_logger().info("Published /awsim/reset")
        time.sleep(0.5)
        self.pub_control_mode.publish(Bool(data=True))
        self.get_logger().info("Published /awsim/control_mode_request_topic (Auto=True)")

    # ============================================================
    # Image conversion
    # ============================================================

    def _convert_image(self, msg: Image) -> np.ndarray:
        """ROS Image → NumPy (H, W, 3) RGB uint8"""
        try:
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
                self.get_logger().warn(
                    f"Unsupported encoding: {msg.encoding}",
                    throttle_duration_sec=5.0
                )
                return None
            return img
        except Exception as e:
            self.get_logger().error(f"Image conversion failed: {e}", throttle_duration_sec=5.0)
            return None
