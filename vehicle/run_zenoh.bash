#!/bin/bash

set -euo pipefail

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

CHILD=""
RUNNING=1

fix_ownership() {
    bash /aichallenge/utils/fix_ownership.bash \
        "${HOST_UID-}" "${HOST_GID-}" \
        "${OUTPUT_ROOT:-/output}" "$(dirname "${out_dir}")" || true
}

# Stop the retry loop and forward SIGTERM to the bridge on docker stop, so the
# loop does not immediately restart it.
shutdown() {
    RUNNING=0
    if [ -n "${CHILD}" ]; then
        kill -TERM "${CHILD}" 2>/dev/null || true
    fi
}

export ROS_DOMAIN_ID="${id}"

mkdir -p "${out_dir}"
exec >"${out_dir}/zenoh.log" 2>&1
trap fix_ownership EXIT
trap shutdown SIGINT SIGTERM

cd "${out_dir}" || exit 1

while [ "${RUNNING}" = "1" ]; do
    zenoh-bridge-ros2dds client -e "tls/zenoh.dev.aichallenge-board.jsae.or.jp:${PORT}" -c /vehicle/zenoh.json5 &
    CHILD=$!
    status=0
    wait "${CHILD}" || status=$?
    CHILD=""
    [ "${RUNNING}" = "1" ] || break
    echo "zenoh-bridge-ros2dds exited with status ${status}; retrying in 5s..."
    sleep 5 || true
done
