#!/bin/bash

set -euo pipefail

target="${1-}"
shift || true

SUBMIT_TAR="${SUBMIT_TAR-}"

if [ -z "${target}" ]; then
    cat >&2 <<'EOF'
Usage: ./docker_build.sh <dev|eval|parallel> [options]

Commands:
  dev                       開発モード
  eval  --submit <tar.gz>   評価モード
  parallel <t1> [t2] [t3] [t4]  複数並列実行（D1-D4、最大4台）

Examples:
  ./docker_build.sh dev
  ./docker_build.sh eval --submit path/to/submit.tar.gz
  ./docker_build.sh parallel team_a.tar.gz team_b.tar.gz team_c.tar.gz team_d.tar.gz
EOF
    exit 2
fi

# Collect arguments depending on target
declare -a submits=()
if [ "${target}" = "parallel" ]; then
    # Remaining positional args are submission tarballs
    submits=("$@")
    if [ ${#submits[@]} -eq 0 ]; then
        echo "[ERROR] parallel requires at least one submission file" >&2
        exit 1
    fi
    if [ ${#submits[@]} -gt 4 ]; then
        echo "[ERROR] parallel supports maximum 4 submissions (got ${#submits[@]})" >&2
        exit 1
    fi
else
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
            exit 2
            ;;
        esac
    done
fi

case "${target}" in
"eval")
    opts="--no-cache"
    ;;
"dev")
    opts=""
    ;;
"parallel")
    opts="--no-cache"
    ;;
*)
    echo "invalid argument (use 'dev', 'eval', or 'parallel')"
    exit 1
    ;;
esac

ts="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="output/docker/${ts}-docker_build-$$.log"
mkdir -p output/docker output/latest
ln -sfn "${PWD}/${LOG_FILE}" output/latest/docker_build.log

BUILD_ARGS=()
if [ "$target" = "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    if [ ! -f "${SUBMIT_TAR}" ]; then
        echo "[ERROR] submit file not found: ${SUBMIT_TAR}" >&2
        exit 1
    fi
    BUILD_ARGS+=(--build-arg "SUBMIT_TAR=${SUBMIT_TAR}")
    echo "[INFO] Using submit tar: ${SUBMIT_TAR}"
elif [ "$target" = "parallel" ]; then
    # D1 uses the base SUBMIT_TAR arg from the eval stage
    d1_tar="${submits[0]}"
    if [ ! -f "${d1_tar}" ]; then
        echo "[ERROR] D1 submit file not found: ${d1_tar}" >&2
        exit 1
    fi
    BUILD_ARGS+=(--build-arg "SUBMIT_TAR=${d1_tar}")
    echo "[INFO] D1: ${d1_tar}"

    # D2-D4 use SUBMIT_TAR_D{N}
    for i in 2 3 4; do
        idx=$((i - 1))
        if [ $idx -lt ${#submits[@]} ]; then
            tar_file="${submits[$idx]}"
            if [ ! -f "${tar_file}" ]; then
                echo "[ERROR] D${i} submit file not found: ${tar_file}" >&2
                exit 1
            fi
            BUILD_ARGS+=(--build-arg "SUBMIT_TAR_D${i}=${tar_file}")
            echo "[INFO] D${i}: ${tar_file}"
        fi
    done
elif [ "$target" != "eval" ] && [ -n "${SUBMIT_TAR}" ]; then
    echo "[WARN] --submit is only used for target=eval (ignored): ${SUBMIT_TAR}" >&2
fi

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" "${BUILD_ARGS[@]}" -t "aichallenge-2025-${target}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
