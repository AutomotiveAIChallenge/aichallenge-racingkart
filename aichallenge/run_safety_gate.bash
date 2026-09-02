#!/usr/bin/env bash
# Evaluation-environment entrypoint: safety gate, single vehicle (ROS_DOMAIN_ID 1).
# run_evaluation.bash sources the ROS overlays itself; this wrapper only fixes the mode.
# Contract: docs/interface/evaluation-interface.md §6

export SIM_MODE=safety-gate
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-1}"

exec bash /aichallenge/run_evaluation.bash
