#!/bin/bash

# shellcheck disable=SC1091
source "/aichallenge/workspace/install/setup.bash"

# Topics with data (excluding 0-message topics from original bag)
TOPICS=(
    "/admin/awsim/state"
    "/awsim/control_cmd"
    "/clock"
    "/control/command/actuation_cmd"
    "/control/command/control_cmd"
    "/control/debug/lookahead_point"
    "/diagnostics"
    "/localization/acceleration"
    "/localization/biased_pose"
    "/localization/biased_pose_with_covariance"
    "/localization/debug"
    "/localization/estimated_yaw_bias"
    "/localization/gyro_twist"
    "/localization/gyro_twist_raw"
    "/localization/imu_gnss_poser/pose_with_covariance"
    "/localization/kinematic_state"
    "/localization/pose"
    "/localization/pose_with_covariance"
    "/localization/twist"
    "/localization/twist_estimator/twist_with_covariance"
    "/localization/twist_estimator/twist_with_covariance_raw"
    "/localization/twist_with_covariance"
    "/map/vector_map"
    "/map/vector_map_marker"
    "/output/raw_control_cmd"
    "/parameter_events"
    "/planning/mission_planning/goal"
    "/planning/scenario_planning/trajectory"
    "/robot_description"
    "/rosout"
    "/sensing/gnss/gnss_fixed"
    "/sensing/gnss/nav_sat_fix"
    "/sensing/gnss/navpvt"
    "/sensing/gnss/pose"
    "/sensing/gnss/pose_with_covariance"
    "/sensing/camera/image_raw"
    "/sensing/imu/imu_data"
    "/sensing/imu/imu_raw"
    "/sensing/vehicle_velocity_converter/twist_with_covariance"
    "/system/system_monitor/cpu_monitor/cpu_usage"
    "/tf"
    "/tf_static"
    "/vehicle/status/control_mode"
    "/vehicle/status/gear_status"
    "/vehicle/status/hazard_lights_status"
    "/vehicle/status/steering_status"
    "/vehicle/status/turn_indicators_status"
    "/vehicle/status/velocity_status"
)

ros2 bag record \
    -s mcap \
    "${TOPICS[@]}" \
    -d 60 \
    -o "log/rosbag2_$(date +%Y_%m_%d_%H_%M_%S)" \
    --compression-mode file \
    --compression-format zstd \
    &

# バックグラウンドで実行したプロセスのID (PID) を取得する
ROSBAG_PID=$!
echo "ros2 bag record process started with PID: $ROSBAG_PID"

# シグナルを受け取った際のクリーンアップ関数を定義
cleanup() {
    echo "Signal received, sending SIGINT to ros2 bag record (PID: $ROSBAG_PID)..."
    kill -SIGINT "$ROSBAG_PID"
    echo "Waiting for ros2 bag record to finalize..."
    wait "$ROSBAG_PID"
    echo "Cleanup finished. Exiting."
}

trap cleanup SIGINT SIGTERM

wait "$ROSBAG_PID"
