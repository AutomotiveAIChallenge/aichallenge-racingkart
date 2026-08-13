#!/usr/bin/env bash
# Decrypt exactly one team's prebuilt install/ into the workspace, then unmount the vault.
# Runs on the VEHICLE PC, by organiser staff, while teams are logged out.
# See docs/spec/prestaged-submissions.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="${PRESTAGE_WORKSPACE_ROOT:-${REPO_ROOT}}"
WS="${WORKSPACE_ROOT}/aichallenge/workspace"
MANIFEST_PY="${SCRIPT_DIR}/manifest.py"

VAULT="${PRESTAGE_VAULT-}"
TEAM_ID=""

usage() {
    cat >&2 <<'EOF'
Usage: stage_team.sh [--vault <cipherdir>] <team_id>

Decrypts one team's prebuilt install/ into aichallenge/workspace/install and
unmounts the vault immediately. Run unstage_team.sh when the slot ends.

Environment:
  PRESTAGE_VAULT     default cipherdir if --vault is omitted
  PRESTAGE_PASSFILE  passphrase file. NOT recommended on the vehicle PC
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
    --vault)
        VAULT="${2-}"
        shift 2
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    -*)
        echo "invalid argument: '$1'" >&2
        usage
        exit 2
        ;;
    *)
        [ -z "${TEAM_ID}" ] || die "team_id given more than once"
        TEAM_ID="$1"
        shift
        ;;
    esac
done

[ -n "${TEAM_ID}" ] || {
    usage
    exit 2
}
[ -n "${VAULT}" ] || die "--vault (or PRESTAGE_VAULT) is required"
require_tools gocryptfs fusermount zstd tar python3 sha256sum

MARKER="${WS}/.staged_team"
if [ -e "${MARKER}" ]; then
    die "already staged: $(cat "${MARKER}") — run unstage_team.sh first"
fi
if [ -e "${WS}/install" ]; then
    die "workspace install/ already exists — run unstage_team.sh first"
fi

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

mount_vault "${VAULT}" "${MNT}" ro
MANIFEST="${MNT}/manifest.json"
[ -f "${MANIFEST}" ] || die "manifest.json not found in vault"

# The prebuilt install/ is only ABI-compatible with the image it was built in.
vault_image_id="$(python3 "${MANIFEST_PY}" image-id "${MANIFEST}")"
local_image_id="$(image_id)"
if [ "${vault_image_id}" != "${local_image_id}" ]; then
    die "image mismatch: vault was built in ${vault_image_id}, this host has ${local_image_id}. Rebuild the image or re-run prestage."
fi

status="$(python3 "${MANIFEST_PY}" get "${MANIFEST}" --team-id "${TEAM_ID}" --field build_status)" ||
    die "team not in manifest: ${TEAM_ID}"
[ "${status}" = "ok" ] ||
    die "team ${TEAM_ID} has build_status=${status}; rebuild from team_${TEAM_ID}/submission.tar.gz on this host"

archive="${MNT}/team_${TEAM_ID}/install.tar.zst"
[ -f "${archive}" ] || die "install archive missing: team_${TEAM_ID}/install.tar.zst"

expected_sha="$(python3 "${MANIFEST_PY}" get "${MANIFEST}" --team-id "${TEAM_ID}" --field install_sha256)"
actual_sha="$(sha256_of "${archive}")"
if [ -n "${expected_sha}" ] && [ "${expected_sha}" != "${actual_sha}" ]; then
    die "install archive sha256 mismatch for ${TEAM_ID} (expected ${expected_sha}, got ${actual_sha})"
fi

mkdir -p "${WS}/install"
tar --zstd -xf "${archive}" -C "${WS}/install"
[ -f "${WS}/install/setup.bash" ] || die "extracted install/ has no setup.bash"

printf '%s %s\n' "${TEAM_ID}" "$(date -Iseconds)" >"${MARKER}"
log "staged ${TEAM_ID} into ${WS}/install"
log "run 'unstage_team.sh' when the slot ends"
