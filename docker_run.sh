#!/bin/bash

target="${1}"
device="${2}"
device_drivers="/dev/dri"

case "${target}" in
"eval")
    volume="output:/output /run/user:/run/user:rw"
    ;;
"dev")
    volume="output:/output aichallenge:/aichallenge remote:/remote vehicle:/vehicle /dev/input:/dev/input /run/user:/run/user:rw"
    ;;
"rm")
    # clean up old <none> images
    docker image prune -f
    exit 1
    ;;
*)
    echo "invalid argument (use 'dev' or 'eval')"
    exit 1
    ;;
esac

if [ "${device}" = "cpu" ]; then
    opts=""
    echo "[INFO] Running in CPU mode (forced by argument)"
elif [ "${device}" = "gpu" ]; then
    opts="--nvidia"
    echo "[INFO] Running in GPU mode (forced by argument)"
elif [[ -e /dev/nvidia0 ]]; then
    opts="--nvidia"
    echo "[INFO] NVIDIA device node detected (/dev/nvidia0) → enabling --nvidia"
else
    opts=""
    echo "[INFO] No NVIDIA GPU detected → running on CPU"
fi

ts="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="output/docker/${ts}-docker_run-$$.log"
mkdir -p output/docker output/latest
ln -sfn "${PWD}/${LOG_FILE}" output/latest/docker_run.log

# shellcheck disable=SC2086
rocker ${opts} --x11 --devices ${device_drivers} --user --pulse --net host --privileged --name "aichallenge-2025-$(date "+%Y-%m-%d-%H-%M-%S")" --volume ${volume} -- "aichallenge-2025-${target}" 2>&1 | tee "$LOG_FILE"
