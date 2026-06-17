#!/usr/bin/env bash

set -euo pipefail

out_dir="${RECOVERY_OUTPUT_DIR:-/output/$(date +%Y%m%d-%H%M%S)/recovery}"
bag_name="${RECOVERY_BAG_NAME:-rosbag2_recovery}"

log() {
    echo "[recovery-manual] $*"
}

wait_for_topic() {
    local topic="$1"
    local timeout_s="${2:-20}"
    local end=$((SECONDS + timeout_s))

    log "waiting for topic: ${topic}"
    while [ "${SECONDS}" -lt "${end}" ]; do
        if ros2 topic list 2>/dev/null | grep -Fxq "${topic}"; then
            log "topic is ready: ${topic}"
            return 0
        fi
        sleep 1
    done
    log "timeout waiting for topic: ${topic}"
    return 1
}

bag_pid=""

cleanup_rosbag() {
    if [ -z "${bag_pid}" ]; then
        return 0
    fi
    if kill -0 "${bag_pid}" 2>/dev/null; then
        log "stopping rosbag recorder (PID/PGID=${bag_pid})"
        kill -INT -- "-${bag_pid}" 2>/dev/null || kill -INT "${bag_pid}" 2>/dev/null || true
        wait "${bag_pid}" 2>/dev/null || true
    fi
    bag_pid=""
}

cleanup_on_exit() {
    local rc=$?
    cleanup_rosbag
    if [ "${rc}" -eq 0 ]; then
        log "bag directory: ${out_dir}/${bag_name}"
    fi
    exit "${rc}"
}

stop_and_exit() {
    trap - EXIT
    cleanup_rosbag
    log "bag directory: ${out_dir}/${bag_name}"
    exit 0
}

trap cleanup_on_exit EXIT
trap stop_and_exit SIGINT SIGTERM

had_nounset=0
case $- in *u*) had_nounset=1 ;; esac
set +u
# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash
if [ "${had_nounset}" -eq 1 ]; then
    set -u
fi

mkdir -p "${out_dir}"
cd "${out_dir}"
log "output directory: ${out_dir}"

wait_for_topic "/clock" 20
wait_for_topic "/recovery_supervisor/state" 20
wait_for_topic "/control/command/nominal_control_cmd" 20
wait_for_topic "/control/command/control_cmd" 20
wait_for_topic "/vehicle/status/velocity_status" 20

topics=(
    "/clock"
    "/admin/awsim/state"
    "/awsim/state"
    "/awsim/control_mode_request_topic"
    "/control/command/nominal_control_cmd"
    "/control/command/nominal_control_cmd_raw"
    "/control/command/control_cmd"
    "/control/command/control_cmd_raw"
    "/control/command/gear_cmd"
    "/recovery_supervisor/state"
    "/vehicle/status/control_mode"
    "/vehicle/status/velocity_status"
    "/vehicle/status/steering_status"
    "/vehicle/status/gear_status"
    "/localization/kinematic_state"
    "/localization/pose"
    "/localization/twist"
    "/planning/scenario_planning/trajectory"
    "/tf"
    "/tf_static"
)

log "starting rosbag: ${bag_name}"
if command -v setsid >/dev/null 2>&1; then
    setsid ros2 bag record "${topics[@]}" -o "${bag_name}" -s mcap >rosbag.log 2>&1 &
else
    ros2 bag record "${topics[@]}" -o "${bag_name}" -s mcap >rosbag.log 2>&1 &
fi
bag_pid=$!

log "recording manual recovery smoke. Press Ctrl-C when done."
while kill -0 "${bag_pid}" 2>/dev/null; do
    sleep 1
done

wait "${bag_pid}"
bag_pid=""
