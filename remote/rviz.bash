#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
MAKE_DIR="${REPO_ROOT}"
# COMPOSE_FILE is defined in .env (read automatically by docker compose)

usage() {
    cat <<'USAGE'
Usage:
  rviz.bash            # start RViz stack via make rviz2
  rviz.bash down       # stop and remove rviz2 service
  rviz.bash restart    # restart rviz2 service
USAGE
}

if [ ! -d "${MAKE_DIR}" ]; then
    echo "Error: repository root directory not found at '${MAKE_DIR}'." >&2
    exit 1
fi

if [ ! -f "${MAKE_DIR}/Makefile" ]; then
    echo "Error: Makefile not found in '${MAKE_DIR}'." >&2
    exit 1
fi

if [ ! -f "${REPO_ROOT}/docker-compose.yml" ]; then
    echo "Error: docker-compose.yml not found at '${REPO_ROOT}/docker-compose.yml'." >&2
    exit 1
fi

mode="start"
if [ $# -gt 0 ]; then
    case "$1" in
    down)
        mode="down"
        shift
        ;;
    restart)
        mode="restart"
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "Error: unknown argument '$1'." >&2
        usage
        exit 1
        ;;
    esac
fi

if [ $# -gt 0 ]; then
    echo "Error: too many arguments." >&2
    usage
    exit 1
fi

case "${mode}" in
start)
    echo "Running 'make rviz2' inside '${MAKE_DIR}'."
    cd "${MAKE_DIR}"
    make rviz2
    ;;
down)
    echo "Stopping and removing 'rviz2' service."
    cd "${MAKE_DIR}"
    docker compose rm -f -s rviz2
    ;;
restart)
    echo "Restarting 'rviz2' service."
    cd "${MAKE_DIR}"
    docker compose rm -f -s rviz2
    echo "Running 'make rviz2' inside '${MAKE_DIR}' after restart."
    cd "${MAKE_DIR}"
    make rviz2
    ;;
*)
    echo "Error: unsupported mode '${mode}'." >&2
    exit 1
    ;;
esac
