#!/usr/bin/env bash
# Build every team's submission ahead of time and store the results in a gocryptfs vault.
# Runs on the ORGANISER's machine (needs network + docker), not on the vehicle PC.
# See docs/spec/prestaged-submissions.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="${PRESTAGE_WORKSPACE_ROOT:-${REPO_ROOT}}"
WS="${WORKSPACE_ROOT}/aichallenge/workspace"
MANIFEST_PY="${SCRIPT_DIR}/manifest.py"

VAULT=""
TEAMS_FILE=""
ONLY_TEAM=""
FORCE=0

usage() {
    cat >&2 <<'EOF'
Usage: prestage_all.sh --vault <cipherdir> --teams <teams.tsv> [options]

Options:
  --vault <dir>     gocryptfs cipherdir (must already be initialised with -init)
  --teams <file>    TSV: team_id, user_id, [submission_id]. See teams.tsv.example
  --team <id>       Process only this team_id
  --force           Rebuild even if the team is already in the vault
  -h, --help        Show this help

Environment:
  PRESTAGE_USERNAME / PRESTAGE_PASSWORD   aic-next credentials (prompted once if unset)
  PRESTAGE_PASSFILE                       vault passphrase file (allowed here; organiser machine)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
    --vault)
        VAULT="${2-}"
        shift 2
        ;;
    --teams)
        TEAMS_FILE="${2-}"
        shift 2
        ;;
    --team)
        ONLY_TEAM="${2-}"
        shift 2
        ;;
    --force)
        FORCE=1
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

[ -n "${VAULT}" ] || die "--vault is required"
[ -n "${TEAMS_FILE}" ] || die "--teams is required"
[ -f "${TEAMS_FILE}" ] || die "teams file not found: ${TEAMS_FILE}"
require_tools gocryptfs fusermount zstd tar python3 sha256sum

DOWNLOAD_CMD="${PRESTAGE_DOWNLOAD_CMD:-python3 ${REPO_ROOT}/vehicle/download_submission.py}"
BUILD_CMD="${PRESTAGE_BUILD_CMD:-docker compose run --rm --no-deps -e SYMLINK_INSTALL=0 autoware-build}"

MNT="$(mktemp -d)"
cleanup() {
    umount_vault "${MNT}"
    rmdir "${MNT}" 2>/dev/null || true
}
# Signals must EXIT, not just run the handler: bash resumes the script after a
# handler returns, which would leave the loop writing plaintext into the
# unmounted mountpoint. The explicit exit re-enters the EXIT trap, so the
# unmount happens exactly once.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Credentials: ask once, up front, so the loop is unattended.
if [ -z "${PRESTAGE_DOWNLOAD_CMD-}" ]; then
    if [ -z "${PRESTAGE_USERNAME-}" ]; then
        read -r -p "aic-next username: " PRESTAGE_USERNAME
    fi
    if [ -z "${PRESTAGE_PASSWORD-}" ]; then
        read -r -s -p "aic-next password: " PRESTAGE_PASSWORD
        echo >&2
    fi
fi

mount_vault "${VAULT}" "${MNT}" rw
MANIFEST="${MNT}/manifest.json"

IMAGE_ID="$(image_id)"
log "image ${PRESTAGE_IMAGE} -> ${IMAGE_ID}"
python3 "${MANIFEST_PY}" init "${MANIFEST}" --image-id "${IMAGE_ID}" --image-tag "${PRESTAGE_IMAGE}"

clean_workspace() {
    rm -rf "${WS}/src/aichallenge_submit" "${WS}/install" "${WS}/build" "${WS}/log"
}

ok_count=0
fail_count=0
skip_count=0

