#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

run_id="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
run_rel="${run_id}"
[ -n "${RUN_GROUP-}" ] && run_rel="${run_id}/${RUN_GROUP}"

mkdir -p "output/${run_rel}"
ln -nfs "${run_id}" output/latest || true

output_root="${OUTPUT_ROOT:-/output}"
domain_ids="${DOMAIN_IDS:-${DOMAIN_ID:-1}}"
domain_ids="${domain_ids//,/ }"
rosbag_enabled="${ROSBAG:-false}"
capture_enabled="${CAPTURE:-false}"

sim_svc="${SIMULATOR_SERVICE:-simulator}"
autoware_svc="${AUTOWARE_SERVICE:-autoware}"
cmd_svc="${AW_CMD_SERVICE:-autoware-command}"

host_uid="${HOST_UID:-$(id -u)}"
host_gid="${HOST_GID:-$(id -g)}"
dc_str="${DC:-docker compose -f docker-compose.yml}"
read -r -a dc_cmd <<<"${dc_str}"

domain_id=""
output_run_dir=""
dc() {
    OUTPUT_ROOT="${output_root}" \
        OUTPUT_RUN_DIR="${output_run_dir}" \
        DOMAIN_ID="${domain_id}" \
        AIC_CAPTURE="${capture_enabled}" \
        AIC_ROSBAG="${rosbag_enabled}" \
        EVAL_RUN=1 \
        CMD_WORKDIR="${output_run_dir}" \
        SIM_MODE="${SIM_MODE-}" \
        RUN_MODE="${RUN_MODE-}" \
        CMD="${CMD-}" \
        "${dc_cmd[@]}" "$@"
}

stop_services_best_effort() {
    set +e
    dc stop "${autoware_svc}" >/dev/null 2>&1 || true
    dc stop "${sim_svc}" >/dev/null 2>&1 || true
}
cleanup_all() {
    stop_services_best_effort
    CMD="bash /aichallenge/utils/fix_ownership.bash ${host_uid} ${host_gid} ${output_root} ${run_id}"
    dc run --rm --no-deps "${cmd_svc}" >/dev/null 2>&1 || true
}
trap cleanup_all EXIT INT TERM

for domain_id in ${domain_ids}; do
    mkdir -p "output/${run_rel}/d${domain_id}"
    output_run_dir="${output_root}/${run_rel}/d${domain_id}"
    echo "OUTPUT: output/${run_rel}/d${domain_id} (container: ${output_run_dir})"

    SIM_MODE="${SIM_MODE:-eval}"
    dc up -d --force-recreate "${sim_svc}"
    unset SIM_MODE
    CMD="env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-state" dc run --rm --no-deps "${cmd_svc}"

    RUN_MODE=awsim
    dc up -d --force-recreate "${autoware_svc}"
    unset RUN_MODE
    sleep "${AIC_EVAL_AUTOWARE_START_SLEEP_SECONDS:-3}" || true

    if [ "${AIC_EVAL_WAIT_ADMIN_STATUS_FINISH:-0}" = "1" ]; then
        CMD="env ROS_DOMAIN_ID=0 AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_STATE=${AIC_EVAL_ADMIN_STATUS_FINISH_TIMEOUT_S:-1800} /aichallenge/utils/publish.bash wait-admin-state FinishALL Terminate" \
            dc run --rm --no-deps "${cmd_svc}" || true
    fi

    # By default, the evaluation ends when the simulator exits (AWSIM finishes the run).
    sim_cid="$(dc ps -q "${sim_svc}" 2>/dev/null || true)"
    if [ -n "${sim_cid}" ]; then
        docker wait "${sim_cid}" >/dev/null 2>&1 || true
    fi

    stop_services_best_effort
done
