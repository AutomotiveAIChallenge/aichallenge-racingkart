#!/bin/bash

IS_ROSBAG_MODE=0
IS_CAPTURE_MODE=0
ROS_DOMAIN_ID_SIM=0
ROS_DOMAIN_ID_DEFAULT=1
ROS_DOMAIN_ID=$ROS_DOMAIN_ID_DEFAULT
INPUT_RESULT="d$ROS_DOMAIN_ID-result-details.json"

HOST_UID=""
HOST_GID=""
OUTPUT_ROOT="/output"
RESULT_WAIT_SECONDS=10

RE_NUMBER='^[0-9]+$' # 数字のみにマッチする正規表現
OTHER_ARGS=()        # 既知オプション以外の引数を保持（互換用）

PID_AWSIM=""
PID_AUTOWARE=""
PID_ROSBAG=""
OUTPUT_DIRECTORY=""
CAPTURE_STARTED=0
CAPTURE_STOPPED=0
OWNERSHIP_DONE=0
REQUEST_HELP=0

log() {
    echo "[run_evaluation] $*"
}

warn() {
    echo "[run_evaluation][WARN] $*" >&2
}

run_or_exit() {
    local description="$1"
    shift

    "$@"
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        warn "${description} failed with code ${rc}"
        exit "$rc"
    fi
}

usage() {
    cat <<'EOF'
Usage:
  run_evaluation.bash [rosbag|--rosbag] [capture|--capture] [HOST_UID HOST_GID]
  run_evaluation.bash [--uid N] [--gid N] [--domain-id N] [--output-root PATH]

Notes:
  - Backward compatible with the legacy positional form: "... <uid> <gid>".
  - Unknown args are ignored (kept for forward compatibility).
EOF
}

best_effort() {
    "$@" >/dev/null 2>&1 || warn "Command failed (continuing): $*"
}

is_number() {
    [[ ${1-} =~ $RE_NUMBER ]]
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "${1}" in
        rosbag | --rosbag)
            IS_ROSBAG_MODE=1
            shift
            ;;
        capture | --capture)
            IS_CAPTURE_MODE=1
            shift
            ;;
        --uid)
            HOST_UID="${2-}"
            shift 2
            ;;
        --gid)
            HOST_GID="${2-}"
            shift 2
            ;;
        --domain-id)
            ROS_DOMAIN_ID="${2-}"
            shift 2
            ;;
        --output-root)
            OUTPUT_ROOT="${2-}"
            shift 2
            ;;
        --result-wait-seconds)
            RESULT_WAIT_SECONDS="${2-}"
            shift 2
            ;;
        -h | --help)
            REQUEST_HELP=1
            shift
            ;;
        --)
            shift
            OTHER_ARGS+=("$@")
            break
            ;;
        *)
            if is_number "$1"; then
                if [ -z "$HOST_UID" ]; then
                    HOST_UID="$1"
                    shift
                    continue
                fi
                if [ -z "$HOST_GID" ]; then
                    HOST_GID="$1"
                    shift
                    continue
                fi
                shift
                continue
            fi
            OTHER_ARGS+=("$1")
            shift
            ;;
        esac
    done

    if [ -n "$HOST_UID" ] && ! is_number "$HOST_UID"; then
        warn "Ignoring invalid --uid: '$HOST_UID'"
        HOST_UID=""
    fi
    if [ -n "$HOST_GID" ] && ! is_number "$HOST_GID"; then
        warn "Ignoring invalid --gid: '$HOST_GID'"
        HOST_GID=""
    fi
    if [ -n "$ROS_DOMAIN_ID" ] && ! is_number "$ROS_DOMAIN_ID"; then
        warn "Invalid --domain-id: '$ROS_DOMAIN_ID' (fallback to ${ROS_DOMAIN_ID_DEFAULT})"
        ROS_DOMAIN_ID=$ROS_DOMAIN_ID_DEFAULT
    fi
    if [ -n "$RESULT_WAIT_SECONDS" ] && ! is_number "$RESULT_WAIT_SECONDS"; then
        warn "Invalid --result-wait-seconds: '$RESULT_WAIT_SECONDS' (fallback to 60)"
        RESULT_WAIT_SECONDS=60
    fi

    if [ "$IS_ROSBAG_MODE" -eq 1 ]; then
        log "ROS Bag recording mode enabled."
    fi
    if [ "$IS_CAPTURE_MODE" -eq 1 ]; then
        log "Screen capture mode enabled."
    fi
    if [ -n "$HOST_UID" ]; then
        log "HOST_UID set to: $HOST_UID"
    fi
    if [ -n "$HOST_GID" ]; then
        log "HOST_GID set to: $HOST_GID"
    fi
    if [ "${#OTHER_ARGS[@]}" -gt 0 ]; then
        warn "Ignoring unknown args: ${OTHER_ARGS[*]}"
    fi
}

