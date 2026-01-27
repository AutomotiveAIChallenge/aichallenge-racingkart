#!/bin/bash

set -euo pipefail

HOST_UID="${1-}"
HOST_GID="${2-}"
OUTPUT_ROOT="${3:-/output}"
TS="${4-}"

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

if [ -z "${TS}" ]; then
    warn "timestamp not provided. Skipping ownership change."
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    log "Running as non-root user ($(id -u)). Skipping chown."
    exit 0
fi

target="${OUTPUT_ROOT}/${TS}"
log "Running as root. Changing ownership of artifacts to ${HOST_UID}:${HOST_GID}..."
log "Target directory: ${target}"

chown -R "${HOST_UID}:${HOST_GID}" "${target}" || true
chown -h "${HOST_UID}:${HOST_GID}" "${OUTPUT_ROOT}/latest" || true

log "Ownership change complete."
exit 0
