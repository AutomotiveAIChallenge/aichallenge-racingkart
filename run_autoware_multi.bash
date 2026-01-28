#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_BASE_FILE="${REPO_ROOT}/docker-compose.yml"
HOST_LOG_DIR=""
HOST_LOG_FILE=""

log() { echo "[run_autoware_multi] $*"; }
warn() { echo "[run_autoware_multi][WARN] $*" >&2; }
die() { echo "[run_autoware_multi][ERROR] $*" >&2; exit 1; }

ts_compact() { date +%Y%m%d-%H%M%S; }

usage() {
    cat <<'EOF'
Usage:
  ./run_autoware_multi.bash down [--log-dir <output/_host/...>]
  ./run_autoware_multi.bash --submit <aichallenge_submit.tar.gz> [--submit <...> ...]
                            [--vehicles N] [--device auto|gpu|cpu] [--run-id ID]

Behavior:
  - Starts AWSIM once (docker compose service: simulator).
  - Builds 1 eval image per submit (Dockerfile target: eval).
  - Starts Autoware containers autoware-d1..autoware-dN concurrently.
  - Domain id is assigned by submit order: 1..4 (max 4).
  - Writes logs under output/<run_id>/d<domain_id>/autoware.log and output/latest -> <run_id>.
  - Writes compose override to output/_host/<event_id>/compose.autoware_multi.yml and
    output/_host/latest-autoware-multi -> <event_id>.
EOF
}

is_number() {
    local s="${1:-}"
    [[ -n "${s}" && "${s}" =~ ^[0-9]+$ ]]
}

gpu_enabled_from_device() {
    local device="${1:-auto}"
    case "${device}" in
    gpu) echo 1 ;;
    cpu) echo 0 ;;
    auto)
        if command -v nvidia-smi >/dev/null 2>&1 && [ -e /dev/nvidia0 ]; then echo 1; else echo 0; fi
        ;;
    *) die "invalid --device: '${device}' (use auto|gpu|cpu)" ;;
    esac
}

sanitize_tag_fragment() {
    local s="${1:-}"
    s="${s##*/}"
    s="${s%.tar.gz}"
    s="${s%.tgz}"
    s="${s%.tar}"
    s="${s%.gz}"
    s="$(echo "${s}" | tr -cs 'A-Za-z0-9._-' '_' | sed -E 's/^_+//; s/_+$//')"
    echo "${s:-submit}"
}

ensure_output_dirs() {
    local run_id="$1"
    local vehicles="$2"

    mkdir -p "${REPO_ROOT}/output/_host"

    if [ -e "${REPO_ROOT}/output/latest" ] && [ ! -L "${REPO_ROOT}/output/latest" ]; then
        local legacy="${REPO_ROOT}/output/_host/legacy-output-latest-${run_id}-$$"
        warn "Moving legacy output/latest to ${legacy}"
        mv "${REPO_ROOT}/output/latest" "${legacy}"
    fi

    mkdir -p "${REPO_ROOT}/output/${run_id}"
    ln -nfs "${run_id}" "${REPO_ROOT}/output/latest"

    local i
    for i in $(seq 1 "${vehicles}"); do
        mkdir -p "${REPO_ROOT}/output/${run_id}/d${i}"
    done
}

init_host_log() {
    local run_id="$1"

    mkdir -p "${REPO_ROOT}/output/_host"

    local event_id
    event_id="$(ts_compact)-run_autoware_multi-$$"

    HOST_LOG_DIR="${REPO_ROOT}/output/_host/${event_id}"
    mkdir -p "${HOST_LOG_DIR}"

    ln -nfs "${event_id}" "${REPO_ROOT}/output/_host/latest-autoware-multi"

    HOST_LOG_FILE="${HOST_LOG_DIR}/run_autoware_multi.log"
    touch "${HOST_LOG_FILE}" || true

    exec > >(tee -a "${HOST_LOG_FILE}") 2>&1

    log "Log dir: ${HOST_LOG_DIR}"
    log "Log file: ${HOST_LOG_FILE}"
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
    submit_rel="${submit_abs#${REPO_ROOT}/}"
    echo "${submit_rel}"
}

build_eval_image() {
    local submit_rel="$1"
    local run_id="$2"
    local domain_id="$3"

    local tag
    tag="aichallenge-2025-eval-$(sanitize_tag_fragment "${submit_rel}")-${run_id}-d${domain_id}"
    # IMPORTANT: This function is used via command substitution, so it must only emit the tag on stdout.
    # Send build logs to stderr to avoid corrupting the captured tag.
    log "Build image for d${domain_id}: ${tag} (SUBMIT_TAR=${submit_rel})" >&2
    docker build --progress=plain --target eval --build-arg "SUBMIT_TAR=${submit_rel}" -t "${tag}" "${REPO_ROOT}" 1>&2
    echo "${tag}"
}

