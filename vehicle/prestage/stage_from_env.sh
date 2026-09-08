#!/usr/bin/env bash
# Stage the team named by TEAM_NAME in .env (make team-stage).
# Unstages a different team first; does nothing if TEAM_NAME is already staged.
# See vehicle/prestage/README.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="${PRESTAGE_WORKSPACE_ROOT:-${REPO_ROOT}}"
WS="${WORKSPACE_ROOT}/aichallenge/workspace"
ENV_FILE="${WORKSPACE_ROOT}/.env"
STAGE_CMD="${PRESTAGE_STAGE_CMD:-${SCRIPT_DIR}/stage_team.sh}"
UNSTAGE_CMD="${PRESTAGE_UNSTAGE_CMD:-${SCRIPT_DIR}/unstage_team.sh}"

[ -f "${ENV_FILE}" ] || die ".env not found: ${ENV_FILE}"

# Value of one KEY=value line (compose .env syntax), surrounding quotes stripped.
# Read by hand rather than `source`d so an unquoted value with spaces cannot
# run as a command and the other credentials in .env stay out of this process.
env_value() {
    local line
    line="$(grep -E "^$1=" "${ENV_FILE}" | tail -n1 || true)"
    line="${line#*=}"
    line="${line%$'\r'}"
    case "${line}" in
    \"*\") line="${line#\"}" line="${line%\"}" ;;
    \'*\') line="${line#\'}" line="${line%\'}" ;;
    esac
    printf '%s' "${line}"
}

TEAM_NAME="$(env_value TEAM_NAME)"
VAULT_DIR="$(env_value VAULT_DIR)"
PASS_PHRASE="$(env_value PASS_PHRASE)"
[ -n "${TEAM_NAME}" ] || die "TEAM_NAME is empty in ${ENV_FILE}"
[ -n "${VAULT_DIR}" ] || die "VAULT_DIR is empty in ${ENV_FILE}"

MARKER="${WS}/.staged_team"
if [ -f "${MARKER}" ]; then
    staged="$(cut -d' ' -f1 <"${MARKER}")"
    if [ "${staged}" = "${TEAM_NAME}" ]; then
        log "${TEAM_NAME} is already staged"
        exit 0
    fi
    log "unstaging ${staged} before staging ${TEAM_NAME}"
    "${UNSTAGE_CMD}" --yes
fi

if [ -n "${PASS_PHRASE}" ]; then
    # Hand the passphrase over a pipe (/dev/fd), not a file on disk.
    PRESTAGE_PASSFILE=<(printf '%s\n' "${PASS_PHRASE}") "${STAGE_CMD}" --vault "${VAULT_DIR}" "${TEAM_NAME}"
else
    # Empty PASS_PHRASE: stage_team.sh prompts for it (the venue-PC default).
    "${STAGE_CMD}" --vault "${VAULT_DIR}" "${TEAM_NAME}"
fi
