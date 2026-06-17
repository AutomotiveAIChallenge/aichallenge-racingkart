#!/usr/bin/env bash

set -euo pipefail

profile="${1:-stuck_repro}"
expect="${2:-stuck_under_throttle}"
out_dir="${RECOVERY_OUTPUT_DIR:-/output/$(date +%Y%m%d-%H%M%S)/recovery}"
bag_name="${RECOVERY_BAG_NAME:-rosbag2_recovery}"
record_warmup_sec="${RECOVERY_RECORD_WARMUP_SEC:-2}"
profile_initial_delay_sec="${RECOVERY_PROFILE_INITIAL_DELAY_SEC:-15.0}"
profile_max_duration_sec="${RECOVERY_PROFILE_MAX_DURATION_SEC:-35.0}"
supervisor_enabled="${RECOVERY_SUPERVISOR:-false}"

log() {
    echo "[recovery] $*"
}

wait_for_topic() {
    local topic="$1"
    local timeout_s="${2:-45}"
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
supervisor_pid=""
supervisor_uses_setsid=0
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

cleanup_supervisor() {
    if [ -z "${supervisor_pid}" ]; then
        return 0
    fi
    if kill -0 "${supervisor_pid}" 2>/dev/null; then
        log "stopping recovery supervisor (PID=${supervisor_pid})"
        if [ "${supervisor_uses_setsid}" -eq 1 ]; then
            kill -INT -- "-${supervisor_pid}" 2>/dev/null || kill -INT "${supervisor_pid}" 2>/dev/null || true
            sleep 2
            kill -TERM -- "-${supervisor_pid}" 2>/dev/null || true
        else
            kill -INT "${supervisor_pid}" 2>/dev/null || true
            sleep 2
            kill -TERM "${supervisor_pid}" 2>/dev/null || true
        fi
        wait "${supervisor_pid}" 2>/dev/null || true
    fi
    supervisor_pid=""
    supervisor_uses_setsid=0
}

cleanup_all() {
    cleanup_supervisor
    cleanup_rosbag
}

trap cleanup_all EXIT SIGINT SIGTERM

had_nounset=0
case $- in *u*) had_nounset=1 ;; esac
set +u
# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash
if [ "${had_nounset}" -eq 1 ]; then
    set -u
fi

supervisor_param_file="${RECOVERY_SUPERVISOR_PARAM_FILE:-$(ros2 pkg prefix recovery_supervisor)/share/recovery_supervisor/config/recovery_supervisor.param.yaml}"
supervisor_param_overrides=()
supervisor_ros_args=()

add_supervisor_param_override() {
    local name="$1"
    local value="$2"
    if [ -n "${value}" ]; then
        supervisor_ros_args+=("-p" "${name}:=${value}")
        supervisor_param_overrides+=("${name}=${value}")
    fi
}

write_supervisor_param_record() {
    local output_path="$1"
    {
        echo "RECOVERY_SUPERVISOR_PARAM_FILE=${supervisor_param_file}"
        for override in "${supervisor_param_overrides[@]}"; do
            echo "override.${override}"
        done
    } >"${output_path}"
}

add_supervisor_param_override "stuck_speed_threshold" "${RECOVERY_SUPERVISOR_STUCK_SPEED_THRESHOLD:-}"
add_supervisor_param_override "stuck_duration" "${RECOVERY_SUPERVISOR_STUCK_DURATION:-}"
add_supervisor_param_override "command_speed_threshold" "${RECOVERY_SUPERVISOR_COMMAND_SPEED_THRESHOLD:-}"
add_supervisor_param_override "command_accel_threshold" "${RECOVERY_SUPERVISOR_COMMAND_ACCEL_THRESHOLD:-}"
add_supervisor_param_override "moving_speed_threshold" "${RECOVERY_SUPERVISOR_MOVING_SPEED_THRESHOLD:-}"
add_supervisor_param_override "reverse_speed" "${RECOVERY_SUPERVISOR_REVERSE_SPEED:-}"
add_supervisor_param_override "reverse_accel" "${RECOVERY_SUPERVISOR_REVERSE_ACCEL:-}"
add_supervisor_param_override "reverse_duration" "${RECOVERY_SUPERVISOR_REVERSE_DURATION:-}"
add_supervisor_param_override "drive_settle_duration" "${RECOVERY_SUPERVISOR_DRIVE_SETTLE_DURATION:-}"
add_supervisor_param_override "cooldown_duration" "${RECOVERY_SUPERVISOR_COOLDOWN_DURATION:-}"
add_supervisor_param_override "nominal_timeout_sec" "${RECOVERY_SUPERVISOR_NOMINAL_TIMEOUT_SEC:-}"
add_supervisor_param_override "velocity_timeout_sec" "${RECOVERY_SUPERVISOR_VELOCITY_TIMEOUT_SEC:-}"
add_supervisor_param_override "timer_hz" "${RECOVERY_SUPERVISOR_TIMER_HZ:-}"

