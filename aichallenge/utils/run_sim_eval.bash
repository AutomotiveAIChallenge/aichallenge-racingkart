#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

log() { echo "[run_sim_eval] $*"; }

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/eval_flow.bash"

aic_eval_log() { log "$@"; }

aic_eval_run_or_exit() {
    local label="$1"
    shift
    "$@"
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "[run_sim_eval][ERROR] ${label} failed (rc=${rc})" >&2
        return $rc
    fi
}

run_id="${RUN_ID-}"
if [ -z "${run_id}" ]; then
    run_id="$(date +%Y%m%d-%H%M%S)"
fi

run_group="${RUN_GROUP-}"
run_rel="${run_id}"
if [ -n "${run_group}" ]; then
    run_rel="${run_id}/${run_group}"
fi

mkdir -p output output/_host
if [ -e output/latest ] && [ ! -L output/latest ]; then
    legacy="output/_host/legacy-output-latest-${run_id}-${RANDOM}"
    log "Moving legacy output/latest to ${legacy}"
    mv output/latest "${legacy}"
fi

mkdir -p "output/${run_rel}"
ln -nfs "${run_id}" output/latest

output_root="${OUTPUT_ROOT:-/output}"
domain_ids="${DOMAIN_IDS:-${DOMAIN_ID:-1}}"
domain_ids="${domain_ids//,/ }"
result_wait_seconds="${RESULT_WAIT_SECONDS:-10}"
rosbag_enabled="${ROSBAG:-false}"
capture_enabled="${CAPTURE:-false}"

sim_svc="${SIMULATOR_SERVICE:-simulator}"
autoware_svc="${AUTOWARE_SERVICE:-autoware}"
cmd_svc="${AW_CMD_SERVICE:-autoware-command}"
rosbag_svc="${ROSBAG_SERVICE:-autoware-rosbag}"

host_uid="${HOST_UID:-$(id -u)}"
host_gid="${HOST_GID:-$(id -g)}"

dc_str="${DC:-docker compose -f docker-compose.yml}"
read -r -a dc_cmd <<<"${dc_str}"

capture_started=0
rosbag_started=0
sim_cid=""
autoware_cid=""
rosbag_cid=""
output_run_dir=""
domain_id=""

dc() {
    OUTPUT_ROOT="${output_root}" \
        OUTPUT_RUN_DIR="${output_run_dir}" \
        DOMAIN_ID="${domain_id}" \
        EVAL_RUN=1 \
        CMD_WORKDIR="${output_run_dir}" \
        SIM_MODE="${SIM_MODE-}" \
        RUN_MODE="${RUN_MODE-}" \
        CMD="${CMD-}" \
        "${dc_cmd[@]}" "$@"
}

aic_eval_backend_start_simulator() {
    local SIM_MODE="eval"
    dc up -d --force-recreate "${sim_svc}"
}

aic_eval_backend_wait_sim_ready() {
    local CMD="env ROS_DOMAIN_ID=${AIC_EVAL_SIM_DOMAIN_ID:-0} /aichallenge/utils/publish.bash check-awsim"
    dc run --rm --no-deps "${cmd_svc}"
}

aic_eval_backend_start_autoware() {
    local RUN_MODE="awsim"
    dc up -d --force-recreate "${autoware_svc}"
}

aic_eval_backend_move_window_best_effort() {
    local CMD="bash /aichallenge/utils/move_window.bash"
    dc run --rm --no-deps "${cmd_svc}"
}

aic_eval_backend_request_initialpose() {
    local CMD="env ROS_DOMAIN_ID=${AIC_EVAL_DOMAIN_ID} /aichallenge/utils/publish.bash request-initialpose"
    dc run --rm --no-deps "${cmd_svc}"
}

aic_eval_backend_request_control() {
    local CMD="env ROS_DOMAIN_ID=${AIC_EVAL_DOMAIN_ID} /aichallenge/utils/publish.bash request-control"
    dc run --rm --no-deps "${cmd_svc}"
}

