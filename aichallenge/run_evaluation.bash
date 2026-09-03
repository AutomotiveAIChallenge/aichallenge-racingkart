#!/usr/bin/env bash
# Single-vehicle evaluation; also the evaluation-environment entrypoint for s2r_1_practice_solo,
# where no docker-entrypoint.sh has sourced ROS (docs/interface/evaluation-interface.md §6).

# The evaluation image builds the submission as the d1 overlay, whose setup.bash chains the base workspace.
if [ -f /aichallenge/d1/workspace/install/setup.bash ]; then
    # shellcheck disable=SC1091
    source /aichallenge/d1/workspace/install/setup.bash
else
    # shellcheck disable=SC1091
    source /aichallenge/workspace/install/setup.bash
fi

domain_id="${ROS_DOMAIN_ID:-1}"
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

sim_mode="${SIM_MODE:-eval}"

exec ros2 launch aichallenge_system_launch evaluation.launch.xml \
    "domain_id:=${domain_id}" \
    "sim_mode:=${sim_mode}" \
    "log_dir:=${out_dir}" \
    "capture:=true" \
    "rosbag:=true" \
    "simulation:=true" \
    "use_sim_time:=true" \
    "run_rviz:=${RUN_RVIZ:-true}" \
    "debug:=true"
