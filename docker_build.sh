#!/bin/bash

set -euo pipefail

target="${1-}"
shift || true

SUBMIT_TAR="${SUBMIT_TAR-}"

if [ -z "${target}" ]; then
    echo "Usage: ./docker_build.sh <dev|eval> [--submit <path/to/aichallenge_submit.tar.gz>]" >&2
    exit 2
fi

while [ $# -gt 0 ]; do
    case "$1" in
    --submit | --submit-tar)
        SUBMIT_TAR="${2-}"
        shift 2
        ;;
    --)
        shift
        break
        ;;
    *)
        echo "invalid argument: '$1'" >&2
        echo "Usage: ./docker_build.sh <dev|eval> [--submit <path/to/aichallenge_submit.tar.gz>]" >&2
        exit 2
        ;;
    esac
done

case "${target}" in
"eval")
    opts="--no-cache"
    ;;
"dev")
    opts=""
    ;;
*)
    echo "invalid argument (use 'dev' or 'eval')"
    exit 1
    ;;
esac

ts="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="output/docker/${ts}-docker_build-$$.log"
mkdir -p output/docker output/latest
ln -sfn "${PWD}/${LOG_FILE}" output/latest/docker_build.log

# The default tarball is only as fresh as the last ./create_submit_file.bash run. Warn when the
# source tree has changed since (a newer file, or a file that was packed but has since been
# deleted), so an eval build does not silently test an old submission. Comparing file lists also
# catches a plain deletion, which leaves no newer file behind for the mtime check above to find.
# AIC_STRICT_SUBMIT=1 turns the warning into an error.
DEFAULT_SUBMIT_TAR="submit/aichallenge_submit.tar.gz"
SUBMIT_SRC_DIR="aichallenge/workspace/src/aichallenge_submit"
if [ "$target" = "eval" ] && [ "${SUBMIT_TAR:-${DEFAULT_SUBMIT_TAR}}" = "${DEFAULT_SUBMIT_TAR}" ] &&
    [ -f "${DEFAULT_SUBMIT_TAR}" ] && [ -d "${SUBMIT_SRC_DIR}" ]; then
    newer_file="$(find "${SUBMIT_SRC_DIR}" -type f -newer "${DEFAULT_SUBMIT_TAR}" -not -path '*/__pycache__/*' -print -quit)"

    # Files the tarball has but the current tree no longer does (deletions since packing).
    tar_files="$(tar -tzf "${DEFAULT_SUBMIT_TAR}" 2>/dev/null | grep -v '/$' | sort -u || true)"
    src_files="$(
        cd "$(dirname "${SUBMIT_SRC_DIR}")" || exit 1
        find "$(basename "${SUBMIT_SRC_DIR}")" -type f -not -path '*/__pycache__/*' -not -name '*.pyc' | sort -u
    )"
    deleted_file="$(comm -23 <(printf '%s\n' "${tar_files}") <(printf '%s\n' "${src_files}") | head -1)"

    if [ -n "${newer_file}" ] || [ -n "${deleted_file}" ]; then
        if [ -n "${newer_file}" ]; then
            echo "[WARN] ${DEFAULT_SUBMIT_TAR} is older than ${newer_file}" >&2
        fi
        if [ -n "${deleted_file}" ]; then
            echo "[WARN] ${DEFAULT_SUBMIT_TAR} still contains ${deleted_file}, which no longer exists under ${SUBMIT_SRC_DIR}" >&2
        fi
        echo "[WARN] The eval image will contain the submission as it was when the tarball was made." >&2
        echo "[WARN] Run ./create_submit_file.bash first to evaluate your current code." >&2
        if [ "${AIC_STRICT_SUBMIT:-0}" = "1" ]; then
            exit 1
        fi
    fi
fi

BUILD_ARGS=()
if [ "$target" = "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    if [ ! -f "${SUBMIT_TAR}" ]; then
        echo "[ERROR] submit file not found: ${SUBMIT_TAR}" >&2
        exit 1
    fi
    BUILD_ARGS+=(--build-arg "SUBMIT_TAR=${SUBMIT_TAR}")
    echo "[INFO] Using submit tar: ${SUBMIT_TAR}"
elif [ "$target" != "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    echo "[WARN] --submit is only used for target=eval (ignored): ${SUBMIT_TAR}" >&2
fi

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" "${BUILD_ARGS[@]}" -t "aichallenge-2025-${target}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
