#!/bin/bash

set -euo pipefail

id="${1:-${ROS_DOMAIN_ID:-1}}"
log_dir="${2:-${LOG_DIR-}}"
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

run_snapshot() {
    local suffix="$1"

    timeout 10s ros2 topic list -t >"${out_dir}/rosbag_topics_${suffix}.txt" 2>&1 || true
    timeout 10s ros2 node list >"${out_dir}/rosbag_nodes_${suffix}.txt" 2>&1 || true
    timeout 10s ros2 topic info -v /racing_kart/joy \
        >"${out_dir}/rosbag_topic_info_racing_kart_joy_${suffix}.txt" 2>&1 || true
    timeout 10s ros2 topic info -v /initialpose \
        >"${out_dir}/rosbag_topic_info_initialpose_${suffix}.txt" 2>&1 || true
}

bag_contains_topic() {
    local topic="$1"
    grep -Fq "Topic: ${topic} |" "${out_dir}/rosbag2_all_info.txt"
}

write_verify_report() {
    local autoware_topics=(
        "/control/command/control_cmd"
        "/localization/kinematic_state"
        "/planning/scenario_planning/trajectory"
        "/vehicle/status/velocity_status"
    )
    local driver_topics=(
        "/racing_kart/vcu/status"
        "/racing_kart/brake/status"
        "/racing_kart/steer/status"
        "/racing_kart/imu"
        "/racing_kart/gnss"
    )

    {
        echo "rosbag verification"
        echo "generated_at: $(date --iso-8601=seconds)"
        echo "ros_domain_id: ${ROS_DOMAIN_ID}"
        echo "bag_dir: ${bag_dir}"
        echo

        echo "[bag files]"
        if [ -d "${bag_dir}" ]; then
            find "${bag_dir}" -maxdepth 1 -type f -printf "%f\n" | sort
        else
            echo "MISSING: ${bag_dir}"
        fi
        echo

        echo "[autoware representative topics]"
        for topic in "${autoware_topics[@]}"; do
            if bag_contains_topic "${topic}"; then
                echo "OK: ${topic}"
            else
                echo "MISSING: ${topic}"
            fi
        done
        echo

        echo "[driver representative topics]"
        for topic in "${driver_topics[@]}"; do
            if bag_contains_topic "${topic}"; then
                echo "OK: ${topic}"
            else
                echo "MISSING: ${topic}"
            fi
        done
        echo

        echo "[zenoh graph checks]"
        if [ -s "${out_dir}/zenoh.log" ]; then
            echo "OK: zenoh.log exists"
        else
            echo "MISSING: zenoh.log"
        fi

        if grep -Eiq "zenoh.*bridge|bridge.*zenoh" "${out_dir}/rosbag_nodes_before.txt"; then
            echo "OK: zenoh bridge node visible before recording"
        else
            echo "WARN: zenoh bridge node was not visible in rosbag_nodes_before.txt"
        fi

        if grep -Eq "Subscription count:[[:space:]]*[1-9][0-9]*" \
            "${out_dir}/rosbag_topic_info_racing_kart_joy_before.txt"; then
            echo "OK: /racing_kart/joy has subscriber(s), expected from zenoh bridge"
        else
            echo "WARN: /racing_kart/joy subscriber was not visible before recording"
        fi

        if grep -Eq "Subscription count:[[:space:]]*[1-9][0-9]*" \
            "${out_dir}/rosbag_topic_info_initialpose_before.txt"; then
            echo "OK: /initialpose has subscriber(s), expected from zenoh bridge"
        else
            echo "WARN: /initialpose subscriber was not visible before recording"
        fi

        if bag_contains_topic "/racing_kart/joy"; then
            echo "OK: /racing_kart/joy is present in bag"
        else
            echo "INFO: /racing_kart/joy is not present in bag; this is expected if remote joy did not publish while recording"
        fi
    } >"${out_dir}/rosbag_verify.txt"
}

write_post_record_outputs() {
    run_snapshot "after"

    if [ -d "${bag_dir}" ]; then
        ros2 bag info "${bag_dir}" >"${out_dir}/rosbag2_all_info.txt" 2>&1 || true
    else
        echo "rosbag directory not found: ${bag_dir}" >"${out_dir}/rosbag2_all_info.txt"
    fi

    write_verify_report
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

source_setup "/opt/ros/humble/setup.bash"
source_setup "/aichallenge/workspace/install/setup.bash"
source_setup "/racing_kart_interface/install/setup.bash"

ros2 interface show racing_kart_msgs/msg/VcuStatus \
    >"${out_dir}/racing_kart_msgs_check.txt" 2>&1

run_snapshot "before"

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