move_window() {
    log "Move window"

    if ! wmctrl -l >/dev/null 2>&1; then
        log "wmctrl command not available. Skipping window management."
        sleep 5
        return 0
    fi

    local has_gpu has_awsim has_rviz
    has_gpu=$(command -v nvidia-smi >/dev/null && echo 1 || echo 0)

    # Add timeout to prevent infinite hanging
    local timeout=60 # 60 seconds timeout
    local elapsed=0

    while [ $elapsed -lt $timeout ]; do
        has_awsim=$(wmctrl -l | grep -q "AWSIM" && echo 1 || echo 0)
        has_rviz=$(wmctrl -l | grep -q "RViz" && echo 1 || echo 0)

        if [ "$has_rviz" -eq 1 ] && { [ "$has_awsim" -eq 1 ] || [ "$has_gpu" -eq 0 ]; }; then
            break
        fi
        sleep 1
        ((elapsed++))
        log "Move window: $elapsed seconds elapsed"
    done

    if [ $elapsed -ge $timeout ]; then
        warn "Timeout waiting for AWSIM/RViz windows after ${timeout} seconds"
        warn "AWSIM window found: $has_awsim"
        warn "RViz window found: $has_rviz"
        warn "GPU available: $has_gpu"
        warn "Continuing without window positioning..."
        return 1
    fi

    log "AWSIM and RViz windows found"
    # Move windows
    wmctrl -a "RViz" && wmctrl -r "RViz" -e 0,0,0,1920,1043
    sleep 1
    wmctrl -a "AWSIM" && wmctrl -r "AWSIM" -e 0,0,0,900,1043
    sleep 2
}

setup_output_dir() {
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    mkdir -p "$OUTPUT_ROOT" || exit 1
    cd "$OUTPUT_ROOT" || exit 1
    mkdir "$ts" || exit 1
    ln -nfs "$ts" latest
    cd "$ts" || exit 1
    OUTPUT_DIRECTORY="$(pwd)"
    log "Output directory: $OUTPUT_DIRECTORY"
}

setup_ros_env() {
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    # shellcheck disable=SC1091
    source /autoware/install/setup.bash
    # shellcheck disable=SC1091
    source /aichallenge/workspace/install/setup.bash
    export ROS_DOMAIN_ID=$ROS_DOMAIN_ID
}

tune_network_best_effort() {
    best_effort sudo -n ip link set multicast on lo
    best_effort sudo -n sysctl -w net.core.rmem_max=2147483647
}

start_simulator() {
    log "Start AWSIM"
    nohup /aichallenge/run_simulator.bash eval >/dev/null 2>&1 &
    PID_AWSIM=$!
    log "AWSIM PID: $PID_AWSIM"
}

check_simulator_ready() {
    log "Check simulator readiness"
    export ROS_DOMAIN_ID=$ROS_DOMAIN_ID_SIM
    bash /aichallenge/publish.bash check-awsim
    log "AWSIM is ready."
    export ROS_DOMAIN_ID=$ROS_DOMAIN_ID_DEFAULT
}

start_autoware() {
    log "Start Autoware"
    nohup /aichallenge/run_autoware.bash awsim "$ROS_DOMAIN_ID" >autoware.log 2>&1 &
    PID_AUTOWARE=$!
    log "Autoware PID: $PID_AUTOWARE"
}

