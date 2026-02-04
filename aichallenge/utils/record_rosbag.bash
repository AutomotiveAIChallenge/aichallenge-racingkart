#!/bin/bash

set -euo pipefail

PID=""

cleanup_rosbag() {
    if [ -z "${PID}" ]; then
        return 0
    fi
    if kill -0 "${PID}" 2>/dev/null; then
        echo "Rosbag recording cleanup... (PID/PGID=${PID})"
        kill -INT -- "-${PID}" 2>/dev/null || kill -INT "${PID}" 2>/dev/null || true
        wait "${PID}" 2>/dev/null || true
    fi
}

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

# Run under its own process group so we can stop reliably without killing other recorders.
if command -v setsid >/dev/null 2>&1; then
    setsid ros2 bag record "${TOPICS[@]}" -o rosbag2_autoware -s mcap --compression-format zstd --compression-mode file &
else
    ros2 bag record "${TOPICS[@]}" -o rosbag2_autoware -s mcap --compression-format zstd --compression-mode file &
fi
PID=$!
wait "${PID}" || true
