#!/usr/bin/env bash
# Remove every trace of the staged team's submission after their slot ends.
# Runs on the VEHICLE PC. See docs/spec/prestaged-submissions.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="${PRESTAGE_WORKSPACE_ROOT:-${REPO_ROOT}}"
WS="${WORKSPACE_ROOT}/aichallenge/workspace"
OUTPUT_DIR="${WORKSPACE_ROOT}/output"
DOCKER_CMD="${PRESTAGE_DOCKER_CMD:-docker}"
EVAL_IMAGE="${PRESTAGE_EVAL_IMAGE:-aichallenge-2025-eval}"

KEEP_OUTPUT=""
ASSUME_YES=0

usage() {
    cat >&2 <<'EOF'
Usage: unstage_team.sh [--keep-output <dir>] [--yes]

Deletes the staged team's plaintext and build artefacts:
  aichallenge/workspace/{install,build,log}
  the eval image, and the staging marker.
On a git checkout, aichallenge/workspace/src/aichallenge_submit is restored
from git (git clean + checkout) instead of being deleted, so the tracked
reference submission survives unstage. Off a git checkout it is deleted.

Options:
  --keep-output <dir>  Move output/* here (per team_id); otherwise left in place
  --yes                Do not prompt for confirmation
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
    --keep-output)
        KEEP_OUTPUT="${2-}"
        shift 2
        ;;
    --yes)
        ASSUME_YES=1
        shift
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "invalid argument: '$1'" >&2
        usage
        exit 2
        ;;
    esac
done

MARKER="${WS}/.staged_team"
team_id="unknown"
if [ -f "${MARKER}" ]; then
    team_id="$(cut -d' ' -f1 <"${MARKER}")"
fi

if [ "${ASSUME_YES}" -eq 0 ]; then
    log "about to delete install/, build/, log/, src/aichallenge_submit under ${WS}"
    log "staged team: ${team_id}"
    read -r -p "proceed? [y/N] " reply
    case "${reply}" in
    y | Y) ;;
    *) die "aborted" ;;
    esac
fi

# Preserve run logs before wiping anything; they belong to the organiser.
if [ -n "${KEEP_OUTPUT}" ] && [ -d "${OUTPUT_DIR}" ]; then
    dest="${KEEP_OUTPUT}/${team_id}"
    mkdir -p "${dest}"
    # output/latest is a symlink into the run dirs; drop it rather than archive it.
    rm -f "${OUTPUT_DIR}/latest"
    # output/.gitignore is tracked and must stay; excluding it from both the
    # existence check and the move keeps a dotfile-only output/ from leaving a
    # literal, unmatched "*" for mv (which would abort the whole script under
    # set -e before any plaintext below is deleted).
    if find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 ! -name '.gitignore' -print -quit | grep -q .; then
        find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 ! -name '.gitignore' -exec mv -t "${dest}" {} +
        log "archived output to ${dest}"
    fi
fi

# aichallenge_submit is the tracked reference submission on a git checkout;
# restore it from git instead of deleting it, so make dev / make autoware-build
# keep working after unstage. Off a git checkout (e.g. a bare deployment) fall
# back to plain deletion.
IN_GIT_WORKTREE=0
if git -C "${WORKSPACE_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    IN_GIT_WORKTREE=1
fi

if [ "${IN_GIT_WORKTREE}" -eq 1 ]; then
    rm -rf "${WS}/install" "${WS}/build" "${WS}/log" || true
    git -C "${WORKSPACE_ROOT}" clean -fdxq -- aichallenge/workspace/src/aichallenge_submit || true
    git -C "${WORKSPACE_ROOT}" checkout -q -- aichallenge/workspace/src/aichallenge_submit || true
else
    rm -rf "${WS}/install" "${WS}/build" "${WS}/log" "${WS}/src/aichallenge_submit" || true
fi
rm -f "${MARKER}" || true

# A baked eval image contains the submission source; it must not survive the slot.
if "${DOCKER_CMD}" image inspect "${EVAL_IMAGE}" >/dev/null 2>&1; then
    log "removing image ${EVAL_IMAGE}"
    "${DOCKER_CMD}" image rm -f "${EVAL_IMAGE}" >/dev/null || warn "failed to remove ${EVAL_IMAGE}"
fi

leftovers=0
for path in "${WS}/install" "${WS}/build" "${WS}/log" "${MARKER}"; do
    if [ -e "${path}" ]; then
        warn "leftover: ${path}"
        leftovers=$((leftovers + 1))
    fi
done

if [ -e "${WS}/src/aichallenge_submit" ]; then
    if [ "${IN_GIT_WORKTREE}" -eq 1 ] &&
        [ -z "$(git -C "${WORKSPACE_ROOT}" status --porcelain -- aichallenge/workspace/src/aichallenge_submit)" ]; then
        : # restored to tracked content — not a leftover
    else
        warn "leftover: ${WS}/src/aichallenge_submit"
        leftovers=$((leftovers + 1))
    fi
fi

if [ "${leftovers}" -ne 0 ]; then
    die "${leftovers} leftover path(s) — clean up manually before the next slot"
fi

log "unstaged ${team_id}: workspace is clean"
