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
                               [--output-root /output] [--result-wait-seconds N] [--run-id ID] [--run-group NAME]
  ./docker_build_run.bash all   [--submit <path/to/aichallenge_submit.tar.gz>]...
                               [--device auto|gpu|cpu] [--rosbag] [--capture]
                               [--output-root /output] [--result-wait-seconds N] [--run-id ID]
  ./docker_build_run.bash down

Notes:
  - Logs are written to: output/_host/<event_id>/docker_build_run.log
  - Evaluation artifacts are written under: output/<run_id>/<run_group>/d<domain_id>/ (run_group is optional)
EOF
}

sanitize_group() {
    local name="${1:-}"
    name="${name##*/}"
    name="${name%.tar.gz}"
    name="${name%.tgz}"
    name="${name%.tar}"
    name="${name%.gz}"
    name="$(echo "${name}" | tr -cs 'A-Za-z0-9._-' '_' | sed -E 's/^_+//; s/_+$//')"
    if [ -z "${name}" ]; then
        name="submit"
    fi
    echo "${name}"
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

install_submit_tar() {
    local submit_tar="${1:-}"
    [ -n "${submit_tar}" ] || die "install_submit_tar: submit file not specified"
    [ -f "${submit_tar}" ] || die "submit file not found: ${submit_tar}"

    local src_root="${REPO_ROOT}/aichallenge/workspace/src"
    local dest="${src_root}/aichallenge_submit"
    local tmp
    tmp="$(mktemp -d)"

    log "Installing submit tar into workspace: ${submit_tar}"

    if [[ "${submit_tar}" = *.tar.gz || "${submit_tar}" = *.tgz || "${submit_tar}" = *.gz ]]; then
        tar -xzf "${submit_tar}" -C "${tmp}"
    else
        tar -xf "${submit_tar}" -C "${tmp}"
    fi

    local extracted=""
    if [ -d "${tmp}/aichallenge_submit" ]; then
        extracted="${tmp}/aichallenge_submit"
    else
        extracted="$(find "${tmp}" -maxdepth 3 -type d -name aichallenge_submit -print -quit || true)"
    fi
    if [ -z "${extracted}" ] || [ ! -d "${extracted}" ]; then
        rm -rf "${tmp}" || true
        die "submit tar does not contain aichallenge_submit/ directory: ${submit_tar}"
    fi

    mkdir -p "${src_root}"
    rm -rf "${dest}"
    mv "${extracted}" "${dest}"

    # Best-effort fix for a common ROSIDL packaging rule:
    # Packages that generate interfaces must declare membership in rosidl_interface_packages.
    # (Some submissions forget this and colcon fails early.)
    local ptb_xml="${dest}/parameter_topic_bridge/package.xml"
    if [ -f "${ptb_xml}" ] && ! rg -q "<member_of_group>rosidl_interface_packages</member_of_group>" "${ptb_xml}"; then
        log "Patching rosidl group membership: ${ptb_xml}"
        python3 - "${ptb_xml}" <<'PY' || true
import sys
path = sys.argv[1]
s = open(path, "r", encoding="utf-8").read()
needle = "<member_of_group>rosidl_interface_packages</member_of_group>"
if needle in s:
    sys.exit(0)
if "<export>" in s:
    s = s.replace("<export>", "<export>\n    " + needle, 1)
else:
    s = s.replace("</package>", "  <export>\n    " + needle + "\n  </export>\n</package>", 1)
open(path, "w", encoding="utf-8").write(s)
PY
    fi

    rm -rf "${tmp}" || true
    log "Installed: ${dest}"
}

build_autoware_and_wait() {
    local device="${1:-auto}"
    local gpu_enabled
    gpu_enabled="$(gpu_enabled_from_device "${device}")"

    local svc="aic-build"
    if [ "${gpu_enabled}" = "1" ]; then
        svc="aic-build-gpu"
    fi

    log "Building autoware overlay (service: ${svc}, device: ${device})"
    (cd "${REPO_ROOT}" && make build-autoware DEVICE="${device}")

    local cid=""
    for _ in $(seq 1 30); do
        cid="$(cd "${REPO_ROOT}" && docker compose -f docker-compose.yml ps -aq "${svc}" 2>/dev/null || true)"
        [ -n "${cid}" ] && break
        sleep 1
    done
    [ -n "${cid}" ] || die "failed to find build container id for service: ${svc}"

    local exit_code=""
    exit_code="$(docker wait "${cid}" 2>/dev/null || true)"
    if [ "${exit_code}" != "0" ]; then
        warn "Autoware build failed (exit code: ${exit_code}). Showing last logs:"
        docker logs --tail 200 "${cid}" || true
        die "autoware build failed"
    fi
    log "Autoware build finished (exit code: ${exit_code})"
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
    local run_id=""
    local run_group=""

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
        --run-id)
            run_id="${2:-}"
            shift 2
            ;;
        --run-group)
            run_group="${2:-}"
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
    if [ -n "${run_id}" ]; then
        log "Eval run id: ${run_id}"
    fi
    if [ -n "${run_group}" ]; then
        log "Eval run group: ${run_group}"
    fi

    (cd "${REPO_ROOT}" && make run-sim-eval \
        DEVICE="${device}" \
        DOMAIN_IDS="${domain_ids}" \
        ROSBAG="${rosbag}" \
        CAPTURE="${capture}" \
        OUTPUT_ROOT="${output_root}" \
        RESULT_WAIT_SECONDS="${result_wait_seconds}" \
        RUN_ID="${run_id}" \
        RUN_GROUP="${run_group}")
}

