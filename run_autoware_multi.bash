#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[run_autoware_multi] $*"
}

warn() {
    echo "[run_autoware_multi][WARN] $*" >&2
}

die() {
    echo "[run_autoware_multi][ERROR] $*" >&2
    exit 1
}

ts_compact() {
    date +%Y%m%d-%H%M%S
}

gpu_enabled_from_device() {
    local device="${1:-auto}"
    case "${device}" in
    gpu)
        echo 1
        ;;
    cpu)
        echo 0
        ;;
    auto)
        if command -v nvidia-smi >/dev/null 2>&1 && [ -e /dev/nvidia0 ]; then
            echo 1
        else
            echo 0
        fi
        ;;
    *)
        die "invalid --device: '${device}' (use auto|gpu|cpu)"
        ;;
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
    if [ -z "${s}" ]; then
        s="submit"
    fi
    echo "${s}"
}

make_output_dirs() {
    local run_id="$1"
    local vehicles="$2"

    mkdir -p "${REPO_ROOT}/output"
    mkdir -p "${REPO_ROOT}/output/_host"

    if [ -e "${REPO_ROOT}/output/latest" ] && [ ! -L "${REPO_ROOT}/output/latest" ]; then
        local legacy
        legacy="${REPO_ROOT}/output/_host/legacy-output-latest-${run_id}-$$"
        warn "Moving legacy output/latest to ${legacy}"
        mv "${REPO_ROOT}/output/latest" "${legacy}"
    fi

    mkdir -p "${REPO_ROOT}/output/${run_id}"
    ln -nfs "${run_id}" "${REPO_ROOT}/output/latest"

    for i in $(seq 1 "${vehicles}"); do
        mkdir -p "${REPO_ROOT}/output/${run_id}/d${i}"
    done
}

write_compose_override() {
    local out_file="$1"
    local run_id="$2"
    local vehicles="$3"
    local gpu_enabled="$4"
    shift 4
    local -a images=("$@")

    cat >"${out_file}" <<EOF
services:
EOF

    for i in $(seq 1 "${vehicles}"); do
        local img="${images[$((i - 1))]}"
        cat >>"${out_file}" <<EOF
  autoware-d${i}:
    image: "${img}"
    privileged: true
    pull_policy: never
    network_mode: host
EOF
        if [ "${gpu_enabled}" = "1" ]; then
            cat >>"${out_file}" <<'EOF'
    runtime: nvidia
EOF
        fi
        cat >>"${out_file}" <<EOF
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
}

usage() {
    cat <<'EOF'
Usage:
  ./run_autoware_multi.bash --submit <aichallenge_submit.tar.gz> [--submit <...> ...]
                            [--vehicles N] [--device auto|gpu|cpu] [--run-id ID]

Notes:
  - Starts AWSIM once (docker compose service: simulator).
  - Starts Autoware containers autoware-d1..autoware-dN concurrently.
  - Each submit is baked into its own image (Dockerfile target: eval) and assigned domain id 1..4 by submit order.
  - Artifacts are written to output/<run_id>/d<domain_id>/autoware.log and output/latest -> <run_id>.
EOF
}

main() {
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

    if [ "${#submits[@]}" -eq 0 ]; then
        die "At least one --submit is required"
    fi

    if [ -z "${vehicles}" ]; then
        vehicles="${#submits[@]}"
    fi

    case "${vehicles}" in
    ''|*[!0-9]*)
        die "--vehicles must be a number (1..4)"
        ;;
    esac

    if [ "${vehicles}" -lt 1 ] || [ "${vehicles}" -gt 4 ]; then
        die "--vehicles must be in 1..4"
    fi

    if [ "${#submits[@]}" -ne "${vehicles}" ]; then
        die "--vehicles (${vehicles}) must match --submit count (${#submits[@]})"
    fi

    if [ -z "${run_id}" ]; then
        run_id="$(ts_compact)-autoware-multi-$$"
    fi

    local gpu_enabled
    gpu_enabled="$(gpu_enabled_from_device "${device}")"

    mkdir -p "${REPO_ROOT}/output/_host"
    local event_id log_dir log_file
    event_id="$(ts_compact)-run_autoware_multi-$$"
    log_dir="${REPO_ROOT}/output/_host/${event_id}"
    mkdir -p "${log_dir}"
    ln -nfs "${event_id}" "${REPO_ROOT}/output/_host/latest-autoware-multi"
    log_file="${log_dir}/run_autoware_multi.log"
    touch "${log_file}" || true
    exec > >(tee -a "${log_file}") 2>&1

    log "Log dir: ${log_dir}"
    log "Run id: ${run_id}"
    log "Vehicles: ${vehicles}"
    log "Device: ${device} (gpu_enabled=${gpu_enabled})"

    make_output_dirs "${run_id}" "${vehicles}"

    local -a images=()
    for i in $(seq 1 "${vehicles}"); do
        local submit="${submits[$((i - 1))]}"
        [ -f "${submit}" ] || die "submit file not found: ${submit}"

        # Dockerfile COPY for SUBMIT_TAR must be within build context.
        local submit_abs
        submit_abs="$(realpath "${submit}")"
        case "${submit_abs}" in
        "${REPO_ROOT}"/*) ;;
        *)
            die "submit must be under repo root (docker build context): ${submit}"
            ;;
        esac
        local submit_rel
        submit_rel="${submit_abs#${REPO_ROOT}/}"

        local tag
        tag="aichallenge-2025-eval-$(sanitize_tag_fragment "${submit_rel}")-${run_id}-d${i}"

        log "Build image for d${i}: ${tag} (SUBMIT_TAR=${submit_rel})"
        docker build --progress=plain --target eval --build-arg "SUBMIT_TAR=${submit_rel}" -t "${tag}" "${REPO_ROOT}"
        images+=("${tag}")
    done

    local override_file
    override_file="${log_dir}/compose.autoware_multi.yml"
    write_compose_override "${override_file}" "${run_id}" "${vehicles}" "${gpu_enabled}" "${images[@]}"

    log "Starting simulator (once)"
    if [ "${gpu_enabled}" = "1" ]; then
        NVIDIA_VISIBLE_DEVICES="all" NVIDIA_DRIVER_CAPABILITIES="all" docker compose -f "${REPO_ROOT}/docker-compose.yml" up -d --force-recreate simulator-gpu
    else
        docker compose -f "${REPO_ROOT}/docker-compose.yml" up -d --force-recreate simulator
    fi

    log "Starting autoware-d1..d${vehicles} (concurrent)"
    if [ "${gpu_enabled}" = "1" ]; then
        NVIDIA_VISIBLE_DEVICES="all" NVIDIA_DRIVER_CAPABILITIES="all" docker compose -f "${REPO_ROOT}/docker-compose.yml" -f "${override_file}" up -d --force-recreate $(printf 'autoware-d%s ' $(seq 1 "${vehicles}"))
    else
        docker compose -f "${REPO_ROOT}/docker-compose.yml" -f "${override_file}" up -d --force-recreate $(printf 'autoware-d%s ' $(seq 1 "${vehicles}"))
    fi

    log "Started. Output: output/${run_id}/d*/autoware.log"
    log "To stop everything: docker compose -f docker-compose.yml -f ${override_file} down --remove-orphans"
}

main "$@"