mkdir -p "${out_dir}"
cd "${out_dir}"
log "output directory: ${out_dir}"
log "profile=${profile} expect=${expect}"
log "profile_initial_delay_sec=${profile_initial_delay_sec}"
log "profile_max_duration_sec=${profile_max_duration_sec}"
log "supervisor_enabled=${supervisor_enabled}"
log "supervisor_param_file=${supervisor_param_file}"
if [ "${#supervisor_param_overrides[@]}" -gt 0 ]; then
    log "supervisor_param_overrides=${supervisor_param_overrides[*]}"
fi

wait_for_topic "/clock" 45
wait_for_topic "/awsim/control_mode_request_topic" 45
wait_for_topic "/vehicle/status/velocity_status" 45

profile_output_control_topic="${RECOVERY_PROFILE_OUTPUT_CONTROL_TOPIC:-/control/command/control_cmd}"
profile_publish_gear_cmd="${RECOVERY_PROFILE_PUBLISH_GEAR_CMD:-true}"
profile_stop_on_stuck="${RECOVERY_PROFILE_STOP_ON_STUCK:-true}"
profile_success_on_timeout="${RECOVERY_PROFILE_SUCCESS_ON_TIMEOUT:-false}"

if [ "${supervisor_enabled}" = "true" ] || [ "${supervisor_enabled}" = "1" ]; then
    if [ ! -f "${supervisor_param_file}" ]; then
        log "recovery supervisor param file does not exist: ${supervisor_param_file}"
        exit 2
    fi
    cp "${supervisor_param_file}" recovery_supervisor.param.yaml
    write_supervisor_param_record recovery_supervisor_params.env

    profile_output_control_topic="${RECOVERY_PROFILE_OUTPUT_CONTROL_TOPIC:-/control/command/nominal_control_cmd}"
    profile_publish_gear_cmd="${RECOVERY_PROFILE_PUBLISH_GEAR_CMD:-false}"
    profile_stop_on_stuck="${RECOVERY_PROFILE_STOP_ON_STUCK:-false}"
    profile_success_on_timeout="${RECOVERY_PROFILE_SUCCESS_ON_TIMEOUT:-true}"

    log "starting recovery supervisor"
    if command -v setsid >/dev/null 2>&1; then
        setsid ros2 run recovery_supervisor recovery_supervisor_node.py \
            --ros-args \
            --params-file "${supervisor_param_file}" \
            -p "use_sim_time:=true" \
            "${supervisor_ros_args[@]}" >supervisor.log 2>&1 &
        supervisor_uses_setsid=1
    else
        ros2 run recovery_supervisor recovery_supervisor_node.py \
            --ros-args \
            --params-file "${supervisor_param_file}" \
            -p "use_sim_time:=true" \
            "${supervisor_ros_args[@]}" >supervisor.log 2>&1 &
        supervisor_uses_setsid=0
    fi
    supervisor_pid=$!
    wait_for_topic "/recovery_supervisor/state" 20
fi

log "profile_output_control_topic=${profile_output_control_topic}"
log "profile_publish_gear_cmd=${profile_publish_gear_cmd}"
log "profile_stop_on_stuck=${profile_stop_on_stuck}"
log "profile_success_on_timeout=${profile_success_on_timeout}"

topics=(
    "/clock"
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
    "/awsim/status"
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

sleep "${record_warmup_sec}"

log "requesting AWSIM control mode"
ros2 topic pub -1 /awsim/control_mode_request_topic std_msgs/msg/Bool "{data: true}" >/dev/null

set +e
ros2 run recovery_test_tools command_profile_node.py \
    --ros-args \
    -p "use_sim_time:=true" \
    -p "profile:=${profile}" \
    -p "initial_delay_sec:=${profile_initial_delay_sec}" \
    -p "max_duration_sec:=${profile_max_duration_sec}" \
    -p "output_control_topic:=${profile_output_control_topic}" \
    -p "publish_gear_cmd:=${profile_publish_gear_cmd}" \
    -p "stop_on_stuck:=${profile_stop_on_stuck}" \
    -p "success_on_timeout:=${profile_success_on_timeout}" >profile.log 2>&1
profile_rc=$?
set -e

cleanup_supervisor
cleanup_rosbag
trap - EXIT SIGINT SIGTERM

log "command profile exit code: ${profile_rc}"
log "analyzing rosbag"

set +e
ros2 run recovery_test_tools analyze_recovery_bag.py "${bag_name}" \
    --expect "${expect}" \
    --output recovery-result.json >analyze.log 2>&1
analyze_rc=$?
set -e

cat analyze.log
if [ -f recovery-result.json ]; then
    log "result: ${out_dir}/recovery-result.json"
fi

if [ "${profile_rc}" -ne 0 ]; then
    log "profile failed; see ${out_dir}/profile.log"
    exit "${profile_rc}"
fi
exit "${analyze_rc}"
