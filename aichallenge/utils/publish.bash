#!/bin/bash

# shellcheck disable=SC2317

# Help function to display usage
usage() {
    echo "Usage: $0 [OPTION]"
    echo "Options:"
    echo "  wait-admin-state    Wait for /admin/awsim/state (prefer manager service if available)"
    echo "  wait-admin-finish   Wait up to 600s for FinishALL or Terminate on /admin/awsim/state"
    echo "  wait-admin-ready    Wait up to 600s for any /admin/awsim/state message"
    echo "  reset-awsim         Reset AWSIM (topic publish)"
    echo "  help                Display this help message"
}

# Function to capture screen
run_with_timeout() {
    local label="$1"
    local timeout_s="$2"
    shift 2

    echo "${label}..."
    timeout "${timeout_s}s" "$@" >/dev/null 2>&1
    local rc=$?

    if [ "$rc" -eq 124 ]; then
        echo "Warning: ${label} timed out after ${timeout_s} seconds"
        return 124
    fi
    if [ "$rc" -ne 0 ]; then
        echo "Error: ${label} failed (rc=$rc)"
        return "$rc"
    fi

    echo "${label} successfully"
    return 0
}

wait_for_topic_once() {
    local label="$1"
    local timeout_s="$2"
    local topic="$3"
    local type="$4"
    shift 4

    run_with_timeout "${label}" "${timeout_s}" ros2 topic echo "${topic}" "${type}" --once "$@"
}

service_exists() {
    local name="$1"
    ros2 service list 2>/dev/null | grep -Fxq "${name}"
}

service_call_succeeded() {
    # ros2 service call output format varies by ROS 2 version.
    # Accept both YAML-like and Python repr styles:
    #   success: True
    #   success: true
    # (legacy) the former state-manager service response format
    local out="$1"
    printf '%s\n' "${out}" | grep -Eiq 'success[[:space:]]*[:=][[:space:]]*(true|True|1)\b'
}

yaml_string_list() {
    # Print YAML list of strings: ["a","b","c"]
    # NOTE: This is minimal and assumes elements do not contain double quotes.
    local -a items=("$@")
    if [ "${#items[@]}" -eq 0 ]; then
        printf '[]'
        return 0
    fi
    local out='['
    local i
    for ((i = 0; i < ${#items[@]}; i++)); do
        if [ "$i" -gt 0 ]; then
            out+=','
        fi
        out+="\"${items[$i]}\""
    done
    out+=']'
    printf '%s' "${out}"
}

extract_string_data() {
    # Extract the `data:` field from std_msgs/msg/String output.
    # Examples:
    #   data: "FinishALL"
    #   data: 'FinishALL'
    #   data: FinishALL
    local out="$1"
    printf '%s\n' "$out" | sed -n \
        -e 's/^data:[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' \
        -e "s/^data:[[:space:]]*'\\(.*\\)'[[:space:]]*$/\\1/p" \
        -e 's/^data:[[:space:]]*//p' | head -n 1
}

wait_admin_state() {
    local timeout_s="${AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_STATE:-${AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_STATUS:-60}}"
    local expected=("$@")
    # Wait via topic.
    local deadline now left out rc status last
    deadline=$(($(date +%s) + timeout_s))
    last=""

    local topic="${AIC_AWSIM_ADMIN_STATE_TOPIC:-/admin/awsim/state}"
    local label="${AIC_ADMIN_WAIT_LABEL:-admin}"
    local expected_str=""
    if [ "${#expected[@]}" -eq 0 ]; then
        expected_str="any"
    else
        expected_str="$(
            IFS='|'
            echo "${expected[*]}"
        )"
    fi

    echo "[${label}] wait until: topic=${topic} expected=${expected_str} timeout=${timeout_s}s"
    while :; do
        now=$(date +%s)
        left=$((deadline - now))
        if [ "${left}" -le 0 ]; then
            echo "[${label}][WARN] timed out: topic=${topic} expected=${expected_str} timeout=${timeout_s}s"
            if [ -n "${last}" ]; then
                echo "[${label}] last state: ${last}"
            fi
            return 124
        fi

        out=$(timeout "${left}s" ros2 topic echo "${topic}" "std_msgs/msg/String" --once \
            --qos-history keep_last --qos-depth 1 \
            --qos-durability transient_local --qos-reliability reliable 2>/dev/null)
        rc=$?
        if [ "$rc" -eq 124 ]; then
            echo "[${label}][WARN] timed out: topic=${topic} expected=${expected_str} timeout=${timeout_s}s"
            if [ -n "${last}" ]; then
                echo "[${label}] last state: ${last}"
            fi
            return 124
        fi
        if [ "$rc" -ne 0 ]; then
            echo "[${label}][ERROR] wait failed: topic=${topic} (rc=$rc)"
            return "$rc"
        fi

        status=$(extract_string_data "${out}")
        if [ -z "${status}" ]; then
            continue
        fi

        if [ "${status}" != "${last}" ]; then
            echo "[${label}] state: ${status}"
            last="${status}"
        fi

        if [ "${#expected[@]}" -eq 0 ]; then
            echo "[${label}] done (received any state)"
            return 0
        fi
        for e in "${expected[@]}"; do
            if [ "${status}" = "${e}" ] || [ "${status,,}" = "${e,,}" ]; then
                echo "[${label}] done (matched: ${e})"
                return 0
            fi
        done

        sleep 1
    done
}

wait_admin_finish() {
    local timeout_s="${AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_FINISH:-600}"
    AIC_ADMIN_WAIT_LABEL="${AIC_ADMIN_WAIT_LABEL:-admin-finish}" \
        AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_STATE="${timeout_s}" \
        wait_admin_state FinishALL Terminate
}

wait_admin_ready() {
    local timeout_s="${AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_READY:-600}"
    AIC_ADMIN_WAIT_LABEL="${AIC_ADMIN_WAIT_LABEL:-admin-ready}" \
        AIC_TOPIC_WAIT_TIMEOUT_S_ADMIN_STATE="${timeout_s}" \
        wait_admin_state
}

reset_awsim() {
    run_with_timeout "Resetting AWSIM" 5 \
        ros2 topic pub --once "/admin/awsim/reset" "std_msgs/msg/Empty" "{}"
}

# Check if an argument was provided
if [ $# -eq 0 ]; then
    usage >&2
    exit 1
fi

rc=0

# Process based on provided argument
case "$1" in
wait-admin-state)
    wait_admin_state "${@:2}"
    rc=$?
    ;;
wait-admin-finish)
    wait_admin_finish
    rc=$?
    ;;
wait-admin-ready)
    wait_admin_ready
    rc=$?
    ;;
reset-awsim)
    reset_awsim
    rc=$?
    ;;
help)
    usage
    rc=0
    ;;
*)
    echo "Error: Invalid option '$1'" >&2
    usage >&2
    rc=2
    ;;
esac

exit "$rc"