write_compose_override() {
    local out_file="$1"
    local run_id="$2"
    local vehicles="$3"
    local gpu_enabled="$4"
    shift 4
    local -a images=("$@")

    {
        echo "services:"
        local i
        for i in $(seq 1 "${vehicles}"); do
            local img="${images[$((i - 1))]}"
            cat <<EOF
  autoware-d${i}:
    image: "${img}"
    privileged: true
    pull_policy: never
    network_mode: host
EOF
            if [ "${gpu_enabled}" = "1" ]; then
                echo "    runtime: nvidia"
            fi
            cat <<EOF
    environment:
      - DISPLAY=\${DISPLAY}
      - USER=\${USER}
      - ROS_DISTRO=humble
      - XAUTHORITY=\${XAUTHORITY}
      - QT_X11_NO_MITSHM=1
      - TZ=Asia/Tokyo
      - RUN_MODE=awsim
      - DOMAIN_ID=${i}
      - RUN_ID=${run_id}
    volumes:
      - ./output:/output
      - /tmp/.X11-unix:/tmp/.X11-unix:rw
      - /dev/dri:/dev/dri
      - \${XAUTHORITY}:\${XAUTHORITY}:rw
    devices:
      - /dev/dri
      - /dev/input
    working_dir: /output/${run_id}/d${i}
    command: ["bash", "-lc", "exec /aichallenge/run_autoware.bash awsim ${i} >autoware.log 2>&1"]

EOF
        done
    } >"${out_file}"
}

compose_up() {
    local gpu_enabled="$1"
    shift
    if [ "${gpu_enabled}" = "1" ]; then
        NVIDIA_VISIBLE_DEVICES="all" NVIDIA_DRIVER_CAPABILITIES="all" docker compose "$@"
    else
        docker compose "$@"
    fi
}

cmd_down() {
    local log_dir="${REPO_ROOT}/output/_host/latest-autoware-multi"

    while [ $# -gt 0 ]; do
        case "$1" in
        --log-dir)
            log_dir="${2:-}"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option for down: '$1'"
            ;;
        esac
    done

    local override_file="${log_dir}/compose.autoware_multi.yml"
    [ -f "${override_file}" ] || die "compose override not found: ${override_file} (hint: --log-dir output/_host/<event_id>)"

    log "docker compose down --remove-orphans (override: ${override_file})"
    docker compose -f "${COMPOSE_BASE_FILE}" -f "${override_file}" down --remove-orphans
}

main() {
    if [ "${1:-}" = "down" ]; then
        shift
        cmd_down "$@"
        return 0
    fi

    local device="auto"
    local run_id=""
    local vehicles=""
    local -a submits=()

    while [ $# -gt 0 ]; do
        case "$1" in
        --submit | --submit-tar)
            submits+=("${2:-}")
            shift 2
            ;;
        --vehicles)
            vehicles="${2:-}"
            shift 2
            ;;
        --device)
            device="${2:-}"
            shift 2
            ;;
        --run-id)
            run_id="${2:-}"
            shift 2
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
    if [ -z "${vehicles}" ]; then vehicles="${#submits[@]}"; fi
    is_number "${vehicles}" || die "--vehicles must be a number (1..4)"
    if [ "${vehicles}" -lt 1 ] || [ "${vehicles}" -gt 4 ]; then die "--vehicles must be in 1..4"; fi
    if [ "${#submits[@]}" -ne "${vehicles}" ]; then
        die "--vehicles (${vehicles}) must match --submit count (${#submits[@]})"
    fi
    if [ -z "${run_id}" ]; then run_id="$(ts_compact)-autoware-multi-$$"; fi

    local gpu_enabled
    gpu_enabled="$(gpu_enabled_from_device "${device}")"

    init_host_log "${run_id}"

    log "Vehicles: ${vehicles}"
    log "Device: ${device} (gpu_enabled=${gpu_enabled})"

    ensure_output_dirs "${run_id}" "${vehicles}"

    local -a images=()
    local domain_id
    for domain_id in $(seq 1 "${vehicles}"); do
        local submit="${submits[$((domain_id - 1))]}"
        [ -f "${submit}" ] || die "submit file not found: ${submit}"

        local submit_rel
        submit_rel="$(require_submit_in_build_context "${submit}")"

        images+=("$(build_eval_image "${submit_rel}" "${run_id}" "${domain_id}")")
    done

    local override_file="${HOST_LOG_DIR}/compose.autoware_multi.yml"
    write_compose_override "${override_file}" "${run_id}" "${vehicles}" "${gpu_enabled}" "${images[@]}"

    log "Starting simulator (once)"
    if [ "${gpu_enabled}" = "1" ]; then
        compose_up "${gpu_enabled}" -f "${COMPOSE_BASE_FILE}" up -d --force-recreate simulator-gpu
    else
        compose_up "${gpu_enabled}" -f "${COMPOSE_BASE_FILE}" up -d --force-recreate simulator
    fi

    local -a autoware_svcs=()
    for domain_id in $(seq 1 "${vehicles}"); do
        autoware_svcs+=("autoware-d${domain_id}")
    done

    log "Starting ${autoware_svcs[*]} (concurrent)"
    compose_up "${gpu_enabled}" -f "${COMPOSE_BASE_FILE}" -f "${override_file}" up -d --force-recreate "${autoware_svcs[@]}"

    log "Started. Output: output/${run_id}/d*/autoware.log"
    log "Stop: ./run_autoware_multi.bash down"
    log "  or: docker compose -f docker-compose.yml -f ${override_file} down --remove-orphans"
}

main "$@"
