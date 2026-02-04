#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

dc_str="${DC:-docker compose -f docker-compose.yml}"
read -r -a dc_cmd <<<"${dc_str}"

cmd=(python3 /aichallenge/utils/test_autostart_orchestrator.py "$@")
CMD_STR="$(printf '%q ' "${cmd[@]}")"

"${dc_cmd[@]}" run --rm --no-deps \
    -e CMD_WORKDIR="/aichallenge" \
    -e CMD="${CMD_STR}" \
    autoware-command
