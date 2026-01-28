#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOG_EVENT_ID=""
LOG_DIR=""
LOG_FILE=""

log() {
    echo "[docker_build_run] $*"
}

warn() {
    echo "[docker_build_run][WARN] $*" >&2
}

die() {
    echo "[docker_build_run][ERROR] $*" >&2
    exit 1
}

ts_compact() {
    date +%Y%m%d-%H%M%S
}

init_host_log() {
    mkdir -p "${REPO_ROOT}/output/_host"
    LOG_EVENT_ID="$(ts_compact)-docker_build_run-$$"
    LOG_DIR="${REPO_ROOT}/output/_host/${LOG_EVENT_ID}"
    mkdir -p "${LOG_DIR}"
    ln -nfs "${LOG_EVENT_ID}" "${REPO_ROOT}/output/_host/latest-build-run"
    LOG_FILE="${LOG_DIR}/docker_build_run.log"
    touch "${LOG_FILE}" || true

    exec > >(tee -a "${LOG_FILE}") 2>&1

    log "Log dir: ${LOG_DIR}"
    log "Log file: ${LOG_FILE}"
}

cleanup_compose_best_effort() {
    set +e
    (cd "${REPO_ROOT}" && docker compose -f docker-compose.yml down --remove-orphans >/dev/null 2>&1) || true
    set -e
}

on_sigint() {
    warn "Interrupted (SIGINT). Running docker compose down..."
    cleanup_compose_best_effort
    exit 130
}

on_sigterm() {
    warn "Terminated (SIGTERM). Running docker compose down..."
    cleanup_compose_best_effort
    exit 143
}

usage() {
    cat <<'EOF'
Usage:
  ./docker_build_run.bash build <dev|eval> [--submit <path/to/aichallenge_submit.tar.gz>]
  ./docker_build_run.bash eval  [--device auto|gpu|cpu] [--domain-ids 1,2,3,4] [--rosbag] [--capture]
                               [--output-root /output] [--result-wait-seconds N]
  ./docker_build_run.bash all   [--submit <path/to/aichallenge_submit.tar.gz>]
                               [--device auto|gpu|cpu] [--domain-ids 1,2,3,4] [--rosbag] [--capture]
                               [--output-root /output] [--result-wait-seconds N]
  ./docker_build_run.bash down

Notes:
  - Logs are written to: output/_host/<event_id>/docker_build_run.log
  - Evaluation artifacts are written under: output/<timestamp>/d<domain_id>/
EOF
}

cmd_build() {
    local target="${1:-}"
    shift || true

    local submit_tar=""
    while [ $# -gt 0 ]; do
        case "$1" in
        --submit | --submit-tar)
            submit_tar="${2:-}"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            die "Unknown option for build: '$1'"
            ;;
        esac
    done

    if [ -z "${target}" ]; then
        die "build requires target: dev|eval"
    fi
    if [ "${target}" = "eval" ] && [ -n "${submit_tar}" ] && [ ! -f "${submit_tar}" ]; then
        die "submit file not found: ${submit_tar}"
    fi

    log "Build target: ${target}"
    if [ -n "${submit_tar}" ]; then
        log "Submit tar: ${submit_tar}"
    fi

    if [ -n "${submit_tar}" ]; then
        (cd "${REPO_ROOT}" && ./docker_build.sh "${target}" --submit "${submit_tar}")
    else
        (cd "${REPO_ROOT}" && ./docker_build.sh "${target}")
    fi
}

cmd_eval() {
    local device="auto"
    local domain_ids="1,2,3,4"
    local rosbag="false"
    local capture="false"
    local output_root="/output"
    local result_wait_seconds="10"

    while [ $# -gt 0 ]; do
        case "$1" in
        --device)
            device="${2:-}"
            shift 2
            ;;
        --domain-ids)
            domain_ids="${2:-}"
            shift 2
            ;;
        --rosbag)
            rosbag="true"
            shift
            ;;
        --capture)
            capture="true"
            shift
            ;;
        --output-root)
            output_root="${2:-}"
            shift 2
            ;;
        --result-wait-seconds)
            result_wait_seconds="${2:-}"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            die "Unknown option for eval: '$1'"
            ;;
        esac
    done

    log "Eval device: ${device}"
    log "Eval domain ids: ${domain_ids}"
    log "Eval rosbag: ${rosbag}"
    log "Eval capture: ${capture}"
    log "Eval output root: ${output_root}"
    log "Eval result wait seconds: ${result_wait_seconds}"

    (cd "${REPO_ROOT}" && make run-sim-eval \
        DEVICE="${device}" \
        DOMAIN_IDS="${domain_ids}" \
        ROSBAG="${rosbag}" \
        CAPTURE="${capture}" \
        OUTPUT_ROOT="${output_root}" \
        RESULT_WAIT_SECONDS="${result_wait_seconds}")
}

cmd_all() {
    local submit_tar=""
    local -a eval_args=()

    while [ $# -gt 0 ]; do
        case "$1" in
        --submit | --submit-tar)
            submit_tar="${2:-}"
            shift 2
            ;;
        *)
            eval_args+=("$1")
            shift
            ;;
        esac
    done

    cmd_build eval ${submit_tar:+--submit "${submit_tar}"}
    cmd_eval "${eval_args[@]}"
}

cmd_down() {
    log "docker compose down --remove-orphans"
    (cd "${REPO_ROOT}" && docker compose -f docker-compose.yml down --remove-orphans)
}

main() {
    init_host_log

    trap on_sigint INT
    trap on_sigterm TERM

    local subcmd="${1:-}"
    shift || true

    case "${subcmd}" in
    build)
        cmd_build "$@"
        ;;
    eval)
        cmd_eval "$@"
        ;;
    all)
        cmd_all "$@"
        ;;
    down)
        cmd_down "$@"
        ;;
    -h | --help | help | "")
        usage
        exit 0
        ;;
    *)
        die "Unknown subcommand: '${subcmd}'"
        ;;
    esac
}

main "$@"