aic_eval_backend_start_capture_best_effort() {
    local CMD="env ROS_DOMAIN_ID=${AIC_EVAL_DOMAIN_ID} /aichallenge/utils/publish.bash request-capture"
    dc run --rm --no-deps "${cmd_svc}" >/dev/null 2>&1 || true
    capture_started=1
}

aic_eval_backend_start_rosbag_best_effort() {
    dc up -d --force-recreate "${rosbag_svc}" >/dev/null 2>&1 || true
    rosbag_started=1
}

aic_eval_backend_wait_sim_finish() {
    sim_cid="$(dc ps -q "${sim_svc}")"
    if [ -n "${sim_cid}" ]; then
        docker wait "${sim_cid}" >/dev/null 2>&1 || true
    fi
}

aic_eval_backend_convert_result_best_effort() {
    local CMD="bash /aichallenge/utils/convert_result.bash ${AIC_EVAL_DOMAIN_ID} ${AIC_EVAL_RESULT_WAIT_SECONDS}"
    dc run --rm --no-deps "${cmd_svc}" >/dev/null 2>&1 || true
}

aic_eval_backend_cleanup_domain() {
    cleanup_domain
}

cleanup_domain() {
    local had_errexit=0
    case $- in *e*) had_errexit=1 ;; esac
    set +e

    if [ "${capture_started}" -eq 1 ]; then
        local CMD="env ROS_DOMAIN_ID=${domain_id} /aichallenge/utils/publish.bash request-capture"
        dc run --rm --no-deps "${cmd_svc}" >/dev/null 2>&1 || true
    fi

    if [ "${rosbag_started}" -eq 1 ]; then
        rosbag_cid="$(dc ps -q "${rosbag_svc}" 2>/dev/null || true)"
        if [ -n "${rosbag_cid}" ]; then
            docker kill --signal INT "${rosbag_cid}" >/dev/null 2>&1 || true
            docker wait "${rosbag_cid}" >/dev/null 2>&1 || true
        fi
        dc stop "${rosbag_svc}" >/dev/null 2>&1 || true
    fi

    autoware_cid="$(dc ps -q "${autoware_svc}" 2>/dev/null || true)"
    if [ -n "${autoware_cid}" ]; then
        docker kill --signal INT "${autoware_cid}" >/dev/null 2>&1 || true
        docker wait "${autoware_cid}" >/dev/null 2>&1 || true
    fi
    dc stop "${autoware_svc}" >/dev/null 2>&1 || true

    sim_cid="$(dc ps -q "${sim_svc}" 2>/dev/null || true)"
    if [ -n "${sim_cid}" ]; then
        docker kill --signal INT "${sim_cid}" >/dev/null 2>&1 || true
        docker wait "${sim_cid}" >/dev/null 2>&1 || true
    fi
    dc stop "${sim_svc}" >/dev/null 2>&1 || true

    if [ "${had_errexit}" -eq 1 ]; then
        set -e
    fi
}

cleanup_all() {
    local had_errexit=0
    case $- in *e*) had_errexit=1 ;; esac
    set +e
    cleanup_domain
    local CMD="bash /aichallenge/utils/fix_ownership.bash ${host_uid} ${host_gid} ${output_root} ${run_id}"
    dc run --rm --no-deps "${cmd_svc}" >/dev/null 2>&1 || true

    if [ "${had_errexit}" -eq 1 ]; then
        set -e
    fi
}

trap cleanup_all EXIT
trap 'echo "[run_sim_eval] Interrupted" >&2; exit 130' INT
trap 'echo "[run_sim_eval] Terminated" >&2; exit 143' TERM

for domain_id in ${domain_ids}; do
    mkdir -p "output/${run_rel}/d${domain_id}"
    output_run_dir="${output_root}/${run_rel}/d${domain_id}"
    echo "OUTPUT: output/${run_rel}/d${domain_id} (container: ${output_run_dir})"

    capture_started=0
    rosbag_started=0

    aic_eval_flow_run_domain "${domain_id}" 0 "${result_wait_seconds}" "${capture_enabled}" "${rosbag_enabled}"
    echo "[run_sim_eval] Domain ${domain_id} finished"
done

echo "[run_sim_eval] Evaluation finished"
