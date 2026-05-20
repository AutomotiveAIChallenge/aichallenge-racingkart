#!/bin/bash

set -euo pipefail

target="${1-}"
shift || true

SUBMIT_TAR="${SUBMIT_TAR-}"

if [ -z "${target}" ]; then
    cat >&2 <<'EOF'
Usage: ./docker_build.sh <dev|eval|parallel> [options]

Commands:
  dev       開発モード（AWSIM + Autoware D1）
  eval      評価モード（Autoware D1）
  parallel  複数並列実行（D1-D3、3台）

Options:
  --submit <path> [...]  提出物 tar.gz ファイル（eval: 1つ, parallel: 3つ）

Examples:
  ./docker_build.sh dev
  ./docker_build.sh eval --submit path/to/submit.tar.gz
  ./docker_build.sh parallel --submit a.tar.gz b.tar.gz c.tar.gz
EOF
    exit 2
fi

submits=()
while [ $# -gt 0 ]; do
    case "$1" in
    --submit)
        shift
        while [ $# -gt 0 ] && [[ $1 != --* ]]; do
            submits+=("$1")
            shift
        done
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

if [ "${target}" = "eval" ]; then
    SUBMIT_TAR="${submits[0]-${SUBMIT_TAR}}"
elif [ "${target}" = "parallel" ]; then
    [ ${#submits[@]} -eq 3 ] || {
        echo "[ERROR] parallel requires exactly 3 submission files" >&2
        exit 1
    }
fi

case "${target}" in
"eval" | "parallel")
    opts=""
    ;;
"dev")
    opts=""
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
    # parallel: D1 は SUBMIT_TAR（eval ステージ）、D2-D3 は SUBMIT_TAR_D{N}
    for i in 1 2 3; do
        tar_file="${submits[$((i - 1))]}"
        if [ ! -f "${tar_file}" ]; then
            echo "[ERROR] submit file not found: ${tar_file}" >&2
            exit 1
        fi
        if [ "$i" -eq 1 ]; then
            BUILD_ARGS+=(--build-arg "SUBMIT_TAR=${tar_file}")
        else
            BUILD_ARGS+=(--build-arg "SUBMIT_TAR_D${i}=${tar_file}")
        fi
        echo "[INFO] D${i}: ${tar_file}"
    done
fi

# shellcheck disable=SC2086
docker build ${opts} --progress=plain --target "${target}" "${BUILD_ARGS[@]}" -t "aichallenge-2025-${target}" . 2>&1 | tee "$LOG_FILE"
echo "========================================================"
echo "This log is in : ${LOG_FILE}"
echo "========================================================"
