#!/bin/bash
# Wait for nodes to start, then log CPU affinity of all ROS processes.
log_file="${1:-/output/affinity_check.log}"
sleep_sec="${2:-15}"

sleep "${sleep_sec}"

{
    echo "=== CPU affinity check ($(date)) ==="
    ps aux | grep -E 'ros|component|AWSIM' | grep -v grep | awk '{print $2}' | \
        xargs -I{} sh -c \
            'echo -n "PID {}: "; taskset -cp {} 2>/dev/null | sed "s/.*affinity list: /affinity: /"; echo "  cmd: $(ps -p {} -o args= 2>/dev/null)"'
} >> "${log_file}" 2>&1
