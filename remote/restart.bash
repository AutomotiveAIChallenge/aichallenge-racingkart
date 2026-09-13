#!/bin/bash
set -euo pipefail

./rviz.bash down

./rviz.bash &

echo "5秒待機しzenohに接続します... / Waiting 5 s, then connecting to zenoh..."
sleep 5

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONNECT_SCRIPT="${SCRIPT_DIR}/connect_zenoh.bash"

usage() {
    echo "Usage: $0 {A2|A3|A6|A7|test-*}" >&2
}

if [ ! -x "${CONNECT_SCRIPT}" ]; then
    echo "Error: connect script not found or not executable: ${CONNECT_SCRIPT}" >&2
    exit 1
fi

if [ $# -ne 1 ]; then
    usage
    exit 1
fi

TARGET="$1"

echo "Stopping existing 'zenoh-bridge-ros2dds' processes..."
pkill -f 'zenoh-bridge-ro' >/dev/null 2>&1 || true

echo "Waiting for processes to terminate..."
while pgrep -f 'zenoh-bridge-ro' >/dev/null 2>&1; do
    sleep 0.5
done

echo "Restarting zenoh bridge for target '${TARGET}'"
exec "${CONNECT_SCRIPT}" "${TARGET}"
