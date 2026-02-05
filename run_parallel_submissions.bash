#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_BASENAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_NAME="${SCRIPT_BASENAME%.*}"
COMPOSE_BASE_FILE="${REPO_ROOT}/docker-compose.yml"
COMPOSE_GPU_FILE="${REPO_ROOT}/docker-compose.gpu.yml"
COMPOSE_PROJECT_FILE_NAME="compose.project"

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
  ./run_parallel_submissions.bash collect [--vehicles N]
  ./run_parallel_submissions.bash [--capture] [--rosbag] --submit <aichallenge_submit.tar.gz> [<aichallenge_submit.tar.gz> ...]
  DEVICE=<auto|gpu|cpu> ./run_parallel_submissions.bash [--capture] [--rosbag] --submit <aichallenge_submit.tar.gz> [<aichallenge_submit.tar.gz> ...]

Behavior:
  - Starts AWSIM once (docker compose service: simulator).
  - Waits for /admin/awsim/state via topic.
  - Builds 1 eval image per submit (Dockerfile target: eval).
  - Starts Autoware containers autoware-d1..autoware-dN concurrently.
  - Domain id is assigned by submit order: 1..4 (max 4).
  - Writes logs under output/<run_id>/d<domain_id>/autoware.log and output/latest -> <run_id>.
  - Writes this script log to output/<run_id>/<script_name>.log.
  - Writes compose override to output/<run_id>/compose.autoware_multi.yml.

Env:
  DEVICE=auto|gpu|cpu    GPU selection (default: auto)
                         auto: enable GPU if /dev/nvidia0 exists
                         gpu : force GPU override (requires Docker-side NVIDIA support)
                         cpu : never use GPU override
  CAPTURE=true|false     Enable autostart_orchestrator capture toggle (default: false)
  ROSBAG=true|false      Enable autostart_orchestrator rosbag recording (default: false)
  AIC_PARALLEL_COMPOSE_PROJECT=<name>
                         Override docker compose project name (default: auto)
EOF
}

