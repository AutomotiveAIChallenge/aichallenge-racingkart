#!/usr/bin/env bash
# Evaluation-environment entrypoint: safety gate, single vehicle (ROS_DOMAIN_ID 1).
# Self-contained: no docker-entrypoint.sh or compose env is assumed.
# Contract: docs/interface/evaluation-interface.md §6

# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash
# The evaluation image builds the submission as an overlay under /aichallenge/d1 (aichallenge-aws makefile/Dockerfile);
# the local eval image builds it into /aichallenge/workspace, so the overlay is optional here.
if [ -f /aichallenge/d1/workspace/install/setup.bash ]; then
    # shellcheck disable=SC1091
    source /aichallenge/d1/workspace/install/setup.bash
fi

export SIM_MODE=safety-gate
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-1}"
# No rviz in the evaluation environment: capture comes from the standalone screen recorder (full X screen),
# not from rviz's capture panel, which only records the rviz window region.
export RUN_RVIZ=false

exec bash /aichallenge/run_evaluation.bash
