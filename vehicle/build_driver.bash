#!/usr/bin/env bash

set -eo pipefail

# Builds the racing_kart_interface workspace mounted at /workspace, the same path
# the driver container runs it at (--symlink-install bakes absolute paths into
# install/, so the two have to match). Artifacts stay in the mounted host
# repository, the way build_autoware.bash works under /aichallenge.
#
# The build recipe is restated here rather than reusing racing_kart_interface's own
# docker/entrypoint.sh: its "build" mode imports dependencies into src instead of
# src/depends, and its rosdep step needs sudo, which the non-root compose user does
# not have. Reusing it would require changing racing_kart_interface.

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash

cd /workspace

# src/depends is gitignored, so a freshly cloned repository has none. Existing
# checkouts are left alone, so repeat builds do not hit the network.
# vcs import needs the target directory to exist already.
# rosdep is not run: its apt packages are already baked into the image.
if [ ! -d src/depends ]; then
    mkdir -p src/depends
    vcs import --shallow --input depends.repos src/depends
fi

colcon build --symlink-install --packages-up-to racing_kart_launch --cmake-args -DCMAKE_BUILD_TYPE=Release

echo "[build_driver] Build successful."
