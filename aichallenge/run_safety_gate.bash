#!/usr/bin/env bash
# Evaluation-environment entrypoint: safety gate (docs/interface/evaluation-interface.md §6).

export SIM_MODE=safety-gate
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-1}"

exec bash /aichallenge/run_evaluation.bash
