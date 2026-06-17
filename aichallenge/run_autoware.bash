#!/bin/bash

mode="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"
recovery_supervisor_param_file="${RECOVERY_SUPERVISOR_PARAM_FILE:-}"

case "${mode}" in
"awsim")
    opts=("simulation:=true" "use_sim_time:=true" "run_rviz:=true")
    ;;
"awsim-no-viz")
    opts=("simulation:=true" "use_sim_time:=true" "run_rviz:=false")
    ;;
"awsim-no-control")
    opts=("simulation:=true" "use_sim_time:=true" "run_rviz:=false" "control_method:=none")
    ;;
"awsim-mpc-recovery")
    opts=("simulation:=true" "use_sim_time:=true" "run_rviz:=false" "control_method:=mpc_recovery")
    ;;
"vehicle")
    opts=("simulation:=false" "use_sim_time:=false" "run_rviz:=false")
    ;;
"rosbag")
    opts=("simulation:=false" "use_sim_time:=true" "run_rviz:=true")
    ;;
*)
    echo "invalid argument (use 'awsim', 'awsim-no-viz', 'awsim-no-control', 'awsim-mpc-recovery', 'vehicle', or 'rosbag')"
    exit 1
    ;;
esac

if [ -n "${recovery_supervisor_param_file}" ]; then
    opts+=("recovery_supervisor_param_file:=${recovery_supervisor_param_file}")
fi

export ROS_DOMAIN_ID=$id

mkdir -p "${out_dir}"
exec >"${out_dir}/autoware.log" 2>&1
trap 'bash /aichallenge/utils/fix_ownership.bash "${HOST_UID}" "${HOST_GID}" /output "$(dirname "${out_dir}")"' EXIT

cd "${out_dir}" || exit
# Persist ROS node logs under the run output directory (so autostart_orchestrator logs are collectible).
export ROS_HOME="${out_dir}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
mkdir -p "${ROS_LOG_DIR}"

ros2 launch aichallenge_system_launch aichallenge_system.launch.xml "${opts[@]}" "domain_id:=$id"
