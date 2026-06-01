#!/bin/bash

set -euo pipefail

id="${1:-${ROS_DOMAIN_ID:-1}}"
log_dir="${2:-${LOG_DIR-}}"
racing_kart_interface_dir="${3:-${RACING_KART_INTERFACE_DIR:-/home/tier4/racing_kart_interface}}"
out_dir="${log_dir:+${log_dir}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"
bag_name="rosbag2_all"
bag_dir="${out_dir}/${bag_name}"

PID=""
FINALIZED=0

source_setup() {
    local setup_file="$1"
    if [ ! -f "${setup_file}" ]; then
        echo "[ERROR] required setup file not found: ${setup_file}" >&2
        exit 1
    fi

    # ROS setup scripts may read unset environment variables.
    set +u
    # shellcheck disable=SC1090
    source "${setup_file}"
    set -u
}

write_post_record_outputs() {
    if [ -d "${bag_dir}" ]; then
        ros2 bag info "${bag_dir}" >"${out_dir}/rosbag2_all_info.txt" 2>&1 || true
    else
        echo "rosbag directory not found: ${bag_dir}" >"${out_dir}/rosbag2_all_info.txt"
    fi
}

fix_ownership() {
    bash /aichallenge/utils/fix_ownership.bash \
        "${HOST_UID-}" \
        "${HOST_GID-}" \
        /output \
        "$(dirname "${out_dir}")" || true
}

finish_recording() {
    if [ "${FINALIZED}" = "1" ]; then
        return 0
    fi
    FINALIZED=1

    if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
        echo "Stopping ros2 bag record with SIGINT (PID/PGID=${PID})..."
        kill -INT -- "-${PID}" 2>/dev/null || kill -INT "${PID}" 2>/dev/null || true
        wait "${PID}" 2>/dev/null || true
    fi
    PID=""

    write_post_record_outputs
    fix_ownership
}

on_signal() {
    echo "Signal received; finalizing rosbag..."
    finish_recording
    exit 0
}

export ROS_DOMAIN_ID="${id}"

mkdir -p "${out_dir}"
exec >"${out_dir}/rosbag.log" 2>&1

trap finish_recording EXIT
trap on_signal SIGINT SIGTERM

cd "${out_dir}" || exit 1
export ROS_HOME="${out_dir}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
mkdir -p "${ROS_LOG_DIR}"

echo "Starting all-topic rosbag recording"
echo "out_dir: ${out_dir}"
echo "bag_dir: ${bag_dir}"
echo "ROS_DOMAIN_ID: ${ROS_DOMAIN_ID}"
echo "racing_kart_interface_dir: ${racing_kart_interface_dir}"

source_setup "/opt/ros/humble/setup.bash"
source_setup "/aichallenge/workspace/install/setup.bash"
source_setup "${racing_kart_interface_dir}/install/setup.bash"

record_cmd=(
    ros2 bag record
    -a
    --include-hidden-topics
    -s mcap
    --compression-format zstd
    --compression-mode file
    -o "${bag_name}"
)

echo "record command: ${record_cmd[*]}"

if command -v setsid >/dev/null 2>&1; then
    setsid "${record_cmd[@]}" &
else
    "${record_cmd[@]}" &
fi
PID=$!
echo "ros2 bag record process started with PID/PGID: ${PID}"

wait "${PID}" || true
PID=""
finish_recording
