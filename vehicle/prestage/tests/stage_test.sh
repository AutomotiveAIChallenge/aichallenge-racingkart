#!/usr/bin/env bash
# stage_team.sh: 1 チームだけ展開されること、他チームの平文が現れないこと、
# イメージ不一致で中断すること、多重ステージを拒否することを検証する。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="${SCRIPT_DIR}/../stage_team.sh"
MANIFEST_PY="${SCRIPT_DIR}/../manifest.py"
WORK="$(mktemp -d)"
trap 'fusermount -u "${WORK}/build_mnt" 2>/dev/null; rm -rf "${WORK}"' EXIT INT TERM

fails=0
expect_eq() { # $1=label $2=expected $3=actual
    if [ "$2" = "$3" ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 (expected '$2' got '$3')"
        fails=$((fails + 1))
    fi
}

WS="${WORK}/ws/aichallenge/workspace"
mkdir -p "${WS}/src" "${WORK}/vault" "${WORK}/build_mnt"
printf 'testpass\n' >"${WORK}/pw"
gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault" >/dev/null 2>&1

# --- ボールトを直接組み立てる（prestage_all.sh に依存しない） ---
gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault" "${WORK}/build_mnt"
for t in t01 t02; do
    src="${WORK}/src_${t}"
    mkdir -p "${src}/pkg_${t}/lib"
    echo "artifact of ${t}" >"${src}/pkg_${t}/lib/lib.so"
    echo "setup of ${t}" >"${src}/setup.bash"
    mkdir -p "${WORK}/build_mnt/team_${t}"
    tar --zstd -cf "${WORK}/build_mnt/team_${t}/install.tar.zst" -C "${src}" .
done
python3 "${MANIFEST_PY}" init "${WORK}/build_mnt/manifest.json" --image-id "sha256:testimage"
python3 "${MANIFEST_PY}" upsert "${WORK}/build_mnt/manifest.json" --team-id t01 --user-id u1 \
    --submission-id s1 --submitted-at 1 --build-status ok \
    --install-sha256 "$(sha256sum "${WORK}/build_mnt/team_t01/install.tar.zst" | cut -d' ' -f1)"
python3 "${MANIFEST_PY}" upsert "${WORK}/build_mnt/manifest.json" --team-id t02 --user-id u2 \
    --submission-id s2 --submitted-at 2 --build-status failed
fusermount -u "${WORK}/build_mnt"

export PRESTAGE_PASSFILE="${WORK}/pw"
export PRESTAGE_WORKSPACE_ROOT="${WORK}/ws"
export PRESTAGE_IMAGE_ID_CMD="echo sha256:testimage"

# --- 正常系 ---
out=$("${STAGE}" --vault "${WORK}/vault" t01 2>&1)
rc=$?
# shellcheck disable=SC2001 # prefixing every line needs a regex, not just substring replace
echo "${out}" | sed 's/^/    /'
expect_eq "stage exits 0" "0" "${rc}"
expect_eq "install/setup.bash present" "setup of t01" "$(cat "${WS}/install/setup.bash" 2>/dev/null)"
expect_eq "t01 artifact present" "yes" "$([ -f "${WS}/install/pkg_t01/lib/lib.so" ] && echo yes || echo no)"
expect_eq "marker written" "yes" "$(grep -q '^t01 ' "${WS}/.staged_team" && echo yes || echo no)"
expect_eq "vault unmounted after run" "0" "$(mount | grep -c "${WORK}/vault" || true)"

# --- 他チームの平文がどこにも無い ---
expect_eq "no t02 plaintext on disk" "yes" \
    "$(grep -rl 'artifact of t02' "${WORK}/ws" >/dev/null 2>&1 && echo no || echo yes)"

# --- 多重ステージは拒否 ---
"${STAGE}" --vault "${WORK}/vault" t01 >/dev/null 2>&1
expect_eq "second stage refused" "1" "$?"

rm -rf "${WS}/install" "${WS}/.staged_team"

# --- build_status=failed は拒否 ---
"${STAGE}" --vault "${WORK}/vault" t02 >/dev/null 2>&1
expect_eq "failed build refused" "1" "$?"

# --- イメージ不一致は中断 ---
PRESTAGE_IMAGE_ID_CMD="echo sha256:different" "${STAGE}" --vault "${WORK}/vault" t01 >/dev/null 2>&1
expect_eq "image mismatch refused" "1" "$?"
expect_eq "nothing staged on mismatch" "no" "$([ -e "${WS}/install" ] && echo yes || echo no)"

# --- 未知のチームは中断 ---
"${STAGE}" --vault "${WORK}/vault" t99 >/dev/null 2>&1
expect_eq "unknown team refused" "1" "$?"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
