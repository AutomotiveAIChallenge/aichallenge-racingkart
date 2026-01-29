#!/usr/bin/env bash

# Shared evaluation flow (sequence) used by:
# - aichallenge/run_evaluation.bash (single-container / nohup backend)
# - aichallenge/utils/run_sim_eval.bash (docker compose backend)
#
# This file is meant to be sourced. It defines functions only.
#
# Required functions (must be defined by the caller before invoking the flow):
# - aic_eval_log
# - aic_eval_run_or_exit <label> <command...>
# - aic_eval_backend_start_simulator
# - aic_eval_backend_wait_sim_ready
# - aic_eval_backend_start_autoware
# - aic_eval_backend_request_initialpose
# - aic_eval_backend_request_control
# - aic_eval_backend_wait_sim_finish
# - aic_eval_backend_convert_result_best_effort
# - aic_eval_backend_cleanup_domain
#
# Optional functions:
# - aic_eval_backend_move_window_best_effort
# - aic_eval_backend_start_capture_best_effort
# - aic_eval_backend_start_rosbag_best_effort

aic_eval_flow__have_fn() {
    command -v "$1" >/dev/null 2>&1
}

aic_eval_flow__require_fn() {
    local fn="$1"
    if ! aic_eval_flow__have_fn "${fn}"; then
        echo "[eval_flow][ERROR] missing required function: ${fn}" >&2
        return 2
    fi
}

aic_eval_flow__call_optional() {
    local fn="$1"
    shift
    if aic_eval_flow__have_fn "${fn}"; then
        "${fn}" "$@"
    fi
}

aic_eval_flow_run_domain() {
    local domain_id="${1-}"
    local sim_domain_id="${2:-0}"
    local result_wait_seconds="${3:-10}"
    local capture_enabled="${4:-false}"
    local rosbag_enabled="${5:-false}"

    [ -n "${domain_id}" ] || {
        echo "[eval_flow][ERROR] domain_id is required" >&2
        return 2
    }

    export AIC_EVAL_DOMAIN_ID="${domain_id}"
    export AIC_EVAL_SIM_DOMAIN_ID="${sim_domain_id}"
    export AIC_EVAL_RESULT_WAIT_SECONDS="${result_wait_seconds}"
    export AIC_EVAL_CAPTURE_ENABLED="${capture_enabled}"
    export AIC_EVAL_ROSBAG_ENABLED="${rosbag_enabled}"

    aic_eval_flow__require_fn aic_eval_log
    aic_eval_flow__require_fn aic_eval_run_or_exit

    aic_eval_flow__require_fn aic_eval_backend_start_simulator
    aic_eval_flow__require_fn aic_eval_backend_wait_sim_ready
    aic_eval_flow__require_fn aic_eval_backend_start_autoware
    aic_eval_flow__require_fn aic_eval_backend_request_initialpose
    aic_eval_flow__require_fn aic_eval_backend_request_control
    aic_eval_flow__require_fn aic_eval_backend_wait_sim_finish
    aic_eval_flow__require_fn aic_eval_backend_convert_result_best_effort
    aic_eval_flow__require_fn aic_eval_backend_cleanup_domain

    aic_eval_log "--- Starting Evaluation ---"
    aic_eval_log "DOMAIN_ID=${AIC_EVAL_DOMAIN_ID} ROSBAG=${AIC_EVAL_ROSBAG_ENABLED} CAPTURE=${AIC_EVAL_CAPTURE_ENABLED}"

    aic_eval_run_or_exit "Start simulator" aic_eval_backend_start_simulator
    aic_eval_run_or_exit "AWSIM readiness check (/clock)" aic_eval_backend_wait_sim_ready

    aic_eval_run_or_exit "Start Autoware" aic_eval_backend_start_autoware
    sleep "${AIC_EVAL_AUTOWARE_START_SLEEP_SECONDS:-3}" || true

    aic_eval_flow__call_optional aic_eval_backend_move_window_best_effort || true

    aic_eval_run_or_exit "Initial pose set" aic_eval_backend_request_initialpose
    aic_eval_run_or_exit "Control request" aic_eval_backend_request_control

    if [ "${AIC_EVAL_CAPTURE_ENABLED}" = "true" ]; then
        aic_eval_flow__call_optional aic_eval_backend_start_capture_best_effort || true
    fi
    if [ "${AIC_EVAL_ROSBAG_ENABLED}" = "true" ]; then
        aic_eval_flow__call_optional aic_eval_backend_start_rosbag_best_effort || true
    fi

    aic_eval_flow__call_optional aic_eval_backend_wait_before_finish || true

    aic_eval_backend_wait_sim_finish || true
    aic_eval_backend_convert_result_best_effort || true
    aic_eval_backend_cleanup_domain || true
}
