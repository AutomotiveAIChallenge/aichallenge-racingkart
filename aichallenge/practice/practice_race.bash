#!/usr/bin/env bash
# Local SIM-final practice: up to 4 submission tarballs race on one AWSIM under the
# SIM-final settings (simulator_scripts/practice-final.sh). Normally run via
#   make practice-4car SUBMISSIONS="a.tar.gz b.tar.gz c.tar.gz d.tar.gz"
# Slot N = ROS_DOMAIN_ID N = start position N (the same mapping as the SIM final).
# Options (environment): see aichallenge/practice/README.md.
set -euo pipefail

cd "$(dirname "$0")/../.."
REPO_ROOT="${PWD}"

# Keep the user's overlays (.env COMPOSE_FILE: gpu / sound) and add the practice-only overlay.
# docker-compose*.yml themselves are not modified.
base_compose="${COMPOSE_FILE-}"
if [ -z "${base_compose}" ] && [ -f .env ]; then
    base_compose="$(sed -n 's/^COMPOSE_FILE=//p' .env | tail -n 1)"
fi
export COMPOSE_FILE="${base_compose:-docker-compose.yml}:aichallenge/practice/compose.practice.yml"

die() {
    echo "[practice] ERROR: $*" >&2
    exit 1
}
log() { echo "[practice] $*" >&2; }

read -r -a TARS <<<"${SUBMISSIONS-}"
N=${#TARS[@]}
CLASS="${CLASS:-s2r}"
HANDICAP="${HANDICAP:-on}"
NPC="${NPC:-0}"
GRID="${GRID:-fixed}"
ROUND="${ROUND:-0}"
SEED="${SEED-}"
VIZ="${VIZ:-0}"
HEADLESS="${HEADLESS:-0}"
PIN="${PIN:-0}"
KEEP="${KEEP:-0}"
START_TIMEOUT="${START_TIMEOUT:-600}" # s, all slots built + launched + control requested
RACE_TIMEOUT="${RACE_TIMEOUT:-900}"   # s, 420 s race + start countdown + result write
TS="${TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
RUN_HOST="output/${TS}"
RUN_CTR="/output/${TS}"

# ---- 0. options -------------------------------------------------------------------
((N >= 1 && N <= 4)) || die "SUBMISSIONS must list 1 to 4 tar.gz files (got ${N})"
[[ ${CLASS} =~ ^(s2r|e2e)$ ]] || die "CLASS must be s2r or e2e"
[[ ${HANDICAP} =~ ^(on|off)$ ]] || die "HANDICAP must be on or off"
[[ ${NPC} =~ ^[0-3]$ ]] || die "NPC must be 0..3"
[[ ${ROUND} =~ ^[0-9]+$ ]] || die "ROUND must be a non-negative integer"
[[ ${GRID} =~ ^(fixed|shuffle|rotate)$ ]] || die "GRID must be fixed, shuffle or rotate"

# ---- 1. tarballs: same layout as ./create_submit_file.bash produces ------------------
# Hygiene only, not a sandbox: a submission's CMake runs arbitrary code at build time anyway.
validate_tar() {
    local t="$1" entries
    [ -f "${t}" ] || die "not found: ${t}"
    entries=$(tar -tzf "${t}") || die "${t} is not a readable tar.gz"
    if grep -qvE '^aichallenge_submit(/|$)' <<<"${entries}"; then
        die "${t}: every entry must be under aichallenge_submit/ (make it with ./create_submit_file.bash)"
    fi
    if grep -qE '(^|/)\.\.(/|$)' <<<"${entries}"; then
        die "${t}: contains a '..' path"
    fi
}
for t in "${TARS[@]}"; do validate_tar "${t}"; done

[ -x aichallenge/simulator/AWSIM/AWSIM.x86_64 ] || die "AWSIM is not installed under aichallenge/simulator/AWSIM"
if [ "${PIN}" = 1 ] && (($(nproc) < 17)); then
    die "PIN=1 pins slot N to 3 cores from CPU 5 up (needs >= 17 CPUs); this host has $(nproc)"
fi

# ---- 2. a leftover stack on domain 0..4 would silently join this race -----------------
for p in "" 1 2 3 4; do
    project_args=()
    [ -n "${p}" ] && project_args=(-p "${p}")
    if [ -n "$(docker compose "${project_args[@]}" ps -q 2>/dev/null)" ]; then
        die "containers are still running in compose project '${p:-default}'; run 'make down' first"
    fi
done

# ---- 3. grid: which tarball sits in which slot ---------------------------------------
case "${GRID}" in
fixed) ORDER=("${TARS[@]}") ;;
rotate)
    r=$((ROUND % N))
    ORDER=("${TARS[@]:r}" "${TARS[@]:0:r}")
    ;;
