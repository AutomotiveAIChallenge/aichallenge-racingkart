#!/usr/bin/env bash
# Move the aichallenge-2025-dev image between PCs without rebuilding it.
# stage_team.sh refuses a vault whose image ID differs from the local image, and
# `docker_build.sh dev` on each PC yields a different ID, so build once and ship it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"
DOCKER_CMD="${PRESTAGE_DOCKER_CMD:-docker}"

usage() {
    cat >&2 <<'EOF'
Usage: image_transfer.sh export <image.tar.zst>   # on the PC that built the image
       image_transfer.sh import <image.tar.zst>   # on every other PC
Environment: PRESTAGE_IMAGE (default aichallenge-2025-dev)
EOF
}

verb="${1-}"
file="${2-}"
if [ -z "${verb}" ] || [ -z "${file}" ]; then
    usage
    exit 2
fi
require_tools zstd

case "${verb}" in
export)
    log "saving ${PRESTAGE_IMAGE} ($("${DOCKER_CMD}" image inspect --format '{{.Id}}' "${PRESTAGE_IMAGE}")) -> ${file}"
    "${DOCKER_CMD}" save "${PRESTAGE_IMAGE}" | zstd -T0 -q -f -o "${file}"
    log "wrote ${file} ($(du -h "${file}" | cut -f1))"
    ;;
import)
    [ -f "${file}" ] || die "file not found: ${file}"
    zstd -dc "${file}" | "${DOCKER_CMD}" load
    log "local ${PRESTAGE_IMAGE} is now $("${DOCKER_CMD}" image inspect --format '{{.Id}}' "${PRESTAGE_IMAGE}")"
    ;;
*)
    usage
    exit 2
    ;;
esac
