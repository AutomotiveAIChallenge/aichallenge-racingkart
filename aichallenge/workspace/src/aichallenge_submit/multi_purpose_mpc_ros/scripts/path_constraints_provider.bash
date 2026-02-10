#!/bin/bash
source $(ros2 pkg prefix multi_purpose_mpc_ros)/.venv/bin/activate
python3 $(ros2 pkg prefix multi_purpose_mpc_ros)/lib/multi_purpose_mpc_ros/path_constraints_provider $@
