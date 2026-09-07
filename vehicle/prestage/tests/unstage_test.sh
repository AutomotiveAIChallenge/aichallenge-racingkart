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

# --- G1: output/ containing only the tracked .gitignore must not abort the run ---
ROOT2="${WORK}/ws2"
WS2="${ROOT2}/aichallenge/workspace"
mkdir -p "${WS2}/install/pkg" "${ROOT2}/output"
echo "artifact" >"${WS2}/install/pkg/lib.so"
touch "${ROOT2}/output/.gitignore"
printf 't02 2026-08-13T12:00:00+09:00\n' >"${WS2}/.staged_team"

out2=$(PRESTAGE_WORKSPACE_ROOT="${ROOT2}" PRESTAGE_DOCKER_CMD="false" \
    "${UNSTAGE}" --keep-output "${WORK}/archive2" --yes 2>&1)
rc2=$?
# shellcheck disable=SC2001 # prefixing every line needs a regex, not just substring replace
echo "${out2}" | sed 's/^/    /'
expect_eq "dotfile-only output: exits 0" "0" "${rc2}"
expect_eq "dotfile-only output: install removed" "no" "$([ -e "${WS2}/install" ] && echo yes || echo no)"
expect_eq "dotfile-only output: gitignore kept" "yes" "$([ -f "${ROOT2}/output/.gitignore" ] && echo yes || echo no)"

# --- G1: .gitignore + a run dir -> the run dir is moved, .gitignore stays in place ---
ROOT3="${WORK}/ws3"
WS3="${ROOT3}/aichallenge/workspace"
mkdir -p "${WS3}/install/pkg" "${ROOT3}/output/20260907-1200"
touch "${ROOT3}/output/.gitignore"
echo "rosbag" >"${ROOT3}/output/20260907-1200/result-summary.json"
printf 't03 2026-08-13T12:00:00+09:00\n' >"${WS3}/.staged_team"

out3=$(PRESTAGE_WORKSPACE_ROOT="${ROOT3}" PRESTAGE_DOCKER_CMD="false" \
    "${UNSTAGE}" --keep-output "${WORK}/archive3" --yes 2>&1)
rc3=$?
# shellcheck disable=SC2001 # prefixing every line needs a regex, not just substring replace
echo "${out3}" | sed 's/^/    /'
expect_eq "run-dir output: exits 0" "0" "${rc3}"
expect_eq "run-dir output: moved under archive" "yes" \
    "$([ -f "${WORK}/archive3/t03/20260907-1200/result-summary.json" ] && echo yes || echo no)"
expect_eq "run-dir output: gitignore stays in place" "yes" "$([ -f "${ROOT3}/output/.gitignore" ] && echo yes || echo no)"

# --- G2: on a git checkout, restore the tracked reference submission instead of deleting it ---
ROOT4="${WORK}/ws4"
WS4="${ROOT4}/aichallenge/workspace"
mkdir -p "${WS4}/src/aichallenge_submit/ref" "${WS4}/install/pkg"
echo "reference" >"${WS4}/src/aichallenge_submit/ref/marker.txt"
git -C "${ROOT4}" init -q
git -C "${ROOT4}" -c user.name=t -c user.email=t@t -c commit.gpgsign=false \
    add aichallenge/workspace/src/aichallenge_submit/ref/marker.txt
git -C "${ROOT4}" -c user.name=t -c user.email=t@t -c commit.gpgsign=false commit -q -m init
mkdir -p "${WS4}/src/aichallenge_submit/team_pkg"
echo "print('team')" >"${WS4}/src/aichallenge_submit/team_pkg/node.py"
echo "artifact" >"${WS4}/install/pkg/lib.so"
printf 't04 2026-08-13T12:00:00+09:00\n' >"${WS4}/.staged_team"

out4=$(PRESTAGE_WORKSPACE_ROOT="${ROOT4}" PRESTAGE_DOCKER_CMD="false" "${UNSTAGE}" --yes 2>&1)
rc4=$?
# shellcheck disable=SC2001 # prefixing every line needs a regex, not just substring replace
echo "${out4}" | sed 's/^/    /'
expect_eq "git checkout: unstage exits 0" "0" "${rc4}"
expect_eq "git checkout: team_pkg removed" "no" \
    "$([ -e "${WS4}/src/aichallenge_submit/team_pkg" ] && echo yes || echo no)"
expect_eq "git checkout: reference marker kept" "yes" \
    "$([ -f "${WS4}/src/aichallenge_submit/ref/marker.txt" ] && echo yes || echo no)"
expect_eq "git checkout: install removed" "no" "$([ -e "${WS4}/install" ] && echo yes || echo no)"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
