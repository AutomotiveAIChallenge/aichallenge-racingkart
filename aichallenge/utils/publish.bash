#!/bin/bash

# Help function to display usage
usage() {
    echo "Usage: $0 [OPTION]"
    echo "Options:"
    echo "  check-awsim         Check if simulator is ready"
    echo "  wait-admin-status   Wait for /admin/awsim/state (transient local, usually ROS_DOMAIN_ID=0)"
    echo "  wait-d-status       Wait for /dN/awsim/status (N is given as an argument)"
    echo "  reset-awsim         Reset AWSIM (topic publish)"
    echo "  request-capture     Capture screen via service call"
    echo "  request-control     Request control mode change"
    echo "  request-initialpose Set initial pose"
    echo "  help                Display this help message"
}

# Function to capture screen
run_with_timeout() {
    local label="$1"
    local timeout_s="$2"
    shift 2

    echo "${label}..."
    timeout "${timeout_s}s" "$@" >/dev/null 2>&1
    local rc=$?

    if [ $rc -eq 124 ]; then
        echo "Warning: ${label} timed out after ${timeout_s} seconds"
        return 124
    fi
    if [ $rc -ne 0 ]; then
        echo "Error: ${label} failed (rc=$rc)"
        return $rc
    fi

    echo "${label} successfully"
    return 0
}

call_service() {
    local label="$1"
    local timeout_s="$2"
    local service="$3"
    local type="$4"
    local request="${5-}"

    if [ -z "$request" ]; then
        request="{}"
    fi

    run_with_timeout "${label}" "${timeout_s}" ros2 service call "${service}" "${type}" "${request}"
}

wait_for_topic_once() {
    local label="$1"
    local timeout_s="$2"
    local topic="$3"
    local type="$4"
    shift 4

    run_with_timeout "${label}" "${timeout_s}" ros2 topic echo "${topic}" "${type}" --once "$@"
}

extract_string_data() {
    # Extract the `data:` field from std_msgs/msg/String output.
    # Examples:
    #   data: "FinishALL"
    #   data: 'FinishALL'
    #   data: FinishALL
    local out="$1"
    printf '%s\n' "$out" | sed -n \
        -e 's/^data:[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' \
        -e "s/^data:[[:space:]]*'\\(.*\\)'[[:space:]]*$/\\1/p" \
        -e 's/^data:[[:space:]]*//p' | head -n 1
}

request_capture() {
    call_service "Capturing screen" "${AIC_SERVICE_CALL_TIMEOUT_S_CAPTURE:-10}" \
        "/debug/service/capture_screen" "std_srvs/srv/Trigger" "{}"
}

# Function to request control mode
request_control() {
    call_service "Requesting control mode change" "${AIC_SERVICE_CALL_TIMEOUT_S:-10}" \
        "/control/control_mode_request" "autoware_auto_vehicle_msgs/srv/ControlModeCommand" "{mode: 1}"
}

# Function to set initial pose
request_initial_pose_set() {
    call_service "Requesting initial pose set" "${AIC_SERVICE_CALL_TIMEOUT_S:-10}" \
        "/set_initial_pose" "std_srvs/srv/Trigger" "{}"
}

check_simulator_ready() {
    wait_for_topic_once "Waiting for /clock topic to be available" "${AIC_TOPIC_WAIT_TIMEOUT_S_CLOCK:-60}" \
        "/clock" "rosgraph_msgs/msg/Clock" --qos-reliability best_effort
}

wait_admin_status() {
    local timeout_s="${AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_STATUS:-60}"
    local expected=("$@")

    local deadline now left out rc status last
    deadline=$(($(date +%s) + timeout_s))
    last=""

    local topic="${AIC_AWSIM_ADMIN_STATE_TOPIC:-/admin/awsim/state}"

    echo "Waiting for ${topic}..."
    while :; do
        now=$(date +%s)
        left=$((deadline - now))
        if [ "${left}" -le 0 ]; then
            echo "Warning: Waiting for ${topic} timed out after ${timeout_s} seconds"
            if [ -n "${last}" ]; then
                echo "Last ${topic}: ${last}"
            fi
            return 124
        fi

        out=$(timeout "${left}s" ros2 topic echo "${topic}" "std_msgs/msg/String" --once \
            --qos-history keep_last --qos-depth 1 \
            --qos-durability transient_local --qos-reliability reliable 2>/dev/null)
        rc=$?
        if [ $rc -eq 124 ]; then
            echo "Warning: Waiting for ${topic} timed out after ${timeout_s} seconds"
            if [ -n "${last}" ]; then
                echo "Last ${topic}: ${last}"
            fi
            return 124
        fi
        if [ $rc -ne 0 ]; then
            echo "Error: Waiting for ${topic} failed (rc=$rc)"
            return $rc
        fi

        status=$(extract_string_data "${out}")
        if [ -z "${status}" ]; then
            continue
        fi

        if [ "${status}" != "${last}" ]; then
            echo "AWSIM admin status: ${status}"
            last="${status}"
        fi

        if [ "${#expected[@]}" -eq 0 ]; then
            return 0
        fi
        for e in "${expected[@]}"; do
            if [ "${status}" = "${e}" ] || [ "${status,,}" = "${e,,}" ]; then
                return 0
            fi
        done

        sleep 1
    done
}

wait_d_status() {
    local d="${1-}"
    if [ -z "${d}" ]; then
        echo "Error: wait-d-status requires N (domain/vehicle id). Example: wait-d-status 1" >&2
        return 2
    fi
    d="${d#d}"
    wait_for_topic_once "Waiting for /d${d}/awsim/status topic to be available" "${AIC_TOPIC_WAIT_TIMEOUT_S_D_STATUS:-60}" \
        "/d${d}/awsim/status" "std_msgs/msg/Float32MultiArray" --qos-reliability best_effort
}

reset_awsim() {
    run_with_timeout "Resetting AWSIM" 5 \
        ros2 topic pub --once "/admin/awsim/reset" "std_msgs/msg/Empty" "{}"
}

# Check if an argument was provided
if [ $# -eq 0 ]; then
    usage >&2
    exit 1
fi

rc=0

# Process based on provided argument
case "$1" in
check-awsim)
    check_simulator_ready
    rc=$?
    ;;
wait-admin-status)
    wait_admin_status "${@:2}"
    rc=$?
    ;;
wait-d-status)
    wait_d_status "${2-}"
    rc=$?
    ;;
reset-awsim)
    reset_awsim
    rc=$?
    ;;
request-capture)
    request_capture
    rc=$?
    ;;
request-control)
    request_control
    rc=$?
    ;;
request-initialpose)
    request_initial_pose_set
    rc=$?
    ;;
help)
    usage
    rc=0
    ;;
*)
    echo "Error: Invalid option '$1'" >&2
    usage >&2
    rc=2
    ;;
esac

exit "$rc"
