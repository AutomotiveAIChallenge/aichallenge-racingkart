#!/bin/bash

target=${1}

migrate_legacy_output_latest() {
    # `output/latest` was historically used as a directory for host logs.
    # We now reserve `output/latest` as a symlink to the latest evaluation run.
    if [ -e "output/latest" ] && [ ! -L "output/latest" ]; then
        mkdir -p output/_host
        local ts legacy
        ts="$(date +%Y%m%d-%H%M%S)"
        legacy="output/_host/legacy-output-latest-${ts}-$$"
        echo "[INFO] Moving legacy 'output/latest' to '${legacy}'"
        mv output/latest "${legacy}"
    fi
}

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

migrate_legacy_output_latest

mkdir -p output/_host
EVENT_ID="$(date +%Y%m%d-%H%M%S)-docker_build-${target}-$$"
LOG_DIR="output/_host/${EVENT_ID}"
mkdir -p "$LOG_DIR"
ln -nfs "${EVENT_ID}" output/_host/latest
LOG_FILE="${LOG_DIR}/docker_build.log"
echo "A build log is stored at : ${LOG_FILE}"

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" -t "aichallenge-2025-${target}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