shuffle)
    SEED="${SEED:-${RANDOM}}"
    mapfile -t ORDER < <(python3 -c 'import random, sys
items = sys.argv[2:]
random.Random(int(sys.argv[1])).shuffle(items)
print("\n".join(items))' "${SEED}" "${TARS[@]}")
    ;;
esac

# ---- 4. one colcon workspace per distinct tarball, cached by its sha256 ---------------
# The workspace = this checkout's system packages + the tarball's aichallenge_submit,
# which is what the eval image builds (Dockerfile, target eval).
workspace_for() {
    local t="$1" key ws stale
    key="$(sha256sum "${t}" | cut -c1-16)"
    ws="${REPO_ROOT}/output/practice/ws/${key}"
    if [ -f "${ws}/.build-ok" ]; then
        stale=$(find aichallenge/workspace/src -path '*/aichallenge_submit' -prune -o \
            -type f -newer "${ws}/.build-ok" -print -quit)
        if [ -z "${stale}" ]; then
            echo "${ws}"
            return 0
        fi
        log "system packages changed since ${key} was built; rebuilding"
    fi
    rm -rf "${ws}"
    mkdir -p "${ws}/src"
    for d in aichallenge/workspace/src/*/; do
        [ "$(basename "${d}")" = aichallenge_submit ] || cp -a "${d}" "${ws}/src/"
    done
    tar -xzf "${t}" --no-same-owner -C "${ws}/src"
    log "building $(basename "${t}") -> ${ws#"${REPO_ROOT}"/}/build.log"
    SLOT_WORKSPACE="${ws}" docker compose run -T --rm --no-deps autoware-slot \
        bash -lc /aichallenge/build_autoware.bash >"${ws}/build.log" 2>&1 ||
        die "build failed for ${t}; see ${ws}/build.log"
    touch "${ws}/.build-ok"
    echo "${ws}"
}
WS=()
for t in "${ORDER[@]}"; do
    ws=$(workspace_for "${t}") || exit 1
    WS+=("${ws}")
done

# ---- 5. manifest: what raced where, under which conditions ---------------------------
mkdir -p "${RUN_HOST}"
python3 - "${RUN_HOST}/practice-manifest.json" "${CLASS}" "${HANDICAP}" "${NPC}" "${GRID}" \
    "${SEED}" "${ROUND}" "${PIN}" "${ORDER[@]}" <<'EOF'
import hashlib, json, os, platform, subprocess, sys

out, cls, handicap, npcs, grid, seed, rnd, pin, *tars = sys.argv[1:]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


manifest = {
    "schema": "practice-manifest-v1",
    "class": cls, "handicap": handicap, "npcs": int(npcs), "grid": grid,
    "seed": seed or None, "round": int(rnd), "pin": pin == "1",
    "repo_commit": git("rev-parse", "HEAD"), "repo_dirty": bool(git("status", "--porcelain")),
    "host": {"cpus": os.cpu_count(), "machine": platform.machine()},
    "slots": [{"slot": i + 1, "ros_domain_id": i + 1, "label": os.path.basename(t),
               "tarball": os.path.abspath(t), "sha256": sha256(t)} for i, t in enumerate(tars)],
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
EOF

# ---- 6. AWSIM on domain 0, then one Autoware per slot on domain = slot -----------------
log "run dir: ${RUN_HOST}  class=${CLASS} handicap=${HANDICAP} npc=${NPC} grid=${GRID}${SEED:+ seed=${SEED}}"
LOG_DIR="${RUN_CTR}" SIM_MODE=practice-final ROS_DOMAIN_ID=0 \
    PRACTICE_CLASS="${CLASS}" PRACTICE_HANDICAP="${HANDICAP}" PRACTICE_NPCS="${NPC}" \
    PRACTICE_VEHICLES="${N}" PRACTICE_HEADLESS="${HEADLESS}" \
    docker compose up -d simulator
for i in "${!ORDER[@]}"; do
    slot=$((i + 1))
    mode=awsim-no-viz
    [ "${VIZ}" = 1 ] && [ "${slot}" = 1 ] && mode=awsim
    taskset_cmd=""
    [ "${PIN}" = 1 ] && taskset_cmd="taskset -c $((5 + 3 * i))-$((7 + 3 * i))"
    log "slot ${slot} (ROS_DOMAIN_ID=${slot}): $(basename "${ORDER[i]}")${taskset_cmd:+ [${taskset_cmd}]}"
    LOG_DIR="${RUN_CTR}" ROS_DOMAIN_ID="${slot}" RUN_MODE="${mode}" SLOT_WORKSPACE="${WS[i]}" \
        SLOT_TASKSET="${taskset_cmd}" \
        docker compose -p "${slot}" up -d autoware-slot
done

# ---- 7. sync start once every slot has requested autonomous control ---------------------
deadline=$((SECONDS + START_TIMEOUT))
for slot in $(seq 1 "${N}"); do
    until grep -qs 'control mode request: success=True' "${RUN_HOST}/d${slot}/autoware.log"; do
        ((SECONDS < deadline)) ||
            die "slot ${slot} did not request control within ${START_TIMEOUT}s; see ${RUN_HOST}/d${slot}/autoware.log (stack left running: make down)"
        sleep 5
    done
    log "slot ${slot} ready"
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
    make --no-print-directory awsim-request-start >/dev/null 2>&1 || true
    grep -qs 'Received /admin/awsim/start' "${RUN_HOST}/awsim.log" && break
    sleep 3
done
grep -qs 'Received /admin/awsim/start' "${RUN_HOST}/awsim.log" ||
    log "WARN: 'Received /admin/awsim/start' not in awsim.log (wording may differ in this AWSIM build); waiting for results anyway"

# ---- 8. wait for AWSIM's result files, summarise, tear down ------------------------------
log "race started; waiting for ${RUN_HOST}/result-summary.json (timeout ${RACE_TIMEOUT}s)"
deadline=$((SECONDS + RACE_TIMEOUT))
until [ -f "${RUN_HOST}/result-summary.json" ]; do
    ((SECONDS < deadline)) || die "no result-summary.json after ${RACE_TIMEOUT}s (stack left running: make down)"
    sleep 10
done
sleep 5 # dN-result-details.json are written right after the summary
python3 aichallenge/practice/practice_summary.py "${RUN_HOST}" --json "${RUN_HOST}/practice-summary.json" |
    tee "${RUN_HOST}/practice-summary.md"
if [ "${KEEP}" = 1 ]; then
    log "KEEP=1: stack left running ('make down' to stop)"
else
    make --no-print-directory down >/dev/null 2>&1 || log "WARN: 'make down' failed; run it by hand"
fi
