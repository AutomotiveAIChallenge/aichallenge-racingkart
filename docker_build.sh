#!/bin/bash

set -euo pipefail

target="${1-}"
shift || true

SUBMIT_TAR="${SUBMIT_TAR-}"

if [ -z "${target}" ]; then
    echo "Usage: ./docker_build.sh <dev|eval> [--submit <path/to/aichallenge_submit.tar.gz>]" >&2
    exit 2
fi

while [ $# -gt 0 ]; do
    case "$1" in
    --submit | --submit-tar)
        SUBMIT_TAR="${2-}"
        shift 2
        ;;
    --)
        shift
        break
        ;;
    *)
        echo "invalid argument: '$1'" >&2
        echo "Usage: ./docker_build.sh <dev|eval> [--submit <path/to/aichallenge_submit.tar.gz>]" >&2
        exit 2
        ;;
    esac
done

case "${target}" in
"eval")
    opts="--no-cache"
    ;;
"dev")
    opts=""
    ;;
*)
    echo "invalid argument (use 'dev' or 'eval')"
    exit 1
    ;;
esac

ts="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="output/docker/${ts}-docker_build-$$.log"
mkdir -p output/docker output/latest
ln -sfn "${PWD}/${LOG_FILE}" output/latest/docker_build.log

BUILD_ARGS=()
if [ "$target" = "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    if [ ! -f "${SUBMIT_TAR}" ]; then
        echo "[ERROR] submit file not found: ${SUBMIT_TAR}" >&2
        exit 1
    fi
    BUILD_ARGS+=(--build-arg "SUBMIT_TAR=${SUBMIT_TAR}")
    echo "[INFO] Using submit tar: ${SUBMIT_TAR}"
elif [ "$target" != "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    echo "[WARN] --submit is only used for target=eval (ignored): ${SUBMIT_TAR}" >&2
fi

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" "${BUILD_ARGS[@]}" -t "aichallenge-2025-${target}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
