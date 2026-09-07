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

# Build flags. SYMLINK_INSTALL=0 produces a relocatable install/ (real files instead of
# symlinks into src/ and build/), required for prestaged submissions.
# See docs/spec/prestaged-submissions.md
SYMLINK_INSTALL="${SYMLINK_INSTALL:-1}"

# A prestaged team's install/ is a real-copy build (SYMLINK_INSTALL=0). A default
# --symlink-install build here would rewrite parts of it with symlinks pointing
# into src/ and build/, corrupting the prestaged workspace. This must run before
# sourcing ROS (below) so it is testable with DRY_RUN=1 outside the container.
# Checked relative to this script's cwd (./workspace/...) because the script cds
# into ./workspace later.
if [ -f ./workspace/.staged_team ] && [ "${SYMLINK_INSTALL}" != "0" ]; then
    echo "[build_autoware] ERROR: a prestaged team is staged (./workspace/.staged_team present)." >&2
    echo "[build_autoware] a --symlink-install build would corrupt its real-copy install/." >&2
    echo "[build_autoware] unstage first (unstage_team.sh), or build with SYMLINK_INSTALL=0." >&2
    echo "[build_autoware] see docs/spec/prestaged-submissions.md" >&2
    exit 1
fi

colcon_args=(build)
if [ "${SYMLINK_INSTALL}" != "0" ]; then
    colcon_args+=(--symlink-install)
fi
colcon_args+=(--allow-overriding gyro_odometer --cmake-args -DCMAKE_BUILD_TYPE=Release)

# DRY_RUN prints the resolved command without sourcing ROS, so flag resolution can be
# tested outside the container.
if [ "${DRY_RUN:-0}" != "0" ]; then
    echo "colcon ${colcon_args[*]}"
    exit 0
fi

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source /autoware/install/setup.bash

cd ./workspace

# NOTE: gyro_odometer exists in the Autoware underlay, so allow overriding in this overlay workspace.
colcon "${colcon_args[@]}"

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
