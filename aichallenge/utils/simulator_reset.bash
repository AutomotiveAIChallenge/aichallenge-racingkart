#!/usr/bin/env bash
set -euo pipefail

autoware_domain_id="${1:-1}"
sim_domain_id="${2:-0}"

echo "[simulator_reset] Resetting AWSIM (ROS_DOMAIN_ID=${sim_domain_id})"
env ROS_DOMAIN_ID="${sim_domain_id}" /aichallenge/utils/publish.bash reset-awsim || true

exec env ROS_DOMAIN_ID="${autoware_domain_id}"
