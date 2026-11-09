#!/usr/bin/env bash
# Usage: aichallenge/research_tools/bench.sh <label> [duration_sec]
# Restarts AWSIM+Autoware (make dev), records lap times and MPC solver stats.
# Result is printed to stdout; save it under aichallenge/research_tools/results/.
set -u
label="$1"; dur="${2:-290}"
cd "$(dirname "$0")/../.." # repo root
make down >/dev/null 2>&1
make dev >/dev/null 2>&1
logdir=$(ls -td output/20* | head -1)
echo "label=$label logdir=$logdir commit=$(git rev-parse --short HEAD)$(git diff --quiet || echo '+dirty')"
CMD="python3 /aichallenge/research_tools/lap_monitor.py $dur" timeout $((dur + 60)) docker compose run --rm --no-deps autoware-command 2>/dev/null | grep -E "LAP|END"
echo "--- mpc_stats ---"
grep -a "mpc_stats" "$logdir/d1/autoware.log" | sed -E 's/.*\[mpc_stats\]/[mpc_stats]/' | tail -n 40
make down >/dev/null 2>&1
