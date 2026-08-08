#!/bin/bash
# shellcheck disable=SC1091
source "$(ros2 pkg prefix multi_purpose_mpc_ros_with_dynamic_param)/.venv/bin/activate"
python3 "$(ros2 pkg prefix multi_purpose_mpc_ros_with_dynamic_param)/lib/multi_purpose_mpc_ros_with_dynamic_param/reference_velocity_configulator" "$@"
