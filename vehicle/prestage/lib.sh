#!/usr/bin/env bash
# Shared helpers for the prestage scripts. Source this file; do not execute it.
# See docs/spec/prestaged-submissions.md

PRESTAGE_IMAGE="${PRESTAGE_IMAGE:-aichallenge-2025-dev}"

log() { printf '[prestage] %s\n' "$*" >&2; }
warn() { printf '[prestage] WARN: %s\n' "$*" >&2; }
die() {
    printf '[prestage] ERROR: %s\n' "$*" >&2
    exit 1
}

require_tools() {
    local tool
    for tool in "$@"; do
        command -v "${tool}" >/dev/null 2>&1 || die "required tool not found: ${tool}"
    done
}

sha256_of() {
    sha256sum "$1" | cut -d' ' -f1
}

# Resolve the local image ID. Locally built images have no RepoDigests, so .Id
# (the config digest) is the only stable identifier.
image_id() {
    if [ -n "${PRESTAGE_IMAGE_ID_CMD-}" ]; then
        eval "${PRESTAGE_IMAGE_ID_CMD}"
        return
    fi
    docker image inspect --format '{{.Id}}' "${PRESTAGE_IMAGE}" 2>/dev/null ||
        die "image not found: ${PRESTAGE_IMAGE} (run ./docker_build.sh dev)"
}

# mount_vault <cipherdir> <mountpoint> [rw|ro]
mount_vault() {
    local cipherdir="$1" mnt="$2" mode="${3:-rw}"
    local opts=(-q)
    [ -f "${cipherdir}/gocryptfs.conf" ] || die "not a gocryptfs vault: ${cipherdir}"
    [ -d "${mnt}" ] || die "mountpoint does not exist: ${mnt}"
    if [ "${mode}" = "ro" ]; then
        opts+=(-ro)
    fi
    if [ -n "${PRESTAGE_PASSFILE-}" ]; then
        warn "using PRESTAGE_PASSFILE: the passphrase is readable on this machine"
        opts+=(-passfile "${PRESTAGE_PASSFILE}")
    fi
    gocryptfs "${opts[@]}" "${cipherdir}" "${mnt}" || die "failed to mount vault: ${cipherdir}"
}

umount_vault() {
    local mnt="${1-}"
    [ -n "${mnt}" ] || return 0
    mountpoint -q "${mnt}" 2>/dev/null || return 0
    fusermount -u "${mnt}" || warn "failed to unmount ${mnt}"
}
