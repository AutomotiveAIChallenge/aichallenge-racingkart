#!/usr/bin/env bash
# unstage_team.sh: 平文とビルド成果物が消え、output は退避され、
# 残存があれば非ゼロで報告することを検証する。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNSTAGE="${SCRIPT_DIR}/../unstage_team.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT INT TERM

fails=0
expect_eq() { # $1=label $2=expected $3=actual
    if [ "$2" = "$3" ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 (expected '$2' got '$3')"
        fails=$((fails + 1))
    fi
}

ROOT="${WORK}/ws"
WS="${ROOT}/aichallenge/workspace"
mkdir -p "${WS}/install/pkg" "${WS}/build/pkg" "${WS}/log" \
    "${WS}/src/aichallenge_submit/pkg" "${ROOT}/output/20260813-120000"
echo "team source" >"${WS}/src/aichallenge_submit/pkg/node.cpp"
echo "artifact" >"${WS}/install/pkg/lib.so"
echo "rosbag" >"${ROOT}/output/20260813-120000/result-summary.json"
printf 't01 2026-08-13T12:00:00+09:00\n' >"${WS}/.staged_team"

export PRESTAGE_WORKSPACE_ROOT="${ROOT}"
# Stub docker with `false` so `image inspect` reports "absent" and the removal
# branch is skipped entirely. `true` would make every inspect succeed and log a
# removal that never happened.
export PRESTAGE_DOCKER_CMD="false"

out=$("${UNSTAGE}" --keep-output "${WORK}/archive" --yes 2>&1)
rc=$?
# shellcheck disable=SC2001 # prefixing every line needs a regex, not just substring replace
echo "${out}" | sed 's/^/    /'
expect_eq "unstage exits 0" "0" "${rc}"
expect_eq "install removed" "no" "$([ -e "${WS}/install" ] && echo yes || echo no)"
expect_eq "build removed" "no" "$([ -e "${WS}/build" ] && echo yes || echo no)"
expect_eq "log removed" "no" "$([ -e "${WS}/log" ] && echo yes || echo no)"
expect_eq "submit src removed" "no" "$([ -e "${WS}/src/aichallenge_submit" ] && echo yes || echo no)"
expect_eq "marker removed" "no" "$([ -e "${WS}/.staged_team" ] && echo yes || echo no)"
expect_eq "src dir itself kept" "yes" "$([ -d "${WS}/src" ] && echo yes || echo no)"
expect_eq "output moved to archive" "yes" \
    "$([ -f "${WORK}/archive/t01/20260813-120000/result-summary.json" ] && echo yes || echo no)"
expect_eq "output cleared" "0" "$(find "${ROOT}/output" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')"
expect_eq "no team plaintext left" "yes" \
    "$(grep -rl 'team source' "${ROOT}" >/dev/null 2>&1 && echo no || echo yes)"

# --- 残存があれば非ゼロで報告する ---
mkdir -p "${WS}/install"
echo leftover >"${WS}/install/x"
chmod 500 "${WS}" # make removal fail
"${UNSTAGE}" --yes >/dev/null 2>&1
rc=$?
chmod 700 "${WS}"
expect_eq "reports leftovers with non-zero exit" "1" "$([ "${rc}" -ne 0 ] && echo 1 || echo 0)"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
