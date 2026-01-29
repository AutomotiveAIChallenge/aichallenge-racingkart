#!/bin/bash

# Help function to display usage
usage() {
    echo "Usage: $0 [OPTION]"
    echo "Options:"
    echo "  check-awsim         Check if simulator is ready"
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

    run_with_timeout "${label}" "${timeout_s}" ros2 topic echo "${topic}" "${type}" --once
}

request_capture() {
    call_service "Capturing screen" 10 \
        "/debug/service/capture_screen" "std_srvs/srv/Trigger" "{}"
}

# Function to request control mode
request_control() {
    call_service "Requesting control mode change" 10 \
        "/control/control_mode_request" "autoware_auto_vehicle_msgs/srv/ControlModeCommand" "{mode: 1}"
}

# Function to set initial pose
request_initial_pose_set() {
    call_service "Requesting initial pose set" 10 \
        "/set_initial_pose" "std_srvs/srv/Trigger" "{}"
}

check_simulator_ready() {
    wait_for_topic_once "Waiting for /clock topic to be available" 60 \
        "/clock" "rosgraph_msgs/msg/Clock"
}

reset_awsim() {
    run_with_timeout "Resetting AWSIM" 5 \
        ros2 topic pub --once "/aichallenge/awsim/reset" "std_msgs/msg/Empty" "{}"
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
