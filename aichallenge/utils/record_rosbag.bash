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
    "/awsim/control_cmd"
    "/clock"
    "/localization/acceleration"
    "/localization/kinematic_state"
)

ros2 bag record "${TOPICS[@]}" -o rosbag2_autoware -s mcap --compression-format zstd --compression-mode file
