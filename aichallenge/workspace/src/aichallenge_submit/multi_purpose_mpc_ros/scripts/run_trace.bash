#!/bin/bash
# Record a speed trace for DURATION seconds. Run inside the autoware container.
# shellcheck disable=SC1091
source /autoware/install/setup.bash
source /aichallenge/workspace/install/setup.bash
source "$(ros2 pkg prefix multi_purpose_mpc_ros)/.venv/bin/activate"
exec timeout "${DURATION:-50}" python3 \
  /aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/scripts/trace_speed.py \
  --out "${OUT:-/output/trace.csv}" --ay "${AY:-10.0}" --kappa-smoothing "${KS:-1}"
