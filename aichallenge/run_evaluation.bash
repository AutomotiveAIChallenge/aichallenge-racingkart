#!/usr/bin/env bash

domain_id="${ROS_DOMAIN_ID:-${DOMAIN_ID:-1}}"
output_root="${OUTPUT_ROOT:-/output}"
ts="$(date +%Y%m%d-%H%M%S)"
out_dir="${output_root}/${ts}/d${domain_id}"
mkdir -p "${out_dir}"
cd "${out_dir}" || exit

# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash

sim_mode="${SIM_MODE:-eval}"
capture="${AIC_CAPTURE:-true}"
rosbag="${AIC_ROSBAG:-true}"

exec ros2 launch aichallenge_system_launch evaluation.launch.xml \
    "domain_id:=${domain_id}" \
    "sim_mode:=${sim_mode}" \
    "output_run_dir:=${out_dir}" \
    "capture:=${capture}" \
    "rosbag:=${rosbag}" \
    "simulation:=true" \
    "use_sim_time:=true" \
    "run_rviz:=true"
