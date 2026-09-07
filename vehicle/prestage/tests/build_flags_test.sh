#!/usr/bin/env bash
# build_autoware.bash が SYMLINK_INSTALL でフラグを切り替えることを、
# コンテナ外（ROS 無し）で DRY_RUN 経由で検証する。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TARGET="${REPO_ROOT}/aichallenge/build_autoware.bash"

fails=0
check() { # $1=label $2=expected-substring-or-! $3=actual
    if [ "${2:0:1}" = "!" ]; then
        if printf '%s' "$3" | grep -q -- "${2:1}"; then
            echo "FAIL: $1 (unexpectedly contains '${2:1}')"
            fails=$((fails + 1))
        else
            echo "PASS: $1"
        fi
    elif printf '%s' "$3" | grep -q -- "$2"; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 (missing '$2') got: $3"
        fails=$((fails + 1))
    fi
}

out_default=$(cd "${REPO_ROOT}/aichallenge" && DRY_RUN=1 bash "${TARGET}" 2>&1)
check "default keeps --symlink-install" "--symlink-install" "${out_default}"
check "default keeps allow-overriding" "--allow-overriding gyro_odometer" "${out_default}"

out_off=$(cd "${REPO_ROOT}/aichallenge" && DRY_RUN=1 SYMLINK_INSTALL=0 bash "${TARGET}" 2>&1)
check "SYMLINK_INSTALL=0 drops --symlink-install" '!--symlink-install' "${out_off}"
check "SYMLINK_INSTALL=0 keeps Release" "DCMAKE_BUILD_TYPE=Release" "${out_off}"

out_on=$(cd "${REPO_ROOT}/aichallenge" && DRY_RUN=1 SYMLINK_INSTALL=1 bash "${TARGET}" 2>&1)
check "SYMLINK_INSTALL=1 keeps --symlink-install" "--symlink-install" "${out_on}"

# --- G3: refuse a --symlink-install build while a prestaged team is staged ---
STAGED_DIR="$(mktemp -d)"
mkdir -p "${STAGED_DIR}/workspace"
touch "${STAGED_DIR}/workspace/.staged_team"

out_staged=$(cd "${STAGED_DIR}" && DRY_RUN=1 bash "${TARGET}" 2>&1)
rc_staged=$?
if [ "${rc_staged}" -eq 1 ]; then
    echo "PASS: staged team + default SYMLINK_INSTALL exits 1"
else
    echo "FAIL: staged team + default SYMLINK_INSTALL exits 1 (got rc=${rc_staged})"
    fails=$((fails + 1))
fi
check "staged team error message mentions staged" "staged" "${out_staged}"

out_staged_off=$(cd "${STAGED_DIR}" && DRY_RUN=1 SYMLINK_INSTALL=0 bash "${TARGET}" 2>&1)
rc_staged_off=$?
if [ "${rc_staged_off}" -eq 0 ]; then
    echo "PASS: staged team + SYMLINK_INSTALL=0 exits 0"
else
    echo "FAIL: staged team + SYMLINK_INSTALL=0 exits 0 (got rc=${rc_staged_off})"
    fails=$((fails + 1))
fi
check "staged team + SYMLINK_INSTALL=0 still prints colcon command" "colcon build" "${out_staged_off}"

rm -rf "${STAGED_DIR}"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
