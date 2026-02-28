#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_BASENAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_NAME="${SCRIPT_BASENAME%.*}"
MAX_VEHICLES=4

log() { echo "[run_parallel_submissions] $*"; }
die() {
    echo "[run_parallel_submissions][ERROR] $*" >&2
    exit 1
}

ts_compact() { date +%Y%m%d-%H%M%S; }

usage() {
    cat <<'EOF'
Usage:
  ./run_parallel_submissions.bash --submit <tar1> [<tar2> ...]
  ./run_parallel_submissions.bash down

Current behavior (temporary):
  - 1 to 4 submit tar.gz files are supported.
  - Starts simulator.launch.xml first (AWSIM + awsim_state_manager, ROS_DOMAIN_ID=0).
  - Waits for admin-ready.
  - Starts autoware-d1..autoware-dN.
  - Waits for admin-finish and then stops.
EOF
}

ensure_output_dirs() {
    local run_id="$1"
    local vehicles="$2"
    local domain_id
    for ((domain_id = 1; domain_id <= vehicles; domain_id++)); do
        mkdir -p "${REPO_ROOT}/output/${run_id}/d${domain_id}"
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
    local domain_id="$2"
    local tag="autoware-d${domain_id}"
    log "Build image for d${domain_id}: ${tag} (SUBMIT_TAR=${submit_rel})"
    docker build --progress=plain --target eval --build-arg "SUBMIT_TAR=${submit_rel}" -t "${tag}" "${REPO_ROOT}"
}

main() {
    if [ "${1-}" = "down" ]; then
        docker compose down --remove-orphans
        return 0
    fi

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
            return 0
            ;;
        *)
            die "Unknown option: '$1'"
            ;;
        esac
    done

    local vehicles="${#submits[@]}"
    [ "${vehicles}" -ge 1 ] || die "at least one --submit file is required"
    [ "${vehicles}" -le "${MAX_VEHICLES}" ] || die "submit files must be <= ${MAX_VEHICLES}"

    run_id="$(ts_compact)"
    ensure_output_dirs "${run_id}" "${vehicles}"
    init_run_log "${run_id}"

    log "Mode: ${vehicles} vehicles"

    local domain_id submit_path submit_rel
    for ((domain_id = 1; domain_id <= vehicles; domain_id++)); do
        submit_path="${submits[$((domain_id - 1))]}"
        submit_rel="$(require_submit_in_build_context "${submit_path}")"
        build_eval_image "${submit_rel}" "${domain_id}"
    done

    log_dir="/output/${run_id}"
    log "Starting simulator.launch.xml (AWSIM + awsim_state_manager)"
    CMD="env ROS_DOMAIN_ID=0 ros2 launch aichallenge_system_launch simulator.launch.xml log_dir:=${log_dir} vehicles:=${vehicles} laps:=6 timeout:=600 >\"${log_dir}/awsim.log\" 2>&1"
    LOG_DIR="${log_dir}" \
        CMD_WORKDIR="${log_dir}" \
        CMD="${CMD}" \
        docker compose up -d --force-recreate autoware-command

    for ((domain_id = 1; domain_id <= vehicles; domain_id++)); do
        log_dir="/output/${run_id}/d${domain_id}"
        log "Starting autoware-d${domain_id}"
        LOG_DIR="${log_dir}" \
            RUN_MODE="awsim" \
            docker compose up -d --force-recreate "autoware-d${domain_id}"
    done

    log "Started. Output: output/${run_id}/d1..d${vehicles}"
}

main "$@"
