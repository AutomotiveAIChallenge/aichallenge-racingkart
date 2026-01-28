#!/bin/bash

IS_ROSBAG_MODE=0
IS_CAPTURE_MODE=0
ROS_DOMAIN_ID_SIM=0
ROS_DOMAIN_ID_DEFAULT=1
ROS_DOMAIN_ID=$ROS_DOMAIN_ID_DEFAULT
INPUT_RESULT="d$ROS_DOMAIN_ID-result-details.json"

HOST_UID=""
HOST_GID=""
INPUT_RESULT="d$ROS_DOMAIN_ID_DEFAULT-result-details.json"
OUTPUT_ROOT="/output"
RESULT_WAIT_SECONDS=10

RE_NUMBER='^[0-9]+$' # 数字のみにマッチする正規表現
OTHER_ARGS=()        # 既知オプション以外の引数を保持（互換用）

PID_AWSIM=""
PID_AUTOWARE=""
PID_ROSBAG=""
OUTPUT_DIRECTORY=""
RUN_ID=""
RUN_LOG_FILE=""
RUN_ROOT=""
DOMAIN_OUTPUT_DIR=""
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

stop_ros2_daemon_best_effort() {
    if ! command -v ros2 >/dev/null 2>&1; then
        return 0
    fi
    if command -v timeout >/dev/null 2>&1; then
        best_effort timeout 5s ros2 daemon stop
    else
        best_effort ros2 daemon stop
    fi
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
            INPUT_RESULT="d${ROS_DOMAIN_ID}-result-details.json"
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
        INPUT_RESULT="d${ROS_DOMAIN_ID}-result-details.json"
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
    local timeout=10 # 10 seconds timeout
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
    local ts legacy
    ts=$(date +%Y%m%d-%H%M%S)

    RUN_ID="$ts"

    mkdir -p "$OUTPUT_ROOT" || exit 1
    if [ -e "$OUTPUT_ROOT/$RUN_ID" ]; then
        RUN_ID="${RUN_ID}-$$"
    fi
    RUN_ROOT="$OUTPUT_ROOT/$RUN_ID"
    mkdir -p "$RUN_ROOT" || exit 1

    DOMAIN_OUTPUT_DIR="${RUN_ROOT}/d${ROS_DOMAIN_ID}"
    mkdir -p "$DOMAIN_OUTPUT_DIR" || exit 1
    cd "$DOMAIN_OUTPUT_DIR" || exit 1
    OUTPUT_DIRECTORY="$(pwd)"

    RUN_LOG_FILE="${OUTPUT_DIRECTORY}/run_evaluation.log"
    touch "$RUN_LOG_FILE" || true
    exec > >(tee -a "$RUN_LOG_FILE") 2>&1

    cd "$OUTPUT_ROOT" || exit 1
    if [ -e latest ] && [ ! -L latest ]; then
        mkdir -p "$OUTPUT_ROOT/_host" || true
        legacy="$OUTPUT_ROOT/_host/legacy-output-latest-${RUN_ID}"
        warn "Found '${OUTPUT_ROOT}/latest' as a directory/file. Moving to '${legacy}' to restore symlink behavior."
        mv latest "$legacy" || warn "Failed to move legacy latest directory (continuing)."
    fi
    ln -nfs "$RUN_ID" latest || warn "Failed to update latest symlink (continuing)."

    cd "$OUTPUT_DIRECTORY" || exit 1
    log "Output directory: $OUTPUT_DIRECTORY"
    log "Run root directory: $RUN_ROOT"
    log "Domain output directory: $DOMAIN_OUTPUT_DIR"
    log "Run log file: $RUN_LOG_FILE"
}

setup_ros_env() {
    # shellcheck disable=SC1091
    source /aichallenge/workspace/install/setup.bash
    export ROS_DOMAIN_ID=$ROS_DOMAIN_ID
}

