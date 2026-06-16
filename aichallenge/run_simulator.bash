#!/bin/bash

SCRIPT_DIR="$(dirname "$0")/simulator_scripts"
mode="${1:-${SIM_MODE:-simulator}}"
[ $# -gt 0 ] && shift

# dev2 / gate2 等は dev.sh / gate.sh に番号を渡すエイリアス
[[ ${mode} =~ ^(dev|gate)([0-9]+)$ ]] && set -- "${BASH_REMATCH[2]}" "$@" && mode="${BASH_REMATCH[1]}"

# simulator_scripts 内のスクリプトを呼び出す
script="${SCRIPT_DIR}/${mode}.sh"
if [[ ! -f ${script} ]]; then
    echo "[ERROR] unknown mode '${mode}' (supported: $(basename -s .sh "${SCRIPT_DIR}"/*.sh | xargs) dev<N> gate<N>)" >&2
    exit 1
fi

echo "[INFO] Starting AWSIM: ${mode}.sh $*"
exec bash "${script}" "$@"
