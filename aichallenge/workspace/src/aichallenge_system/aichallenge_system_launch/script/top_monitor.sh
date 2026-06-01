#!/bin/bash
log_file="${1:-/output/top_monitor.log}"
interval="${2:-5}"

mkdir -p "$(dirname "$log_file")"

while true; do
    echo "=== $(date) ===" >>"$log_file"
    top -b -c -w 500 -n 1 >>"$log_file"
    sleep "$interval"
done
