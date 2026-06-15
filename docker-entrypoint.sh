#!/bin/bash
# Common container initialization: ROS workspace setup.
# Used as ENTRYPOINT in Dockerfile and sourced from /etc/skel/.bashrc for any
# interactive shell session (rocker, docker_run.sh, docker_exec.sh).
# DDS host tuning (lo multicast + net.core.rmem_max) is applied on the HOST via
# `./setup.bash network tune`; containers run as the host user and cannot set it.

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
