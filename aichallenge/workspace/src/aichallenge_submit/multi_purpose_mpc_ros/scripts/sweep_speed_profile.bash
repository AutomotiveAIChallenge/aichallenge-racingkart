#!/bin/bash
# Sweep the speed-profile limits offline and print the lap time each implies.
# Run inside the autoware container:
#   CMD=/aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/scripts/sweep_speed_profile.bash \
#     docker compose run --rm --no-deps autoware-command
# shellcheck disable=SC1091
source /autoware/install/setup.bash
source /aichallenge/workspace/install/setup.bash
source "$(ros2 pkg prefix multi_purpose_mpc_ros)/.venv/bin/activate"

SCRIPT=/aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/scripts/check_speed_profile.py
VMAX=${VMAX:-34.1}
AMAX=${AMAX:-3.0}
AMIN=${AMIN:--3.0}

for ks in ${KS_LIST:-0 1 2 4}; do
  for ay in ${AY_LIST:-8 10 12 14}; do
    out=$(python3 "$SCRIPT" --kappa-smoothing "$ks" --ay "$ay" \
          --v-max "$VMAX" --a-max "$AMAX" --a-min "$AMIN" 2>/dev/null)
    speed=$(echo "$out" | grep "^speed" | sed 's/.*: //')
    lap=$(echo "$out" | grep "^implied" | sed 's/.*: //')
    printf "ks=%-2s ay=%-4s | %-45s | %s\n" "$ks" "$ay" "$speed" "$lap"
  done
done