setup_ros_logs() {
    # Keep ROS logs under the run directory (instead of ~/.ros/log).
    export ROS_HOME="${OUTPUT_DIRECTORY}/ros"
    export ROS_LOG_DIR="${ROS_HOME}/log"
    mkdir -p "${ROS_LOG_DIR}" || true
    log "ROS_HOME: ${ROS_HOME}"
    log "ROS_LOG_DIR: ${ROS_LOG_DIR}"
}

tune_network_best_effort() {
    best_effort sudo -n ip link set multicast on lo
    best_effort sudo -n sysctl -w net.core.rmem_max=2147483647
}

start_simulator() {
    log "Start AWSIM"
    if command -v setsid >/dev/null 2>&1; then
        nohup setsid /aichallenge/run_simulator.bash eval >"${OUTPUT_DIRECTORY}/awsim.log" 2>&1 &
    else
        nohup /aichallenge/run_simulator.bash eval >"${OUTPUT_DIRECTORY}/awsim.log" 2>&1 &
    fi
    PID_AWSIM=$!
    log "AWSIM PID: $PID_AWSIM"
}

start_autoware() {
    log "Start Autoware"
    if command -v setsid >/dev/null 2>&1; then
        nohup setsid /aichallenge/run_autoware.bash awsim "$ROS_DOMAIN_ID" >"${OUTPUT_DIRECTORY}/autoware.log" 2>&1 &
    else
        nohup /aichallenge/run_autoware.bash awsim "$ROS_DOMAIN_ID" >"${OUTPUT_DIRECTORY}/autoware.log" 2>&1 &
    fi
    PID_AUTOWARE=$!
    log "Autoware PID: $PID_AUTOWARE"
}

start_screen_capture_if_needed() {
    if [ "$IS_CAPTURE_MODE" -eq 1 ]; then
        bash /aichallenge/utils/publish.bash request-capture
        CAPTURE_STARTED=1
        log "Screen capture started."
    else
        log "Screen capture skipped."
    fi
}

stop_screen_capture_if_needed() {
    if [ "$CAPTURE_STARTED" -eq 1 ] && [ "$CAPTURE_STOPPED" -eq 0 ]; then
        log "Stop screen capture"
        bash /aichallenge/utils/publish.bash request-capture || true
        CAPTURE_STOPPED=1
    fi
}

