#!/bin/bash
# Use only the ROS workspace baked into the driver image, before submission build.
set -eo pipefail
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source /workspace/install/setup.bash
exec python3 /vehicle/check_imu_bias.py "$@"
