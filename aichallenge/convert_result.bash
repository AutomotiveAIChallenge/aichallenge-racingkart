#!/bin/bash

set -euo pipefail

domain_id="${1:-1}"
wait_seconds="${2:-10}"

re_number='^[0-9]+$'
if ! [[ ${domain_id} =~ ${re_number} ]]; then
    domain_id=1
fi
if ! [[ ${wait_seconds} =~ ${re_number} ]]; then
    wait_seconds=10
fi

input="d${domain_id}-result-details.json"
echo "[convert_result] Convert result (wait up to ${wait_seconds}s for ${input})"

for ((i = 0; i < wait_seconds; i++)); do
    [ -s "${input}" ] && break
    sleep 1
done

python3 /aichallenge/workspace/src/aichallenge_system/script/result-converter.py --input "${input}" || true
