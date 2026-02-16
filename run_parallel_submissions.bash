#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_BASENAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_NAME="${SCRIPT_BASENAME%.*}"
COMPOSE_BASE_FILE="${REPO_ROOT}/docker-compose.yml"
COMPOSE_GPU_FILE="${REPO_ROOT}/docker-compose.gpu.yml"

log() { echo "[run_parallel_submissions] $*"; }
warn() { echo "[run_parallel_submissions][WARN] $*" >&2; }
die() {
    echo "[run_parallel_submissions][ERROR] $*" >&2
    exit 1
}

ts_compact() { date +%Y%m%d-%H%M%S; }

usage() {
    cat <<'EOF'
Usage:
  ./run_parallel_submissions.bash down [--log-dir <log_dir>]
  ./run_parallel_submissions.bash --submit <aichallenge_submit.tar.gz> [<aichallenge_submit.tar.gz> ...]
  DEVICE=<auto|gpu|cpu> ./run_parallel_submissions.bash --submit <aichallenge_submit.tar.gz> [<aichallenge_submit.tar.gz> ...]

Behavior:
  - Starts AWSIM once (docker compose service: simulator).
  - Waits for /admin/awsim/state via topic.
  - Builds 1 eval image per submit (Dockerfile target: eval).
  - Starts Autoware containers autoware-d1..autoware-dN.
  - Domain id is assigned by submit order: 1..4 (max 4).
  - Writes logs under output/<run_id>/d<domain_id>/autoware.log and output/latest -> <run_id>.
  - Writes this script log to output/<run_id>/<script_name>.log.

Env:
  DEVICE=auto|gpu|cpu    GPU selection (default: auto)
                         auto: enable GPU if /dev/nvidia0 exists
                         gpu : force GPU override (requires Docker-side NVIDIA support)
                         cpu : never use GPU override
EOF
}

gpu_enabled_from_device() {
    local device="${1:-auto}"
    case "${device}" in
    gpu) echo 1 ;;
    cpu) echo 0 ;;
    auto)
        # Only check the device node to avoid depending on NVML (`nvidia-smi`) and Docker daemon access.
        if [ -e /dev/nvidia0 ]; then echo 1; else echo 0; fi
        ;;
    *) die "invalid DEVICE: '${device}' (use auto|gpu|cpu)" ;;
    esac
}

ensure_output_dirs() {
    local run_id="$1"
    local vehicles="$2"
    mkdir -p "${REPO_ROOT}/output/_host"
    mkdir -p "${REPO_ROOT}/output/${run_id}"
    ln -nfs "${run_id}" "${REPO_ROOT}/output/latest"
    local i
    for ((i = 1; i <= vehicles; i++)); do
        mkdir -p "${REPO_ROOT}/output/${run_id}/d${i}"
    done
}

init_run_log() {
    local run_id="$1"
    local log_file="${REPO_ROOT}/output/${run_id}/${SCRIPT_NAME}.log"
    touch "${log_file}" || true
    exec > >(tee -a "${log_file}") 2>&1
    log "Log file: ${log_file}"
    log "Run id: ${run_id}"
}

