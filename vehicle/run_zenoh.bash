#!/bin/bash

vehicle_id="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"

case "${vehicle_id}" in
A2) PORT=7448 ;;
A3) PORT=7449 ;;
A6) PORT=7450 ;;
A7) PORT=7451 ;;
A1) PORT=7452 ;;
A5) PORT=7453 ;;
A8) PORT=7454 ;;
*)
    echo "Invalid VEHICLE_ID"
    exit 1
    ;;
esac

export ROS_DOMAIN_ID=$id

mkdir -p "${out_dir}"
exec >"${out_dir}/zenoh.log" 2>&1

cd "${out_dir}" || exit

while true; do
    zenoh-bridge-ros2dds client -e "tls/zenoh.dev.aichallenge-board.jsae.or.jp:${PORT}" -c /vehicle/zenoh.json5
    status=$?
    echo "zenoh-bridge-ros2dds exited with status ${status}; retrying in 5s..."
    sleep 5
done
