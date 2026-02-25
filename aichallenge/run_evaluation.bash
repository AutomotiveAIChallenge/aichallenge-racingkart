#!/usr/bin/env bash

domain_id="${ROS_DOMAIN_ID:-${DOMAIN_ID:-1}}"
ts="$(date +%Y%m%d-%H%M%S)"
out_dir="/output/${ts}/d${domain_id}"
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
sudo ip link set multicast on lo || true
sudo sysctl -w net.core.rmem_max=2147483647 >/dev/null || true

sim_mode="${SIM_MODE:-eval}"
capture="${AIC_CAPTURE:-true}"
rosbag="${AIC_ROSBAG:-true}"

exec ros2 launch aichallenge_system_launch evaluation.launch.xml \
    "domain_id:=${domain_id}" \
    "sim_mode:=${sim_mode}" \
    "log_dir:=${out_dir}" \
    "capture:=${capture}" \
    "rosbag:=${rosbag}" \
    "simulation:=true" \
    "use_sim_time:=true" \
    "run_rviz:=true"