cmd_all() {
    local -a submit_tars=()
    local device="auto"
    local rosbag="false"
    local capture="false"
    local output_root="/output"
    local result_wait_seconds="10"
    local run_id=""

    while [ $# -gt 0 ]; do
        case "$1" in
        --submit | --submit-tar)
            submit_tars+=("${2:-}")
            shift 2
            ;;
        --device)
            device="${2:-}"
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
        --run-id)
            run_id="${2:-}"
            shift 2
            ;;
        *)
            die "Unknown option for all: '$1'"
            ;;
        esac
    done

    if [ -z "${run_id}" ]; then
        run_id="$(ts_compact)-eval-$$"
    fi

    log "All device: ${device}"
    log "All rosbag: ${rosbag}"
    log "All capture: ${capture}"
    log "All output root: ${output_root}"
    log "All result wait seconds: ${result_wait_seconds}"
    log "All run id: ${run_id}"

    if [ "${#submit_tars[@]}" -eq 0 ]; then
        warn "No --submit provided. Using existing aichallenge/workspace/src/aichallenge_submit/."
        build_autoware_and_wait "${device}"
        local -a eval_flags=()
        if [ "${rosbag}" = "true" ]; then eval_flags+=(--rosbag); fi
        if [ "${capture}" = "true" ]; then eval_flags+=(--capture); fi
        cmd_eval \
            --device "${device}" \
            --output-root "${output_root}" \
            --result-wait-seconds "${result_wait_seconds}" \
            --run-id "${run_id}" \
            "${eval_flags[@]}"
        return 0
    fi

    if [ "${#submit_tars[@]}" -gt 4 ]; then
        die "too many --submit args (${#submit_tars[@]}). max is 4 (mapped to domain id 1..4)"
    fi

    local -a eval_flags=()
    if [ "${rosbag}" = "true" ]; then eval_flags+=(--rosbag); fi
    if [ "${capture}" = "true" ]; then eval_flags+=(--capture); fi

    for i in "${!submit_tars[@]}"; do
        local submit_tar="${submit_tars[$i]}"
        local domain_id="$((i + 1))"

        [ -n "${submit_tar}" ] || die "--submit requires a file path"
        [ -f "${submit_tar}" ] || die "submit file not found: ${submit_tar}"

        local group
        group="$(sanitize_group "${submit_tar}")"

        log "========================================================"
        log "Submit: ${submit_tar}"
        log "Group: ${group}"
        log "Domain id: ${domain_id}"
        log "========================================================"

        install_submit_tar "${submit_tar}"
        build_autoware_and_wait "${device}"
        cmd_eval \
            --device "${device}" \
            --domain-ids "${domain_id}" \
            --output-root "${output_root}" \
            --result-wait-seconds "${result_wait_seconds}" \
            --run-id "${run_id}" \
            --run-group "${group}" \
            "${eval_flags[@]}"
    done
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
