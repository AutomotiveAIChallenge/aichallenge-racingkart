#!/usr/bin/env bash
set -euo pipefail

rosbag=false
capture=false
uid=""
gid=""
for arg in "$@"; do
    case "${arg}" in
    -r | rosbag) rosbag=true ;;
    -c | capture) capture=true ;;
    *)
        if [[ ${arg} =~ ^[0-9]+$ ]]; then
            if [ -z "${uid}" ]; then uid="${arg}"; elif [ -z "${gid}" ]; then gid="${arg}"; fi
        fi
        ;;
    esac
done

domain_id="${ROS_DOMAIN_ID:-${DOMAIN_ID:-1}}"
output_root="${OUTPUT_ROOT:-/output}"
ts="$(date +%Y%m%d-%H%M%S)"
out_dir="${output_root}/${ts}/d${domain_id}"
mkdir -p "${out_dir}"
ln -nfs "${ts}" "${output_root}/latest" || true
cd "${out_dir}"

# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash

pid_sim=""
pid_aw=""
cleanup() {
    local exit_code=$?
    set +e
    trap - EXIT INT TERM
    if [ -n "${pid_aw-}" ]; then
        kill -INT "${pid_aw}" >/dev/null 2>&1 || true
        wait "${pid_aw}" >/dev/null 2>&1 || true
    fi
    if [ -n "${pid_sim-}" ]; then
        kill -INT "${pid_sim}" >/dev/null 2>&1 || true
        wait "${pid_sim}" >/dev/null 2>&1 || true
    fi
    bash /aichallenge/utils/fix_ownership.bash "${uid}" "${gid}" "${output_root}" "${ts}" >/dev/null 2>&1 || true
    return "${exit_code}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# AWSIM
/aichallenge/run_simulator.bash eval >awsim.log 2>&1 &
pid_sim=$!
env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-ready
# Autoware
env AIC_CAPTURE="${capture}" AIC_ROSBAG="${rosbag}" OUTPUT_RUN_DIR="${out_dir}" /aichallenge/run_autoware.bash awsim "${domain_id}" >autoware.log 2>&1 &
pid_aw=$!
# Wait AWSIM finish
env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-finished
