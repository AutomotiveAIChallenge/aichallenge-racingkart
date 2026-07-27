#!/bin/bash
set -u
LOG="${1:-/output/d1/autoware.log}"
mkdir -p "$(dirname "$LOG")"
while true; do
    TS=$(date +%s.%N)
    GPU=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | tr -d ' ' || echo ',,,')
    MEM_TOTAL=$(awk '/^MemTotal:/{print $2}' /proc/meminfo)
    MEM_AVAIL=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
    echo "[resource_monitor] ${TS},${GPU},${MEM_TOTAL},${MEM_AVAIL}" >>"$LOG"
    sleep 5
done
