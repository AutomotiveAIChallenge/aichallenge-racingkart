#!/bin/bash

SCRIPT_DIR="$(dirname "$0")/simulator_scripts"
mode="${1:-${SIM_MODE:-eval}}"

script="${SCRIPT_DIR}/${mode}.sh"
if [[ ! -f ${script} ]]; then
    echo "[WARN] unknown mode '${mode}' -> fallback to simulator.sh"
    script="${SCRIPT_DIR}/simulator.sh"
fi

echo "[INFO] Starting AWSIM in '${mode}' mode"
exec bash "${script}"
