#!/usr/bin/env bash
# End-to-end check with the REAL image: prestage one local submission tar into a
# throw-away vault -> stage it -> launch autoware (RUN_MODE=awsim) -> confirm the
# participant nodes are up -> unstage. Prints the build wall time and the
# install.tar.zst size so the numbers in docs/spec/prestaged-submissions.md can be
# refreshed. Organiser machine only: needs docker, the aichallenge-2025-dev image
# and a valid .env in the repository root. See docs/spec/prestaged-submissions.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SUBMIT=""
KEEP_VAULT=""
TEAM="e2e-01"
WAIT_SEC=120

usage() {
    cat >&2 <<'EOF'
Usage: e2e_real_image.sh --submit <aichallenge_submit.tar.gz> [--keep-vault <cipherdir>] [--wait <sec>]

  --submit <tar>       local submission tar (e.g. submit/aichallenge_submit.tar.gz from
                       ./create_submit_file.bash). Stands in for the download step.
  --keep-vault <dir>   reuse/keep this cipherdir instead of a temporary one
  --wait <sec>         how long to wait for the participant nodes (default 120)

The reference aichallenge_submit/ under aichallenge/workspace/src is deleted and
rebuilt by prestage_all.sh; it is restored from git at the end.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
    --submit)
        SUBMIT="${2-}"
        shift 2
        ;;
    --keep-vault)
        KEEP_VAULT="${2-}"
        shift 2
        ;;
    --wait)
        WAIT_SEC="${2-}"
        shift 2
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

[ -n "${SUBMIT}" ] || {
    usage
    exit 2
}
[ -f "${SUBMIT}" ] || die "submission tar not found: ${SUBMIT}"
SUBMIT="$(cd "$(dirname "${SUBMIT}")" && pwd)/$(basename "${SUBMIT}")"
require_tools gocryptfs fusermount zstd python3 docker make

WORK="$(mktemp -d)"
# Set once stage_team.sh has succeeded, so cleanup() knows a team is actually
# staged (and unstage_team.sh has something to undo) even when a later step
# (die, a signal, or an unexpected failure) exits before the happy-path end.
STAGED=0
cleanup() {
    (cd "${REPO_ROOT}" && docker compose down --remove-orphans >/dev/null 2>&1) || true
    if [ "${STAGED}" -eq 1 ]; then
        "${SCRIPT_DIR}/unstage_team.sh" --yes || warn "unstage_team.sh failed during cleanup"
    fi
    # prestage_all.sh replaces the reference submission with the built tar; put it
    # back regardless of STAGED, since prestage can run (and fail) before staging.
    if git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "${REPO_ROOT}" checkout -- aichallenge/workspace/src/aichallenge_submit || true
        log "restored aichallenge/workspace/src/aichallenge_submit from git"
    fi
    umount_vault "${WORK}/mnt"
    rm -rf "${WORK}"
}
# Separate traps: a signal must EXIT (re-entering the EXIT trap once), not resume the script.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

VAULT="${KEEP_VAULT:-${WORK}/vault}"
mkdir -p "${VAULT}" "${WORK}/mnt"
printf 'e2e-passphrase\n' >"${WORK}/pw"
export PRESTAGE_PASSFILE="${WORK}/pw"
if [ ! -f "${VAULT}/gocryptfs.conf" ]; then
    gocryptfs -q -init -passfile "${WORK}/pw" "${VAULT}"
fi

# Download stub: prestage_all.sh calls "<cmd> --latest --dest-file <path> --user-id <id> ...";
# copy the local tar to <path> instead of talking to aic-next.
cat >"${WORK}/fake_download.sh" <<STUB
#!/usr/bin/env bash
set -eo pipefail
dest=""
while [ \$# -gt 0 ]; do
    case "\$1" in
    --dest-file) dest="\$2"; shift 2 ;;
    *) shift ;;
    esac
done
mkdir -p "\$(dirname "\${dest}")"
cp "${SUBMIT}" "\${dest}"
STUB
chmod +x "${WORK}/fake_download.sh"
export PRESTAGE_DOWNLOAD_CMD="${WORK}/fake_download.sh"
printf '%s\tlocal\t\tE2E local tar\n' "${TEAM}" >"${WORK}/teams.tsv"

cd "${REPO_ROOT}"
[ -f .env ] || die "no .env in ${REPO_ROOT} (run ./setup.bash env)"
if [ -e aichallenge/workspace/.staged_team ]; then
    die "a team is still staged ($(cat aichallenge/workspace/.staged_team)); run unstage_team.sh first"
fi

log "=== 1/4 prestage: real containerised build (SYMLINK_INSTALL=0) ==="
t0=$(date +%s)
"${SCRIPT_DIR}/prestage_all.sh" --vault "${VAULT}" --teams "${WORK}/teams.tsv" --force
t1=$(date +%s)
BUILD_SEC=$((t1 - t0))
log "download+build+archive wall time: ${BUILD_SEC} s"

mount_vault "${VAULT}" "${WORK}/mnt" ro
ARCHIVE_SIZE="$(du -h "${WORK}/mnt/team_${TEAM}/install.tar.zst" | cut -f1)"
log "install.tar.zst size: ${ARCHIVE_SIZE}"
umount_vault "${WORK}/mnt"

log "=== 2/4 stage ==="
t2=$(date +%s)
"${SCRIPT_DIR}/stage_team.sh" --vault "${VAULT}" "${TEAM}"
STAGED=1
t3=$(date +%s)
STAGE_SEC=$((t3 - t2))
log "stage wall time: ${STAGE_SEC} s"
INSTALL_SIZE="$(du -sh aichallenge/workspace/install | cut -f1)"
log "staged install/ size: ${INSTALL_SIZE}"

log "=== 3/4 launch autoware (make autoware-simulator) and wait for participant nodes ==="
make autoware-simulator
nodes=""
deadline=$(($(date +%s) + WAIT_SEC))
while [ "$(date +%s)" -lt "${deadline}" ]; do
    sleep 5
    nodes="$(CMD='ros2 node list' docker compose run -T --rm --no-deps autoware-command 2>/dev/null || true)"
    if printf '%s\n' "${nodes}" | grep -q '/imu_gnss_poser' &&
        printf '%s\n' "${nodes}" | grep -q '/simple_trajectory_generator'; then
        break
    fi
done
printf '%s\n' "${nodes}" | sed 's/^/    /' >&2
if ! printf '%s\n' "${nodes}" | grep -q '/simple_trajectory_generator'; then
    log "autoware.log tail:"
    tail -n 40 output/latest/*/autoware.log 2>/dev/null | sed 's/^/    /' >&2 || true
    die "participant nodes did not come up within ${WAIT_SEC} s"
fi
log "participant nodes are up ($(printf '%s\n' "${nodes}" | grep -c '^/') nodes)"
docker compose down --remove-orphans >/dev/null 2>&1 || true

# unstage and the reference-submission restore run exactly once, in cleanup()
# on exit — including on a die() below this point — so a failure never leaves
# the repo staged with the reference submission deleted.
log "=== 4/4 unstage (runs in cleanup on exit) ==="

log "E2E OK — build ${BUILD_SEC} s, archive ${ARCHIVE_SIZE}, stage ${STAGE_SEC} s, install/ ${INSTALL_SIZE}"
