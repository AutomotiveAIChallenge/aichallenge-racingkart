#!/bin/bash

mode="${1}"
id="${2:-0}" # デフォルト値0を設定
capture="${AIC_CAPTURE:-false}"
rosbag="${AIC_ROSBAG:-false}"

case "${mode}" in
"awsim")
    opts=("simulation:=true" "use_sim_time:=true" "run_rviz:=true")
    ;;
"awsim-no-viz")
    opts=("simulation:=true" "use_sim_time:=true" "run_rviz:=false")
    ;;
"vehicle")
    opts=("simulation:=false" "use_sim_time:=false" "run_rviz:=false")
    ;;
"rosbag")
    opts=("simulation:=false" "use_sim_time:=true" "run_rviz:=true")
    ;;
*)
    echo "invalid argument (use 'awsim' or 'vehicle' or 'rosbag')"
    exit 1
    ;;
esac

opts+=("capture:=${capture}")
opts+=("rosbag:=${rosbag}")

export ROS_DOMAIN_ID=$id
nounset_was_set=0
case "$-" in *u*)
    nounset_was_set=1
    set +u
    ;;
esac
# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash
if [ "${nounset_was_set}" = "1" ]; then
    set -u
fi

OUTPUT_RUN_DIR="${OUTPUT_RUN_DIR:-/output}"
# Persist ROS node logs under the run output directory (so autostart_orchestrator logs are collectible).
export ROS_HOME="${OUTPUT_RUN_DIR}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
mkdir -p "${ROS_LOG_DIR}"

sudo ip link set multicast on lo
sudo sysctl -w net.core.rmem_max=2147483647 >/dev/null

ros2 launch aichallenge_system_launch aichallenge_system.launch.xml "${opts[@]}" "domain_id:=$id"
