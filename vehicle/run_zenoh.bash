#!/bin/bash

vehicle_id="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR source=vehicle_ports.sh
source "${script_dir}/vehicle_ports.sh"

if ! ENDPOINT="$(zenoh_endpoint_for_vehicle_id "${vehicle_id}")"; then
    echo "Invalid VEHICLE_ID: ${vehicle_id:-(empty)} (valid: ${VEHICLE_ID_VALID_LIST})"
    exit 1
fi

export ROS_DOMAIN_ID=$id

# PID 1 in the zenoh container: untrapped signals are dropped and the container
# is SIGKILLed after stop_grace_period, so trap INT/TERM. Children run as jobs
# (own process group, SIGINT not ignored) so the trap can signal the group, and
# are waited on so it fires at once, even during the retry sleep.
run() {
    set -m
    "$@" &
    set +m
    wait $!
}
trap 'kill -INT -- "-$!" 2>/dev/null; wait; exit 0' INT TERM

mkdir -p "${out_dir}"
exec >"${out_dir}/zenoh.log" 2>&1

cd "${out_dir}" || exit

# The bridge reconnects by itself while running; this only covers exits.
while true; do
    run zenoh-bridge-ros2dds client -e "${ENDPOINT}" -c /vehicle/zenoh.json5 -n "/${vehicle_id}"
    echo "zenoh-bridge-ros2dds exited with status $?; retrying in 5s..."
    run sleep 5
done
