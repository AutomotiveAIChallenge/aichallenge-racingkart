#!/bin/bash

target="${1}"
device="${2}"
device_drivers="/dev/dri"

# Mount X socket + Xauthority instead of rocker --x11 (it clobbers XAUTHORITY).
x11_volume="/tmp/.X11-unix:/tmp/.X11-unix"
xauth_opts=""
uid="$(id -u)"
xauthority="${XAUTHORITY-}"
[ -z "${xauthority}" ] && [ -f "/run/user/${uid}/gdm/Xauthority" ] && xauthority="/run/user/${uid}/gdm/Xauthority"
[ -z "${xauthority}" ] && [ -f "${HOME}/.Xauthority" ] && xauthority="${HOME}/.Xauthority"
if [ -n "${xauthority}" ] && [ -f "${xauthority}" ]; then
    x11_volume="${x11_volume} ${xauthority}:${xauthority}"
    xauth_opts="--env XAUTHORITY=${xauthority}"
else
    echo "[WARN] No Xauthority file found; X11 apps (RViz/AWSIM) may fail. Try: export XAUTHORITY=~/.Xauthority"
fi

case "${target}" in
"eval")
    volume="${x11_volume} output:/output vehicle/cyclonedds.xml:/opt/autoware/cyclonedds.xml /run/user:/run/user:rw"
    ;;
"dev")
    volume="${x11_volume} output:/output aichallenge:/aichallenge remote:/remote vehicle:/vehicle vehicle/cyclonedds.xml:/opt/autoware/cyclonedds.xml /dev/input:/dev/input /run/user:/run/user:rw"
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

# Join render/video groups for /dev/dri access.
gid_render="$(getent group render | cut -d: -f3)"
gid_video="$(getent group video | cut -d: -f3)"
group_add_opts=""
[ -n "${gid_render}" ] && group_add_opts="${group_add_opts} --group-add ${gid_render}"
[ -n "${gid_video}" ] && group_add_opts="${group_add_opts} --group-add ${gid_video}"

ts="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="output/docker/${ts}-docker_run-$$.log"
mkdir -p output/docker output/latest
ln -sfn "${PWD}/${LOG_FILE}" output/latest/docker_run.log

# shellcheck disable=SC2086
rocker ${opts} --devices ${device_drivers} --user --pulse ${group_add_opts} --env DISPLAY --env QT_X11_NO_MITSHM=1 ${xauth_opts} --net host --privileged --name "aichallenge-2025-$(date "+%Y-%m-%d-%H-%M-%S")" --volume ${volume} -- "aichallenge-2025-${target}" bash 2>&1 | tee "$LOG_FILE"
