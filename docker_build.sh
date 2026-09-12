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
# source tree has changed since, so an eval build does not silently test an old submission.
# AIC_STRICT_SUBMIT=1 turns the warning into an error.
DEFAULT_SUBMIT_TAR="submit/aichallenge_submit.tar.gz"
SUBMIT_SRC_DIR="aichallenge/workspace/src/aichallenge_submit"
if [ "$target" = "eval" ] && [ "${SUBMIT_TAR:-${DEFAULT_SUBMIT_TAR}}" = "${DEFAULT_SUBMIT_TAR}" ] &&
    [ -f "${DEFAULT_SUBMIT_TAR}" ] && [ -d "${SUBMIT_SRC_DIR}" ]; then
    newer_file="$(find "${SUBMIT_SRC_DIR}" -type f -newer "${DEFAULT_SUBMIT_TAR}" -not -path '*/__pycache__/*' -print -quit)"
    if [ -n "${newer_file}" ]; then
        echo "[WARN] ${DEFAULT_SUBMIT_TAR} is older than ${newer_file}" >&2
        echo "[WARN] The eval image will contain the submission as it was when the tarball was made." >&2
        echo "[WARN] Run ./create_submit_file.bash first to evaluate your current code." >&2
        if [ "${AIC_STRICT_SUBMIT:-0}" = "1" ]; then
            exit 1
        fi
    fi
fi

# Check whether a path relative to the repo root is excluded from the docker build context by
# .dockerignore. Handles the pattern shapes actually used in that file: a bare directory/path
# prefix (matches itself and everything under it), the same prefix with a trailing "/**", and a
# "**/<name>"-style pattern (matches any path component equal to <name>, or a glob against the
# basename when <name> itself contains a wildcard, e.g. "**/*.pyc").
path_excluded_by_dockerignore() {
    local path="$1" ignore_file="${repo_real}/.dockerignore"
    [ -f "${ignore_file}" ] || return 1
    local pat sub base
    while IFS= read -r pat; do
        pat="${pat%$'\r'}"
        # Trim leading/trailing whitespace.
        pat="${pat#"${pat%%[![:space:]]*}"}"
        pat="${pat%"${pat##*[![:space:]]}"}"
        [ -z "${pat}" ] && continue
        case "${pat}" in '#'*) continue ;; esac
        pat="${pat%/\*\*}"
        pat="${pat#./}"
        if [[ ${pat} == \*\*/* ]]; then
            sub="${pat#\*\*/}"
            if [[ ${sub} == *'*'* ]]; then
                base="${path##*/}"
                # shellcheck disable=SC2053 # intentional glob match: sub is a pattern like "*.pyc"
                [[ ${base} == ${sub} ]] && return 0
            else
                case "/${path}/" in */"${sub}"/*) return 0 ;; esac
            fi
        else
            [[ ${path} == "${pat}" || ${path} == "${pat}"/* ]] && return 0
        fi
    done <"${ignore_file}"
    return 1
}

BUILD_ARGS=()
if [ "$target" = "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    if [ ! -f "${SUBMIT_TAR}" ]; then
        echo "[ERROR] submit file not found: ${SUBMIT_TAR}" >&2
        exit 1
    fi
    # The Dockerfile COPYs SUBMIT_TAR from the build context (repo root minus .dockerignore).
    # Accept an absolute path inside the repo by making it relative; reject anything docker
    # cannot see, instead of failing with "not found" after the no-cache build has started.
    repo_real="$(realpath .)"
    submit_real="$(realpath "${SUBMIT_TAR}")"
    case "${submit_real}" in
    "${repo_real}"/*)
        submit_rel="${submit_real#"${repo_real}"/}"
        if path_excluded_by_dockerignore "${submit_rel}"; then
            echo "[ERROR] ${SUBMIT_TAR} is excluded from the build context by .dockerignore." >&2
            echo "        Copy it to submit/ and pass: --submit submit/$(basename "${SUBMIT_TAR}")" >&2
            exit 1
        fi
        SUBMIT_TAR="${submit_rel}"
        ;;
    *)
        echo "[ERROR] ${SUBMIT_TAR} is outside the repository, so docker build cannot COPY it." >&2
        echo "        Copy it to submit/ and pass: --submit submit/$(basename "${SUBMIT_TAR}")" >&2
        exit 1
        ;;
    esac
    BUILD_ARGS+=(--build-arg "SUBMIT_TAR=${SUBMIT_TAR}")
    echo "[INFO] Using submit tar: ${SUBMIT_TAR}"
elif [ "$target" != "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    echo "[WARN] --submit is only used for target=eval (ignored): ${SUBMIT_TAR}" >&2
elif [ "$target" = "eval" ] && [ ! -f "${DEFAULT_SUBMIT_TAR}" ]; then
    echo "[ERROR] ${DEFAULT_SUBMIT_TAR} not found. Run ./create_submit_file.bash first, or pass --submit <file>." >&2
    exit 1
fi

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" "${BUILD_ARGS[@]}" -t "aichallenge-2025-${target}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
