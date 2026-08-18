#!/usr/bin/env bash

set -eo pipefail

# Usage:
#   build_autoware.bash [clean] [HOST_UID HOST_GID] [PACKAGE ...]
#
# Notes:
#   - If "clean" is provided, workspace/{build,install,log} are removed before building.
#   - If running as root and HOST_UID/HOST_GID are provided, ownership is fixed after build.
#   - Trailing arguments select packages to build (with their dependencies). The remote
#     operation PC only needs racing_kart_msgs, not the whole Autoware overlay.

action="${1-}"
if [ "${action}" = "clean" ]; then
    echo "[build_autoware] Cleaning build directories..."
    rm -rf ./workspace/build ./workspace/install ./workspace/log
    echo "[build_autoware] Clean complete."
    shift
fi

HOST_UID="${1-}"
HOST_GID="${2-}"
# UID/GID は省略可なので、実際に渡された数だけ捨てる。ここを shift 2 固定にすると
# 片方だけ渡されたときに残りをパッケージ名と取り違える。
for _ in 1 2; do
    [ "$#" -gt 0 ] && shift
done

# 残りの引数はビルド対象のパッケージ。指定が無ければ従来どおり全部ビルドする。
select_args=()
if [ "$#" -gt 0 ]; then
    select_args=(--packages-up-to "$@")
    echo "[build_autoware] Building selected packages: $*"
fi

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source /autoware/install/setup.bash

cd ./workspace

# NOTE: gyro_odometer exists in the Autoware underlay, so allow overriding in this overlay workspace.
colcon build --symlink-install --allow-overriding gyro_odometer "${select_args[@]}" --cmake-args -DCMAKE_BUILD_TYPE=Release

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