start_screen_capture_if_needed() {
    if [ "$IS_CAPTURE_MODE" -eq 1 ]; then
        bash /aichallenge/publish.bash request-capture
        CAPTURE_STARTED=1
        log "Screen capture started."
    else
        log "Screen capture skipped."
    fi
}

stop_screen_capture_if_needed() {
    if [ "$CAPTURE_STARTED" -eq 1 ] && [ "$CAPTURE_STOPPED" -eq 0 ]; then
        log "Stop screen capture"
        bash /aichallenge/publish.bash request-capture || true
        CAPTURE_STOPPED=1
    fi
}

start_rosbag_if_needed() {
    if [ "$IS_ROSBAG_MODE" -eq 1 ]; then
        log "Start rosbag"
        nohup /aichallenge/record_rosbag.bash >/dev/null 2>&1 &
        PID_ROSBAG=$!
        log "ROS Bag PID: $PID_ROSBAG"
        sleep 2
        if ! kill -0 "$PID_ROSBAG" 2>/dev/null; then
            warn "Rosbag process is not running"
        else
            log "Rosbag recording started successfully"
        fi
    else
        PID_ROSBAG=""
        log "ROS Bag recording skipped."
    fi
}

stop_rosbag_if_needed() {
    if [ -n "$PID_ROSBAG" ] && kill -0 "$PID_ROSBAG" 2>/dev/null; then
        log "Stop rosbag (SIGINT)"
        kill -INT "$PID_ROSBAG" 2>/dev/null || true
        wait "$PID_ROSBAG" 2>/dev/null || true
        PID_ROSBAG=""
    fi
}

convert_result_best_effort() {
    log "Convert result (wait up to ${RESULT_WAIT_SECONDS}s for $INPUT_RESULT)"
    for ((i = 0; i < RESULT_WAIT_SECONDS; i++)); do
        [ -s $INPUT_RESULT ] && break
        sleep 1
    done
    python3 /aichallenge/workspace/src/aichallenge_system/script/result-converter.py --input $INPUT_RESULT || true
}

fix_ownership_if_needed() {
    if [ "$OWNERSHIP_DONE" -eq 1 ]; then
        return 0
    fi
    if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
        if [ "$(id -u)" -eq 0 ]; then
            log "Running as root. Changing ownership of artifacts to ${HOST_UID}:${HOST_GID}..."
            log "Target directory: $(pwd)"
            chown -R "${HOST_UID}:${HOST_GID}" "$(pwd)" || true
            chown -h "${HOST_UID}:${HOST_GID}" "${OUTPUT_ROOT}/latest" || true
            log "Ownership change complete."
        else
            log "Running as non-root user ($(id -u)). Skipping chown."
        fi
    else
        log "HOST_UID/HOST_GID not provided as arguments. Skipping ownership change."
    fi
    OWNERSHIP_DONE=1
}

cleanup() {
    stop_screen_capture_if_needed
    stop_rosbag_if_needed
    fix_ownership_if_needed
}

on_sigint() {
    warn "Interrupted (SIGINT). Cleaning up..."
    trap - EXIT SIGINT SIGTERM
    cleanup
    exit 130
}

on_sigterm() {
    warn "Terminated (SIGTERM). Cleaning up..."
    trap - EXIT SIGINT SIGTERM
    cleanup
    exit 143
}

main() {
    parse_args "$@"
    if [ "$REQUEST_HELP" -eq 1 ]; then
        usage
        return 0
    fi

    trap cleanup EXIT
    trap on_sigint SIGINT
    trap on_sigterm SIGTERM

    setup_output_dir
    setup_ros_env
    tune_network_best_effort

    start_simulator
    check_simulator_ready

    start_autoware
    sleep 3
    move_window
    run_or_exit "Initial pose set" /aichallenge/publish.bash request-initialpose
    run_or_exit "Control request" /aichallenge/publish.bash request-control
    start_screen_capture_if_needed
    start_rosbag_if_needed

    wait "$PID_AWSIM" || true
    convert_result_best_effort
    log "Evaluation Script finished. Cleaning up..."
}

main "$@"
