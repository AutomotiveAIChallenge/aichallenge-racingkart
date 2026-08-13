#!/bin/bash
# Start one zenoh bridge for the AWSIM-based remote-operation rehearsal (`make dev3-remote`).
#
# Topology (identical in shape to the real thing, see remote/README.md):
#   remote side (ROS_DOMAIN_ID 0)  --mTLS-->  EC2 router  <--mTLS--  vehicle side (ROS_DOMAIN_ID N)
# Both halves are zenoh clients; the EC2 router is the real one, one port per vehicle.
#
# Configs are shared with the real thing on purpose: vehicle/zenoh.json5 and
# remote/zenoh-user.json5.template are the same files remote/connect_zenoh.bash and
# vehicle/run_zenoh.bash use. Splitting them would mean the rehearsal validates a config the
# karts never run, which defeats the point of rehearsing.
#
# The two halves are kept apart by the vehicle namespace: the vehicle-side bridge runs with
# `-n /<VEHICLE_ID>`, so the vehicle's own `/racing_kart/joy` is seen as `/A2/racing_kart/joy`
# on the remote side. Inside the vehicle the topic keeps its unprefixed name, exactly as the
# real racing_kart_driver expects.
#
# Usage: sim_zenoh.bash {vehicle|remote} [domain_id]

set -eo pipefail

# Note: don't fold the usage text into ${1:?...} -- braces inside the message terminate the
# parameter expansion early and the leftovers end up appended to the value.
if [ $# -lt 1 ]; then
    echo "usage: sim_zenoh.bash {vehicle|remote} [domain_id]" >&2
    exit 1
fi
role="$1"
id="${2:-${ROS_DOMAIN_ID:-1}}"

# ROS_DOMAIN_ID N -> vehicle. A6 is omitted because its EC2 router port is not running
# (2026-08-13); add it back here and switch to dev4-remote once it is up.
SIM_VEHICLES=(A2 A3 A7)

if ! [[ ${id} =~ ^[0-9]+$ ]] || ((id < 1 || id > ${#SIM_VEHICLES[@]})); then
    echo "[sim_zenoh] domain id must be 1..${#SIM_VEHICLES[@]} (got '${id}')" >&2
    exit 1
fi
VEHICLE_ID="${SIM_VEHICLES[$((id - 1))]}"

# Same port assignment as the production scripts (vehicle/run_zenoh.bash).
case "${VEHICLE_ID}" in
A2) PORT=7448 ;;
A3) PORT=7449 ;;
A6) PORT=7450 ;;
A7) PORT=7451 ;;
*)
    echo "[sim_zenoh] no port known for vehicle '${VEHICLE_ID}'" >&2
    exit 1
    ;;
esac

ENDPOINT="tls/zenoh.dev.aichallenge-board.jsae.or.jp:${PORT}"

case "${role}" in
vehicle)
    export ROS_DOMAIN_ID="${id}"
    echo "[sim_zenoh] vehicle side: ${VEHICLE_ID} (namespace /${VEHICLE_ID}) on domain ${id} -> ${ENDPOINT}"
    exec zenoh-bridge-ros2dds client \
        -e "${ENDPOINT}" \
        -n "/${VEHICLE_ID}" \
        -c /vehicle/zenoh.json5
    ;;
remote)
    # The remote side always lives on domain 0, where every vehicle is visible at once under
    # its own namespace. AWSIM's admin topics also live on domain 0 but are not in the allow
    # list, so they are never routed.
    export ROS_DOMAIN_ID=0
    # Not cleaned up on purpose: exec replaces this shell, and the bridge keeps reading the
    # file. It lives in the container's ephemeral /tmp.
    config="$(mktemp -t "zenoh-user-${VEHICLE_ID}-XXXXXX.json5")"
    sed "s/__VEHICLE_ID__/${VEHICLE_ID}/g" /remote/zenoh-user.json5.template >"${config}"
    echo "[sim_zenoh] remote side: ${VEHICLE_ID} on domain 0 -> ${ENDPOINT}"
    exec zenoh-bridge-ros2dds client \
        -e "${ENDPOINT}" \
        -c "${config}"
    ;;
*)
    echo "[sim_zenoh] unknown role '${role}' (use 'vehicle' or 'remote')" >&2
    exit 1
    ;;
esac