while IFS=$'\t' read -r team_id user_id submission_id || [ -n "${team_id}" ]; do
    case "${team_id}" in
    "" | \#*) continue ;;
    esac
    if [ -n "${ONLY_TEAM}" ] && [ "${team_id}" != "${ONLY_TEAM}" ]; then
        continue
    fi
    case "${team_id}" in
    *[!A-Za-z0-9_-]*) die "invalid team_id (allowed: A-Za-z0-9_-): ${team_id}" ;;
    esac

    team_dir="${MNT}/team_${team_id}"
    if [ "${FORCE}" -eq 0 ] && [ -f "${team_dir}/install.tar.zst" ] &&
        [ "$(python3 "${MANIFEST_PY}" get "${MANIFEST}" --team-id "${team_id}" --field build_status 2>/dev/null || echo)" = "ok" ]; then
        log "${team_id}: already in vault, skipping (use --force to rebuild)"
        skip_count=$((skip_count + 1))
        continue
    fi

    log "=== ${team_id} (user ${user_id}) ==="
    mkdir -p "${team_dir}"
    clean_workspace

    tarball="${team_dir}/submission.tar.gz"
    dl_args=(--latest --dest-file "${tarball}" --user-id "${user_id}")
    if [ -n "${submission_id-}" ]; then
        dl_args=(--submission-id "${submission_id}" --dest-file "${tarball}" --user-id "${user_id}")
    fi
    if [ -z "${PRESTAGE_DOWNLOAD_CMD-}" ]; then
        dl_args+=(--username "${PRESTAGE_USERNAME}" --password "${PRESTAGE_PASSWORD}")
    fi

    if ! ${DOWNLOAD_CMD} "${dl_args[@]}" >>"${team_dir}/build.log" 2>&1; then
        warn "${team_id}: download failed (see ${team_dir}/build.log)"
        python3 "${MANIFEST_PY}" upsert "${MANIFEST}" --team-id "${team_id}" --user-id "${user_id}" \
            --submission-id "${submission_id-}" --build-status failed ||
            warn "${team_id}: manifest update failed"
        fail_count=$((fail_count + 1))
        continue
    fi

    mkdir -p "${WS}/src"
    # A corrupt tarball must fail this team only, not abort the remaining teams.
    if ! tar zxf "${tarball}" -C "${WS}/src" >>"${team_dir}/build.log" 2>&1; then
        warn "${team_id}: submission tarball is not readable (see ${team_dir}/build.log)"
        python3 "${MANIFEST_PY}" upsert "${MANIFEST}" --team-id "${team_id}" --user-id "${user_id}" \
            --submission-id "${submission_id-}" --build-status failed \
            --submission-sha256 "$(sha256_of "${tarball}")" ||
            warn "${team_id}: manifest update failed"
        clean_workspace
        fail_count=$((fail_count + 1))
        continue
    fi

    if ! (cd "${WORKSPACE_ROOT}" && eval "${BUILD_CMD}") >>"${team_dir}/build.log" 2>&1; then
        warn "${team_id}: build failed (see ${team_dir}/build.log)"
        python3 "${MANIFEST_PY}" upsert "${MANIFEST}" --team-id "${team_id}" --user-id "${user_id}" \
            --submission-id "${submission_id-}" --build-status failed \
            --submission-sha256 "$(sha256_of "${tarball}")" ||
            warn "${team_id}: manifest update failed"
        clean_workspace
        fail_count=$((fail_count + 1))
        continue
    fi

    # A build that exits 0 but produces no install/ must fail this team only,
    # not abort the remaining teams.
    if [ ! -f "${WS}/install/setup.bash" ]; then
        warn "${team_id}: build produced no install/setup.bash (see ${team_dir}/build.log)"
        python3 "${MANIFEST_PY}" upsert "${MANIFEST}" --team-id "${team_id}" --user-id "${user_id}" \
            --submission-id "${submission_id-}" --build-status failed \
            --submission-sha256 "$(sha256_of "${tarball}")" ||
            warn "${team_id}: manifest update failed"
        clean_workspace
        fail_count=$((fail_count + 1))
        continue
    fi

    # Archive the CONTENTS of install/ so stage_team.sh can extract straight into it.
    # A vault-full or I/O error here must fail this team only, not abort the remaining teams.
    if ! tar --zstd -cf "${team_dir}/install.tar.zst" -C "${WS}/install" . >>"${team_dir}/build.log" 2>&1; then
        warn "${team_id}: archiving install/ failed (see ${team_dir}/build.log)"
        python3 "${MANIFEST_PY}" upsert "${MANIFEST}" --team-id "${team_id}" --user-id "${user_id}" \
            --submission-id "${submission_id-}" --build-status failed \
            --submission-sha256 "$(sha256_of "${tarball}")" ||
            warn "${team_id}: manifest update failed"
        clean_workspace
        fail_count=$((fail_count + 1))
        continue
    fi
    python3 "${MANIFEST_PY}" upsert "${MANIFEST}" --team-id "${team_id}" --user-id "${user_id}" \
        --submission-id "${submission_id-}" --build-status ok \
        --install-sha256 "$(sha256_of "${team_dir}/install.tar.zst")" \
        --submission-sha256 "$(sha256_of "${tarball}")" ||
        warn "${team_id}: manifest update failed"
    log "${team_id}: ok ($(du -h "${team_dir}/install.tar.zst" | cut -f1))"
    ok_count=$((ok_count + 1))
    clean_workspace
done <"${TEAMS_FILE}"

clean_workspace

log "--- summary: ${ok_count} ok, ${fail_count} failed, ${skip_count} skipped ---"
python3 "${MANIFEST_PY}" summary "${MANIFEST}" >&2
[ "${fail_count}" -eq 0 ]
