#!/bin/bash

mode="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"
target_uid="${HOST_UID:-1000}"
target_gid="${HOST_GID:-1000}"
child_pid=""

fix_ownership() {
    chown -R "${target_uid}:${target_gid}" "${out_dir}" 2>/dev/null || true
}

# This script is PID 1 in the driver container, so an EXIT trap alone never runs
# on `docker compose down`: PID 1 has untrapped signals discarded by the kernel and
# is SIGKILLed after stop_grace_period. Trap TERM/INT explicitly so the chown runs.
# The signal goes to the child's process group because /entrypoint.sh ->
# /workspace/utils/run.bash -> ros2 launch never exec, so signalling the direct
# child alone would orphan the ROS nodes. Same pattern as run_autoware.bash and
# utils/record_all_rosbag.bash.
finish() {
    if [ -n "${child_pid}" ] && kill -0 "${child_pid}" 2>/dev/null; then
        kill -INT -- "-${child_pid}" 2>/dev/null || kill -INT "${child_pid}" 2>/dev/null || true
        wait "${child_pid}" 2>/dev/null || true
    fi
    child_pid=""
    fix_ownership
}

trap finish EXIT
trap 'finish; exit 0' SIGINT SIGTERM

export ROS_DOMAIN_ID=$id

mkdir -p "${out_dir}"
fix_ownership
exec >"${out_dir}/driver.log" 2>&1

cd "${out_dir}" || exit
export ROS_HOME="${out_dir}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
mkdir -p "${ROS_LOG_DIR}"
fix_ownership

# set -m puts the child in its own process group (so the group-wide kill above works)
# and keeps bash from setting SIGINT to SIG_IGN on the backgrounded child (then the
# forwarded INT would be a no-op). wait, not exec, so the traps can still run.
set -m
/entrypoint.sh "${mode}" "${@:4}" &
child_pid=$!
set +m
wait "${child_pid}" || true
child_pid=""
fix_ownership
