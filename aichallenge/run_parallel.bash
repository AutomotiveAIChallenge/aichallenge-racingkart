#!/usr/bin/env bash

ts="$(date +%Y%m%d-%H%M%S)"
out_dir="/output/${ts}"
mkdir -p "${out_dir}"
cd "${out_dir}" || exit
mkdir -p "${out_dir}/ros/log"

log_file="${out_dir}/autoware.log"
export ROS_HOME="${out_dir}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
# Keep launch output in-file while still streaming to container stdout.
exec > >(tee -a "${log_file}") 2>&1

# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash

exec ros2 launch aichallenge_system_launch parallel.launch.xml \
    "log_dir:=${out_dir}"