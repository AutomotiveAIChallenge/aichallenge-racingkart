#!/bin/bash

vehicle_id="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR source=vehicle_ports.sh
source "${script_dir}/vehicle_ports.sh"

if ! PORT="$(zenoh_port_for_vehicle_id "${vehicle_id}")"; then
    echo "Invalid VEHICLE_ID: ${vehicle_id:-(empty)} (valid: ${VEHICLE_ID_VALID_LIST})"
    exit 1
fi

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
