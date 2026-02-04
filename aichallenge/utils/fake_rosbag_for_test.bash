#!/usr/bin/env bash
set -euo pipefail

echo "FAKE_ROSBAG: started"

cleanup() {
    echo "FAKE_ROSBAG: exiting"
    exit 0
}

trap cleanup INT TERM

while :; do
    sleep 1
done

