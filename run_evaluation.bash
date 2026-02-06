#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage:
  ./run_evaluation.bash [test]

Modes:
  (default)  Evaluation mode (SIM_MODE=eval by default)
  test       Smoke-test mode (forces SIM_MODE=test, ROSBAG=true, CAPTURE=true, single DOMAIN_ID)

Environment variables (examples):
  DOMAIN_ID=1 OUTPUT_ROOT=/output ROSBAG=true CAPTURE=true ./run_evaluation.bash
USAGE
}

: "${ROSBAG:=false}"
: "${CAPTURE:=false}"
SIM_MODE="${SIM_MODE:-eval}"

mode="${1-}"
case "${mode}" in
"") ;;
test)
    shift
    # Equivalent to the old `make test`: run AWSIM in test mode and enable capture+rosbag.
    SIM_MODE="test"
    ROSBAG="true"
    CAPTURE="true"
    # Force single-domain run for smoke tests.
    export DOMAIN_IDS="${DOMAIN_ID:-1}"
    ;;
-h | --help | help)
    usage
    exit 0
    ;;
*)
    echo "invalid argument: '${mode}'" >&2
    usage >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
cd "${REPO_ROOT}"

run_id="${RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
run_rel="${run_id}"
[ -n "${RUN_GROUP-}" ] && run_rel="${run_id}/${RUN_GROUP}"

mkdir -p "output/${run_rel}"
ln -nfs "${run_id}" output/latest || true

output_root="${OUTPUT_ROOT:-/output}"
domain_ids="${DOMAIN_IDS:-${DOMAIN_ID:-1}}"
domain_ids="${domain_ids//,/ }"
read -r -a domain_id_list <<<"${domain_ids}"
if [ "${#domain_id_list[@]}" -ne 1 ]; then
    echo "DOMAIN_IDS supports only a single domain in run_evaluation.bash (got: ${domain_ids})" >&2
    echo "Hint: use ./run_parallel_submissions.bash for multi-domain / multi-container runs." >&2
    exit 2
fi
domain_id="${domain_id_list[0]}"

host_uid="${HOST_UID:-$(id -u)}"
host_gid="${HOST_GID:-$(id -g)}"

# Keep behavior consistent with Makefile (DEVICE=auto|gpu|cpu and GPU override selection).
# If DC is not explicitly provided, ask Makefile for the exact docker compose command it would use.
if [ -z "${DC-}" ]; then
    dc_str="$(make --no-print-directory print-dc)"
    # Also apply Makefile's GPU env exports (when enabled).
    eval "$(make --no-print-directory print-gpu-env)" || true
else
    dc_str="${DC}"
fi
read -r -a dc_cmd <<<"${dc_str}"

output_run_dir=""
dc() {
    OUTPUT_ROOT="${output_root}" \
        OUTPUT_RUN_DIR="${output_run_dir}" \
        AIC_CAPTURE="${CAPTURE:-false}" \
        AIC_ROSBAG="${ROSBAG:-false}" \
        DOMAIN_ID="${domain_id:-${DOMAIN_ID:-1}}" \
        CMD_WORKDIR="${output_run_dir}" \
        SIM_MODE="${SIM_MODE}" \
        RUN_MODE="${RUN_MODE-}" \
        CMD="${CMD-}" \
        "${dc_cmd[@]}" "$@"
}

# shellcheck disable=SC2317
cleanup_all() {
    set +e
    dc down --remove-orphans >/dev/null 2>&1 || true
    CMD="bash /aichallenge/utils/fix_ownership.bash ${host_uid} ${host_gid} ${output_root} ${run_id}"
    dc run --rm --no-deps autoware-command >/dev/null 2>&1 || true
    dc down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup_all EXIT INT TERM

# Simulator log should be under output/<run_rel>/awsim.log (not under dN/).
output_run_dir="${output_root}/${run_rel}"
dc up -d --force-recreate simulator
CMD="env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-ready" dc run --rm --no-deps autoware-command

mkdir -p "output/${run_rel}/d${domain_id}"
output_run_dir="${output_root}/${run_rel}/d${domain_id}"
echo "OUTPUT: output/${run_rel}/d${domain_id} (container: ${output_run_dir})"
dc up -d --force-recreate autoware

CMD="env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-finish" dc run --rm --no-deps autoware-command
exit 0
