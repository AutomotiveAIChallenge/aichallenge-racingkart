#!/bin/bash

set -euo pipefail

mode="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"

CHILD=""

fix_ownership() {
    bash /aichallenge/utils/fix_ownership.bash \
        "${HOST_UID-}" "${HOST_GID-}" \
        "${OUTPUT_ROOT:-/output}" "$(dirname "${out_dir}")" || true
}

# Forward docker stop (SIGTERM) to the ROS process so it shuts down gracefully
# instead of being SIGKILLed when this PID 1 exits.
forward_term() {
    if [ -n "${CHILD}" ]; then
        kill -TERM "${CHILD}" 2>/dev/null || true
    fi
}

export ROS_DOMAIN_ID="${id}"

mkdir -p "${out_dir}"
exec >"${out_dir}/driver.log" 2>&1
trap fix_ownership EXIT
trap forward_term SIGINT SIGTERM

cd "${out_dir}" || exit 1
export ROS_HOME="${out_dir}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
mkdir -p "${ROS_LOG_DIR}"

/entrypoint.sh "${mode}" "${@:4}" &
CHILD=$!
# `wait` returns when interrupted by a trapped signal; loop until the child exits.
while ! wait "${CHILD}"; do
    kill -0 "${CHILD}" 2>/dev/null || break
done
