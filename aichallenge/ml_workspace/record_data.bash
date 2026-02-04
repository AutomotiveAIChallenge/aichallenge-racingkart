#!/bin/bash

# Function to handle cleanup on exit
cleanup_rosbag() {
    echo "Rosbag recording cleanup..."
    # Stop any running ros2 bag record processes
    pkill -f "ros2 bag record" 2>/dev/null || true
    sleep 1
}

# Trap signals to ensure cleanup
trap cleanup_rosbag EXIT SIGINT SIGTERM

# shellcheck disable=SC1091
source "/aichallenge/workspace/install/setup.bash"

# Topics with data (excluding 0-message topics from original bag)
TOPICS=(
    "/admin/awsim/state"
    "/awsim/control_cmd"
    "/control/command/actuation_cmd"
    "/control/command/control_cmd"
    "/sensing/lidar/scan"
)

mkdir -p /aichallenge/ml_workspace/rawdata
cd /aichallenge/ml_workspace/rawdata || exit
ros2 bag record "${TOPICS[@]}" -o "$(date +%Y%m%d-%H%M%S)" -s mcap --compression-format zstd --compression-mode file