start_rosbag_if_needed() {
    if [ "$IS_ROSBAG_MODE" -eq 1 ]; then
        log "Start rosbag"
        nohup /aichallenge/utils/record_rosbag.bash >"${OUTPUT_DIRECTORY}/rosbag.log" 2>&1 &
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

get_pgid_of_pid() {
    local pid="$1"
    ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' '
}

get_self_pgid() {
    ps -o pgid= -p $$ 2>/dev/null | tr -d ' '
}

get_sid_of_pid() {
    local pid="$1"
    ps -o sid= -p "$pid" 2>/dev/null | tr -d ' '
}

get_self_sid() {
    ps -o sid= -p $$ 2>/dev/null | tr -d ' '
}

is_sid_safe_to_signal() {
    local sid="$1"
    if [ -z "$sid" ]; then
        return 1
    fi
    local self_sid
    self_sid=$(get_self_sid)
    [ -z "$self_sid" ] || [ "$sid" != "$self_sid" ]
}

kill_sid_safe() {
    local sid="$1"
    local signal="${2:-TERM}"

    if [ -z "$sid" ]; then
        return 0
    fi

    local -a pids=()
    mapfile -t pids < <(pgrep -s "$sid" 2>/dev/null || true)
    if [ "${#pids[@]}" -eq 0 ]; then
        return 0
    fi

    kill "-$signal" "${pids[@]}" 2>/dev/null || true
}

is_session_running() {
    local sid="$1"
    pgrep -s "$sid" >/dev/null 2>&1
}

stop_process_name_best_effort() {
    local name="$1"
    local label="${2:-$1}"

    if ! command -v pgrep >/dev/null 2>&1; then
        return 0
    fi

    local pids
    pids=$(pgrep -x "$name" 2>/dev/null || true)
    if [ -z "$pids" ]; then
        return 0
    fi

    warn "Leftover ${label} detected. Stopping..."

    local pid pgid
    for pid in $pids; do
        pgid=$(get_pgid_of_pid "$pid")
        log "Stop leftover ${label} (PID: ${pid}, PGID: ${pgid:-NA})"
        if is_pgid_safe_to_signal "$pgid"; then
            kill_pgid_safe "$pgid" INT
        else
            kill -INT "$pid" 2>/dev/null || true
        fi
    done

    local i
    for ((i = 0; i < 50; i++)); do
        if ! pgrep -x "$name" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.1
    done

    warn "Leftover ${label} did not exit. Sending SIGKILL..."
    pids=$(pgrep -x "$name" 2>/dev/null || true)
    for pid in $pids; do
        pgid=$(get_pgid_of_pid "$pid")
        if is_pgid_safe_to_signal "$pgid"; then
            kill_pgid_safe "$pgid" KILL
        else
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

stop_pids_matching_cmdline_best_effort() {
    local pattern="$1"
    local label="$2"

    if ! command -v pgrep >/dev/null 2>&1; then
        return 0
    fi

    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [ -z "$pids" ]; then
        return 0
    fi

    warn "Leftover ${label} detected. Stopping..."

    local pid pgid
    for pid in $pids; do
        pgid=$(get_pgid_of_pid "$pid")
        log "Stop leftover ${label} (PID: ${pid}, PGID: ${pgid:-NA})"
        if is_pgid_safe_to_signal "$pgid"; then
            kill_pgid_safe "$pgid" INT
        else
            kill -INT "$pid" 2>/dev/null || true
        fi
    done
}

kill_pgid_safe() {
    local pgid="$1"
    local signal="${2:-TERM}"

    if [ -z "$pgid" ]; then
        return 0
    fi

    local self_pgid
    self_pgid=$(get_self_pgid)
    if [ -n "$self_pgid" ] && [ "$pgid" = "$self_pgid" ]; then
        return 0
    fi

    kill "-$signal" -- "-$pgid" 2>/dev/null || true
}

is_pgid_safe_to_signal() {
    local pgid="$1"
    if [ -z "$pgid" ]; then
        return 1
    fi
    local self_pgid
    self_pgid=$(get_self_pgid)
    [ -z "$self_pgid" ] || [ "$pgid" != "$self_pgid" ]
}

is_process_group_running() {
    local pgid="$1"
    pgrep -g "$pgid" >/dev/null 2>&1
}

stop_nohup_process_if_needed() {
    local label="$1"
    local pid="$2"

    if [ -z "$pid" ]; then
        return 0
    fi

    local pid_running=0
    if kill -0 "$pid" 2>/dev/null; then
        pid_running=1
    fi

    local sid
    sid=$(get_sid_of_pid "$pid")
    if [ -z "$sid" ] && is_session_running "$pid"; then
        # When started with `setsid`, session id == original PID (even if the leader is already gone)
        sid="$pid"
    fi

    local pgid
    pgid=$(get_pgid_of_pid "$pid")

    local has_targets=0
    if [ "$pid_running" -eq 1 ]; then
        has_targets=1
    elif [ -n "$sid" ] && is_session_running "$sid"; then
        has_targets=1
    elif [ -n "$pgid" ] && is_process_group_running "$pgid"; then
        has_targets=1
    fi
    if [ "$has_targets" -ne 1 ]; then
        return 0
    fi

    log "Stop ${label} (PID: ${pid}, SID: ${sid:-NA}, PGID: ${pgid:-NA})"

    if is_sid_safe_to_signal "$sid"; then
        kill_sid_safe "$sid" INT
    elif is_pgid_safe_to_signal "$pgid"; then
        kill_pgid_safe "$pgid" INT
    else
        if [ "$pid_running" -eq 1 ]; then
            kill -INT "$pid" 2>/dev/null || true
        fi
    fi

    local i
    for ((i = 0; i < 50; i++)); do
        if is_sid_safe_to_signal "$sid"; then
            if ! is_session_running "$sid"; then
                break
            fi
        elif is_pgid_safe_to_signal "$pgid"; then
            if ! is_process_group_running "$pgid"; then
                break
            fi
        else
            if [ "$pid_running" -eq 1 ] && ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
        fi
        sleep 0.1
    done

    local still_running=0
    if is_sid_safe_to_signal "$sid"; then
        if is_session_running "$sid"; then
            still_running=1
        fi
    elif is_pgid_safe_to_signal "$pgid"; then
        if is_process_group_running "$pgid"; then
            still_running=1
        fi
    else
        if [ "$pid_running" -eq 1 ] && kill -0 "$pid" 2>/dev/null; then
            still_running=1
        fi
    fi

    if [ "$still_running" -eq 1 ]; then
        warn "${label} did not exit. Sending SIGKILL..."
        if is_sid_safe_to_signal "$sid"; then
            kill_sid_safe "$sid" KILL
        elif is_pgid_safe_to_signal "$pgid"; then
            kill_pgid_safe "$pgid" KILL
        else
            if [ "$pid_running" -eq 1 ]; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi

        for ((i = 0; i < 50; i++)); do
            if is_sid_safe_to_signal "$sid"; then
                if ! is_session_running "$sid"; then
                    break
                fi
            elif is_pgid_safe_to_signal "$pgid"; then
                if ! is_process_group_running "$pgid"; then
                    break
                fi
            else
                if ! kill -0 "$pid" 2>/dev/null; then
                    break
                fi
            fi
            sleep 0.1
        done
    fi

    wait "$pid" 2>/dev/null || true
}

convert_result_best_effort() {
    log "Convert result (wait up to ${RESULT_WAIT_SECONDS}s for $INPUT_RESULT)"
    for ((i = 0; i < RESULT_WAIT_SECONDS; i++)); do
        [ -s "$INPUT_RESULT" ] && break
        sleep 1
    done
    python3 /aichallenge/workspace/src/aichallenge_system/script/result-converter.py --input "$INPUT_RESULT" || true
}

fix_ownership_if_needed() {
    if [ "$OWNERSHIP_DONE" -eq 1 ]; then
        return 0
    fi
    if [ -n "$HOST_UID" ] && [ -n "$HOST_GID" ]; then
        if [ "$(id -u)" -eq 0 ]; then
            log "Running as root. Changing ownership of artifacts to ${HOST_UID}:${HOST_GID}..."
            log "Target directory: ${RUN_ROOT:-$(pwd)}"
            if [ -n "${RUN_ROOT-}" ]; then
                chown -R "${HOST_UID}:${HOST_GID}" "${RUN_ROOT}" || true
            else
                chown -R "${HOST_UID}:${HOST_GID}" "$(pwd)" || true
            fi
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
    stop_nohup_process_if_needed "Autoware" "$PID_AUTOWARE"
    stop_nohup_process_if_needed "AWSIM" "$PID_AWSIM"
    stop_pids_matching_cmdline_best_effort "/opt/ros/humble/bin/ros2" "ros2 (launch/cli)"
    stop_ros2_daemon_best_effort
    stop_pids_matching_cmdline_best_effort "ros2cli.daemon" "ros2 daemon"
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
    setup_ros_logs
    setup_ros_env
    tune_network_best_effort

    start_simulator

    log "Check simulator readiness"
    run_or_exit "AWSIM /clock wait" env ROS_DOMAIN_ID="$ROS_DOMAIN_ID_SIM" bash /aichallenge/utils/publish.bash check-awsim
    log "AWSIM is ready."

    start_autoware
    sleep 3
    move_window
    run_or_exit "Initial pose set" /aichallenge/utils/publish.bash request-initialpose
    run_or_exit "Control request" /aichallenge/utils/publish.bash request-control
    start_screen_capture_if_needed
    start_rosbag_if_needed

    wait "$PID_AWSIM" || true
    convert_result_best_effort
    log "Evaluation Script finished. Cleaning up..."
}

main "$@"
