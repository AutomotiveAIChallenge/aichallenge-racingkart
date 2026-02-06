#!/usr/bin/env bash

domain_id="${ROS_DOMAIN_ID:-${DOMAIN_ID:-1}}"
output_root="${OUTPUT_ROOT:-/output}"
ts="$(date +%Y%m%d-%H%M%S)"
out_dir="${output_root}/${ts}/d${domain_id}"
mkdir -p "${out_dir}"
ln -nfs "${ts}" "${output_root}/latest" || true
cd "${out_dir}" || exit

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
    return "${exit_code}"
}
trap cleanup EXIT
trap 'cleanup;exit 130' INT
trap 'cleanup;exit 143' TERM

# AWSIM
/aichallenge/run_simulator.bash eval >awsim.log 2>&1 &
pid_sim=$!
# Autoware
env OUTPUT_RUN_DIR="${out_dir}" /aichallenge/run_autoware.bash awsim "${domain_id}" >autoware.log 2>&1
pid_aw=$!
