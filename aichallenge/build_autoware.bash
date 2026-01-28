#!/usr/bin/env bash

set -eo pipefail

# Usage:
#   build_autoware.bash [clean] [HOST_UID HOST_GID]
#
# Notes:
#   - If "clean" is provided, workspace/{build,install,log} are removed before building.
#   - If running as root and HOST_UID/HOST_GID are provided, ownership is fixed after build.

action="${1-}"
if [ "${action}" = "clean" ]; then
    echo "[build_autoware] Cleaning build directories..."
    rm -rf ./workspace/build ./workspace/install ./workspace/log
    echo "[build_autoware] Clean complete."
    shift
fi

HOST_UID="${1-}"
HOST_GID="${2-}"

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source /autoware/install/setup.bash

cd ./workspace

# NOTE: gyro_odometer exists in the Autoware underlay, so allow overriding in this overlay workspace.
colcon build --symlink-install --allow-overriding gyro_odometer --cmake-args -DCMAKE_BUILD_TYPE=Release

echo "[build_autoware] Build successful."

if [ -n "${HOST_UID}" ] && [ -n "${HOST_GID}" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        echo "[build_autoware] Running as root. Changing ownership of artifacts to ${HOST_UID}:${HOST_GID}..."
        chown -R "${HOST_UID}:${HOST_GID}" /aichallenge/workspace/build /aichallenge/workspace/install /aichallenge/workspace/log || true
        echo "[build_autoware] Ownership change complete."
    else
        echo "[build_autoware] Running as non-root user ($(id -u)). Skipping chown."
    fi
else
    echo "[build_autoware] HOST_UID/HOST_GID not provided. Skipping ownership change."
fi

exit 0