require_submit_in_build_context() {
    local submit="$1"
    local submit_abs submit_rel
    submit_abs="$(realpath "${submit}")"
    case "${submit_abs}" in
    "${REPO_ROOT}"/*) ;;
    *) die "submit must be under repo root (docker build context): ${submit}" ;;
    esac
    submit_rel="${submit_abs#"${REPO_ROOT}"/}"
    echo "${submit_rel}"
}

build_eval_image() {
    local submit_rel="$1"
    local run_id="$2"
    local domain_id="$3"
    local tag
    tag="autoware-d${domain_id}"
    # IMPORTANT: This function is used via command substitution, so it must only emit the tag on stdout.
    # Send build logs to stderr to avoid corrupting the captured tag.
    log "Build image for d${domain_id}: ${tag} (SUBMIT_TAR=${submit_rel})" >&2
    docker build --progress=plain --target eval --build-arg "SUBMIT_TAR=${submit_rel}" -t "${tag}" "${REPO_ROOT}" 1>&2
    echo "${tag}"
}

main() {
    if [ "${1-}" = "down" ]; then
        local -a compose_args=(-f "${COMPOSE_BASE_FILE}")
        local gpu_enabled
        gpu_enabled="$(gpu_enabled_from_device "${DEVICE:-auto}")"
        if [ "${gpu_enabled}" = "1" ]; then
            compose_args+=(-f "${COMPOSE_GPU_FILE}")
        fi
        docker compose "${compose_args[@]}" down
        return 0
    fi

    local run_id="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
    local device="${DEVICE:-auto}"
    local -a submits=()

    while [ $# -gt 0 ]; do
        case "$1" in
        --submit | --submit-tar)
            shift
            [ $# -gt 0 ] || die "--submit requires at least one file path"
            while [ $# -gt 0 ]; do
                case "$1" in
                -h | --help | --*)
                    break
                    ;;
                *)
                    submits+=("$1")
                    shift
                    ;;
                esac
            done
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: '$1'"
            ;;
        esac
    done

    [ "${#submits[@]}" -gt 0 ] || die "At least one --submit is required"
    local vehicles="${#submits[@]}"
    if [ "${vehicles}" -lt 1 ] || [ "${vehicles}" -gt 4 ]; then die "--submit count must be in 1..4"; fi
    if [ -z "${run_id}" ]; then run_id="$(ts_compact)-${SCRIPT_NAME}-$$"; fi

    local gpu_enabled
    gpu_enabled="$(gpu_enabled_from_device "${device}")"

    mkdir -p "${REPO_ROOT}/output/${run_id}"
    init_run_log "${run_id}"

    log "Vehicles: ${vehicles}"
    log "Device: ${device}"
    log "GPU enabled: ${gpu_enabled}"

    ensure_output_dirs "${run_id}" "${vehicles}"

    local -a images=()
    local domain_id
    for ((domain_id = 1; domain_id <= vehicles; domain_id++)); do
        local submit="${submits[$((domain_id - 1))]}"
        [ -f "${submit}" ] || die "submit file not found: ${submit}"

        local submit_rel
        submit_rel="$(require_submit_in_build_context "${submit}")"
        # autoware-dxとして使うイメージ名を返す
        images+=("$(build_eval_image "${submit_rel}" "${run_id}" "${domain_id}")")
    done

    log "Starting simulator (once)"
    local sim_mode="eval"
    if [ "${vehicles}" -ge 2 ]; then
        sim_mode="${vehicles}p"
    fi
    log "Simulator mode: ${sim_mode}"
    local -a compose_args=(-f "${COMPOSE_BASE_FILE}")
    if [ "${gpu_enabled}" = "1" ]; then
        compose_args+=(-f "${COMPOSE_GPU_FILE}")
    fi
    OUTPUT_RUN_DIR="/output/${run_id}" SIM_MODE="${sim_mode}" docker compose "${compose_args[@]}" up -d --force-recreate simulator
    # Await for /admin/awsim/state
    CMD="env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-ready" docker compose "${compose_args[@]}" run --rm --no-deps autoware-command
    for domain_id in $(seq 1 "${vehicles}"); do
        local svc="autoware-d${domain_id}"
        local img="${images[$((domain_id - 1))]}"
        local out_dir="/output/${run_id}/d${domain_id}"

        log "  - ${svc} (image: ${img}, output: ${out_dir})"
        RUN_MODE="awsim" OUTPUT_RUN_DIR="${out_dir}" RUN_ID="${run_id}" docker compose "${compose_args[@]}" up -d --force-recreate "autoware-d${domain_id}"
    done
    # Wait until FinishALL
    CMD="env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-finish" docker compose "${compose_args[@]}" run --rm --no-deps autoware-command

    log "Started. Output: output/${run_id}/d*/autoware.log"
    log "Stop: ./run_parallel_submissions.bash down"
}

main "$@"
