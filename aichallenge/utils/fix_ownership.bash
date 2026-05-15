#!/bin/bash

set -euo pipefail

HOST_UID="${1-}"
HOST_GID="${2-}"
OUTPUT_ROOT="${3:-/output}"
TARGET="${4-}"

log() {
    echo "[fix_ownership] $*"
}

warn() {
    echo "[fix_ownership][WARN] $*" >&2
}

re_number='^[0-9]+$'
is_number() {
    [[ ${1-} =~ $re_number ]]
}

if [ -z "${HOST_UID}" ] || [ -z "${HOST_GID}" ] || ! is_number "${HOST_UID}" || ! is_number "${HOST_GID}"; then
    log "HOST_UID/HOST_GID not provided as arguments. Skipping ownership change."
    exit 0
fi

if [ -z "${TARGET}" ]; then
    warn "TARGET not provided. Skipping ownership change."
    exit 0
fi

case "${TARGET}" in
"${OUTPUT_ROOT}"/*) ;;
*)
    warn "TARGET '${TARGET}' is not under OUTPUT_ROOT '${OUTPUT_ROOT}'. Skipping."
    exit 1
    ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    log "Running as non-root user ($(id -u)). Skipping chown."
    exit 0
fi

log "Running as root. Changing ownership of artifacts to ${HOST_UID}:${HOST_GID}..."
log "Target directory: ${TARGET}"

# Ensure the output root itself remains writable by the host user.
chown "${HOST_UID}:${HOST_GID}" "${OUTPUT_ROOT}" || true
chown -R "${HOST_UID}:${HOST_GID}" "${TARGET}" || true
chown -Rh "${HOST_UID}:${HOST_GID}" "${OUTPUT_ROOT}/latest" || true

log "Ownership change complete."
exit 0