is_number() {
    local s="${1-}"
    [[ -n ${s} && ${s} =~ ^[0-9]+$ ]]
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

sanitize_tag_fragment() {
    local s="${1-}"
    s="${s##*/}"
    s="${s%.tar.gz}"
    s="${s%.tgz}"
    s="${s%.tar}"
    s="${s%.gz}"
    s="$(echo "${s}" | tr -cs 'A-Za-z0-9._-' '_' | sed -E 's/^_+//; s/_+$//')"
    echo "${s:-submit}"
}

sanitize_project_name() {
    local s="${1-}"
    s="$(echo "${s}" | tr -cs 'A-Za-z0-9._-' '-' | sed -E 's/^-+//; s/-+$//')"
    s="${s,,}"
    echo "${s:-aichallenge}"
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
    for ((i = 1; i <= vehicles; i++)); do
        mkdir -p "${REPO_ROOT}/output/${run_id}/d${i}"
    done
}

resolve_run_id_default() {
    local latest="${REPO_ROOT}/output/latest"
    if [ -L "${latest}" ]; then
        readlink "${latest}" || true
    fi
}

resolve_project_name_from_run_id_best_effort() {
    local run_id="$1"
    local run_root="${REPO_ROOT}/output/${run_id}"
    local f="${run_root}/${COMPOSE_PROJECT_FILE_NAME}"
    if [ -f "${f}" ]; then
        cat "${f}" || true
    fi
}

detect_vehicles() {
    local run_id="$1"
    local run_root="${REPO_ROOT}/output/${run_id}"
    local count=0
    local i
    for ((i = 1; i <= 4; i++)); do
        if [ -d "${run_root}/d${i}" ]; then
            count=$((count + 1))
        fi
    done
    if [ "${count}" -eq 0 ]; then
        echo 0
        return 0
    fi
    echo "${count}"
}

collect_results() {
    local run_id="$1"
    local vehicles="$2"

    local run_root="${REPO_ROOT}/output/${run_id}"
    [ -d "${run_root}" ] || die "run root not found: ${run_root}"

    local i
    for ((i = 1; i <= vehicles; i++)); do
        local dest="${run_root}/d${i}"
        mkdir -p "${dest}"

        # AWSIM tends to output dN-result*.json in its working directory.
        # Move them into per-domain folders for easier browsing.
        (
            shopt -s nullglob
            local f
            for f in "${run_root}/d${i}-result"*.json "${REPO_ROOT}/aichallenge/d${i}-result"*.json; do
                mv -f "${f}" "${dest}/" || true
            done
        )
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

write_compose_project_file() {
    local run_id="$1"
    local project="$2"
    echo "${project}" >"${REPO_ROOT}/output/${run_id}/${COMPOSE_PROJECT_FILE_NAME}"
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
    local capture_enabled="$5"
    local rosbag_enabled="$6"
    shift 6
    local -a images=("$@")

    {
        echo "services:"
        local i
        for ((i = 1; i <= vehicles; i++)); do
            local img="${images[$((i - 1))]}"
            cat <<EOF
  autoware-d${i}:
    image: "${img}"
    privileged: true
    pull_policy: never
    network_mode: host
EOF
            if [ "${gpu_enabled}" = "1" ]; then
                cat <<'EOF'
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: ["gpu"]
EOF
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
      - OUTPUT_RUN_DIR=/output/${run_id}/d${i}
      - AIC_CAPTURE=${capture_enabled}
      - AIC_ROSBAG=${rosbag_enabled}
      - DOMAIN_ID=${i}
      - RUN_ID=${run_id}
EOF
            if [ "${gpu_enabled}" = "1" ]; then
                cat <<EOF
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=all
EOF
            fi
            cat <<EOF
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
    local project="$2"
    shift 2
    if [ "${gpu_enabled}" = "1" ]; then
        NVIDIA_VISIBLE_DEVICES="all" NVIDIA_DRIVER_CAPABILITIES="all" docker compose -p "${project}" "$@"
    else
        docker compose -p "${project}" "$@"
    fi
}

sanitize_yaml_tabs_in_place_best_effort() {
    local file="$1"
    [ -f "${file}" ] || return 0

    if LC_ALL=C grep -q $'\t' "${file}"; then
        warn "compose override contains tab characters (invalid YAML). Sanitizing in place: ${file}"
        local tmp="${file}.tmp.$$"
        if command -v expand >/dev/null 2>&1; then
            expand -t 2 "${file}" >"${tmp}" || {
                rm -f "${tmp}"
                return 1
            }
        else
            sed $'s/\t/  /g' "${file}" >"${tmp}" || {
                rm -f "${tmp}"
                return 1
            }
        fi
        mv -f "${tmp}" "${file}" || {
            rm -f "${tmp}"
            return 1
        }
    fi
}

cleanup_compose_project_best_effort() {
    local project="$1"

    local -a cids=()
    mapfile -t cids < <(docker ps -aq --filter "label=com.docker.compose.project=${project}" 2>/dev/null || true)
    if [ "${#cids[@]}" -eq 0 ]; then
        warn "No containers found for compose project '${project}' (override parse failed)"
        return 0
    fi

    warn "Removing containers by label (project='${project}') due to compose override parse failure"
    docker rm -f "${cids[@]}" >/dev/null 2>&1 || true
}

cmd_down() {
    local legacy_log_dir=""

    while [ $# -gt 0 ]; do
        case "$1" in
        --log-dir)
            legacy_log_dir="${2-}"
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

    local run_id=""
    run_id="$(resolve_run_id_default)"

    local override_file=""
    if [ -n "${run_id}" ] && [ -f "${REPO_ROOT}/output/${run_id}/compose.autoware_multi.yml" ]; then
        override_file="${REPO_ROOT}/output/${run_id}/compose.autoware_multi.yml"
    elif [ -n "${legacy_log_dir}" ] && [ -f "${legacy_log_dir}/compose.autoware_multi.yml" ]; then
        override_file="${legacy_log_dir}/compose.autoware_multi.yml"
    elif [ -f "${REPO_ROOT}/output/_host/latest-autoware-parallel-submissions/compose.autoware_multi.yml" ]; then
        override_file="${REPO_ROOT}/output/_host/latest-autoware-parallel-submissions/compose.autoware_multi.yml"
    else
        die "compose override not found (hint: run once to create output/latest, or specify --log-dir <dir>)"
    fi

    sanitize_yaml_tabs_in_place_best_effort "${override_file}" || warn "Failed to sanitize tabs in ${override_file} (continuing)"

    local project=""
    local override_dir
    override_dir="$(cd "$(dirname "${override_file}")" && pwd)"
    if [ -f "${override_dir}/${COMPOSE_PROJECT_FILE_NAME}" ]; then
        project="$(cat "${override_dir}/${COMPOSE_PROJECT_FILE_NAME}" 2>/dev/null || true)"
    elif [ -n "${run_id}" ]; then
        project="$(resolve_project_name_from_run_id_best_effort "${run_id}")"
    fi
    if [ -z "${project}" ]; then
        project="$(sanitize_project_name "$(basename "${REPO_ROOT}")")"
    fi

    log "docker compose down --remove-orphans (project: ${project}, override: ${override_file})"
    if docker compose -p "${project}" -f "${COMPOSE_BASE_FILE}" -f "${override_file}" config -q >/dev/null 2>&1; then
        docker compose -p "${project}" -f "${COMPOSE_BASE_FILE}" -f "${override_file}" down --remove-orphans
    elif docker compose -p "${project}" -f "${COMPOSE_BASE_FILE}" -f "${COMPOSE_GPU_FILE}" -f "${override_file}" config -q >/dev/null 2>&1; then
        docker compose -p "${project}" -f "${COMPOSE_BASE_FILE}" -f "${COMPOSE_GPU_FILE}" -f "${override_file}" down --remove-orphans
    else
        warn "docker compose failed to parse override file: ${override_file}"
        cleanup_compose_project_best_effort "${project}" || true
    fi

    # AWSIM result jsons are generated after AWSIM exits.
    # Collect after stopping simulator so the result files are present.
    if [ -n "${run_id}" ]; then
        local vehicles
        vehicles="$(detect_vehicles "${run_id}")"
        if [ "${vehicles}" -gt 0 ]; then
            log "Collecting AWSIM result jsons into per-domain folders (run_id=${run_id}, vehicles=${vehicles})"
            collect_results "${run_id}" "${vehicles}" || true
        fi
    fi
}

cmd_collect() {
    local vehicles=""

    while [ $# -gt 0 ]; do
        case "$1" in
        --vehicles)
            vehicles="${2-}"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option for collect: '$1'"
            ;;
        esac
    done

    local run_id=""
    run_id="$(resolve_run_id_default)"
    [ -n "${run_id}" ] || die "output/latest not found (run once first)"

    if [ -z "${vehicles}" ]; then
        vehicles="$(detect_vehicles "${run_id}")"
    fi
    is_number "${vehicles}" || die "--vehicles must be a number (1..4)"
    if [ "${vehicles}" -lt 1 ] || [ "${vehicles}" -gt 4 ]; then die "--vehicles must be in 1..4"; fi

    log "Collecting AWSIM result jsons (run_id=${run_id}, vehicles=${vehicles})"
    collect_results "${run_id}" "${vehicles}"
}

run_autoware_command_best_effort() {
    local gpu_enabled="$1"
    local project="$2"
    local cmd="$3"

    if [ "${gpu_enabled}" = "1" ]; then
        CMD="${cmd}" compose_up "${gpu_enabled}" "${project}" -f "${COMPOSE_BASE_FILE}" -f "${COMPOSE_GPU_FILE}" run --rm --no-deps autoware-command || return 1
    else
        CMD="${cmd}" compose_up "${gpu_enabled}" "${project}" -f "${COMPOSE_BASE_FILE}" run --rm --no-deps autoware-command || return 1
    fi
}

main() {
    if [ "${1-}" = "down" ]; then
        shift
        cmd_down "$@"
        return 0
    fi
    if [ "${1-}" = "collect" ]; then
        shift
        cmd_collect "$@"
        return 0
    fi

    local run_id=""
    local device="${DEVICE:-auto}"
    local capture_enabled="${CAPTURE:-false}"
    local rosbag_enabled="${ROSBAG:-false}"
    local -a submits=()

    while [ $# -gt 0 ]; do
        case "$1" in
        --capture)
            capture_enabled="true"
            shift
            ;;
        --rosbag)
            rosbag_enabled="true"
            shift
            ;;
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
    log "Capture: ${capture_enabled}"
    log "Rosbag: ${rosbag_enabled}"

    ensure_output_dirs "${run_id}" "${vehicles}"

    local -a images=()
    local domain_id
    for ((domain_id = 1; domain_id <= vehicles; domain_id++)); do
        local submit="${submits[$((domain_id - 1))]}"
        [ -f "${submit}" ] || die "submit file not found: ${submit}"

        local submit_rel
        submit_rel="$(require_submit_in_build_context "${submit}")"

        images+=("$(build_eval_image "${submit_rel}" "${run_id}" "${domain_id}")")
    done

    local override_file="${REPO_ROOT}/output/${run_id}/compose.autoware_multi.yml"
    write_compose_override "${override_file}" "${run_id}" "${vehicles}" "${gpu_enabled}" "${capture_enabled}" "${rosbag_enabled}" "${images[@]}"

    local project="${AIC_PARALLEL_COMPOSE_PROJECT-}"
    if [ -z "${project}" ]; then
        project="$(sanitize_project_name "aichallenge-${SCRIPT_NAME}-${run_id}")"
    else
        project="$(sanitize_project_name "${project}")"
    fi
    write_compose_project_file "${run_id}" "${project}"
    log "Compose project: ${project}"

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
    OUTPUT_RUN_DIR="/output/${run_id}" SIM_MODE="${sim_mode}" \
        compose_up "${gpu_enabled}" "${project}" "${compose_args[@]}" up -d --force-recreate simulator

    local -a autoware_svcs=()
    for domain_id in $(seq 1 "${vehicles}"); do
        autoware_svcs+=("autoware-d${domain_id}")
    done

    log "Starting ${autoware_svcs[*]} (concurrent)"
    compose_up "${gpu_enabled}" "${project}" "${compose_args[@]}" -f "${override_file}" up -d --force-recreate "${autoware_svcs[@]}"

    log "Waiting for AWSIM readiness (/admin/awsim/state)"
    run_autoware_command_best_effort "${gpu_enabled}" "${project}" "env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-state" || die "AWSIM readiness check failed"

    log "Waiting for Autoware startup"
    sleep "${AIC_EVAL_AUTOWARE_START_SLEEP_SECONDS:-3}" || true

    log "Initial pose / control / (optional) capture+rosbag are handled by autostart_orchestrator_py (AWSIM only)"

    log "Started. Output: output/${run_id}/d*/autoware.log"
    log "Stop: ./run_parallel_submissions.bash down"
    if [ "${gpu_enabled}" = "1" ]; then
        log "  or: docker compose -p ${project} -f docker-compose.yml -f docker-compose.gpu.yml -f ${override_file} down --remove-orphans"
    else
        log "  or: docker compose -p ${project} -f docker-compose.yml -f ${override_file} down --remove-orphans"
    fi
}

main "$@"
