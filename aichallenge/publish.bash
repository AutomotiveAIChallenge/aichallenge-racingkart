#!/bin/bash

# Help function to display usage
usage() {
    echo "Usage: $0 [OPTION]"
    echo "Options:"
    echo "  check-awsim         Check if simulator is ready"
    echo "  request-capture     Capture screen via service call"
    echo "  request-control     Request control mode change"
    echo "  request-initialpose Set initial pose"
    echo "  help                Display this help message"
    exit 1
}

# Function to capture screen
capture_screen() {
    echo "Capturing screen..."
    timeout 10s ros2 service call /debug/service/capture_screen std_srvs/srv/Trigger >/dev/null
    if [ $? -eq 124 ]; then
        echo "Warning: Screen capture service call timed out after 10 seconds"
    else
        echo "Screen capture requested successfully"
    fi
}

# Function to request control mode
request_control() {
    echo "Requesting control mode change..."
    timeout 10s ros2 service call /control/control_mode_request autoware_auto_vehicle_msgs/srv/ControlModeCommand '{mode: 1}' >/dev/null
    local rc=$?
    if [ $rc -eq 124 ]; then
        echo "Warning: Control mode request timed out after 10 seconds"
        return 124
    fi
    if [ $rc -ne 0 ]; then
        echo "Error: Control mode request failed (rc=$rc)"
        return $rc
    fi

    echo "Control mode change requested successfully"
    return 0
}

# Function to set initial pose
request_initial_pose_set() {
    echo "Requesting initial pose set..."
    timeout 60s ros2 service call /set_initial_pose std_srvs/srv/Trigger >/dev/null
    local rc=$?
    if [ $rc -eq 124 ]; then
        echo "Warning: Initial pose set timed out after 60 seconds"
        return 124
    fi
    if [ $rc -ne 0 ]; then
        echo "Error: Initial pose set request failed (rc=$rc)"
        return $rc
    fi

    echo "Initial pose set successfully"
    return 0
}

check_simulator_ready() {
    timeout_seconds=60
    elapsed=0
    while ! timeout 60s ros2 topic echo /clock 2>/dev/null | grep -q "sec:"; do
        sleep 2
        elapsed=$((elapsed + 2))
        echo "Waiting for /clock topic to be available... (${elapsed}s elapsed)"
        if [ $elapsed -ge $timeout_seconds ]; then
            echo "Warning: /clock topic not available after ${timeout_seconds}s timeout. Continuing anyway..."
            break
        fi
    done
    sleep 1
    echo "System is ready, executing publish commands..."
}

# Check if an argument was provided
if [ $# -eq 0 ]; then
    usage
fi

# Process based on provided argument
case "$1" in
check-awsim)
    check_simulator_ready
    ;;
request-capture)
    capture_screen
    ;;
request-control)
    request_control
    exit $?
    ;;
request-initialpose)
    request_initial_pose_set
    exit $?
    ;;
help)
    usage
    ;;
*)
    echo "Error: Invalid option '$1'"
    usage
    ;;
esac

exit 0
