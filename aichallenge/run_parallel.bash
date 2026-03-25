#!/usr/bin/env bash
# Run multiple Autoware instances in parallel, each with a distinct ROS Domain ID.
# The simulator runs on Domain 0; participants run on Domain 1..N.

set -euo pipefail

# Number of parallel vehicles (default: 4, max: 4)
vehicles="${VEHICLES:-4}"
if (( vehicles < 1 || vehicles > 4 )); then
    echo "[ERROR] VEHICLES must be between 1 and 4 (got: ${vehicles})" >&2
    exit 1
fi

# Determine simulator mode based on vehicle count
sim_mode="${SIM_MODE:-${vehicles}p}"

ts="$(date +%Y%m%d-%H%M%S)"

# Create output directories for each domain
for i in $(seq 1 "$vehicles"); do
    mkdir -p "/output/${ts}/d${i}/ros/log"
done

# --- Trap for graceful shutdown ---
declare -a ALL_PIDS=()
cleanup() {
    echo "[INFO] Shutting down all processes..."
    for pid in "${ALL_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- Start simulator (Domain 0) ---
echo "[INFO] Starting AWSIM in '${sim_mode}' mode (vehicles=${vehicles})"
export ROS_DOMAIN_ID=0
/aichallenge/run_simulator.bash "${sim_mode}" &
SIM_PID=$!
ALL_PIDS+=("$SIM_PID")

# Wait for simulator to initialise
sleep 10

# --- Start Autoware for each domain ---
declare -a AW_PIDS=()
for i in $(seq 1 "$vehicles"); do
    out_dir="/output/${ts}/d${i}"
    log_file="${out_dir}/autoware.log"

    # D1 uses the default workspace; D2-D4 use /aichallenge/d{N}/workspace
    if [ "$i" -eq 1 ]; then
        ws="/aichallenge/workspace"
    else
        ws="/aichallenge/d${i}/workspace"
    fi

    (
        export ROS_DOMAIN_ID="$i"
        export ROS_HOME="${out_dir}/ros"
        export ROS_LOG_DIR="${ROS_HOME}/log"
        # Source the workspace-specific overlay
        # shellcheck disable=SC1091
        source /autoware/install/setup.bash
        if [ -f "${ws}/install/local_setup.bash" ]; then
            # shellcheck disable=SC1091
            source "${ws}/install/local_setup.bash"
        fi

        cd "${out_dir}"
        echo "[INFO] Starting Autoware D${i} (ROS_DOMAIN_ID=${i}, workspace=${ws})"
        exec ros2 launch aichallenge_system_launch evaluation.launch.xml \
            "domain_id:=${i}" \
            "sim_mode:=eval" \
            "log_dir:=${out_dir}" \
            "capture:=true" \
            "rosbag:=true" \
            "simulation:=true" \
            "use_sim_time:=true" \
            "run_rviz:=false"
    ) > "${log_file}" 2>&1 &
    AW_PIDS+=($!)
    ALL_PIDS+=($!)
    echo "[INFO] Autoware D${i} started (PID=$!)"
done

# --- Wait for completion ---
echo "[INFO] Waiting for all Autoware instances to finish..."
aw_exit=0
for pid in "${AW_PIDS[@]}"; do
    wait "$pid" || aw_exit=$?
done

echo "[INFO] All Autoware instances finished (last exit: ${aw_exit}). Waiting for simulator..."
wait "$SIM_PID" || true

echo "[INFO] Parallel execution complete. Results in /output/${ts}/"
