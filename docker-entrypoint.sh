#!/bin/bash
# Common container initialization: ROS workspace setup.

# --- Source ROS workspace (skip when not yet built, e.g. first dev session) ---
if [ -f /aichallenge/workspace/install/setup.bash ]; then
    # shellcheck disable=SC1091
    set +u && source /aichallenge/workspace/install/setup.bash
fi

# When used as ENTRYPOINT, hand off to the CMD / command.
# When sourced from .bashrc, exec is a no-op (no positional args).
if [ $# -gt 0 ]; then
    exec "$@"
fi
