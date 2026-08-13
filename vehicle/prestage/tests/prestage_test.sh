#!/usr/bin/env bash
# prestage_all.sh を、ダウンロードとビルドをスタブに差し替えて検証する。
# ネットワークも docker も使わない。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRESTAGE="${SCRIPT_DIR}/../prestage_all.sh"
MANIFEST_PY="${SCRIPT_DIR}/../manifest.py"
WORK="$(mktemp -d)"
trap 'fusermount -u "${WORK}/mnt" 2>/dev/null; rm -rf "${WORK}"' EXIT INT TERM

fails=0
expect_eq() { # $1=label $2=expected $3=actual
    if [ "$2" = "$3" ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 (expected '$2' got '$3')"
        fails=$((fails + 1))
    fi
}

# --- ダミー workspace ---
WS="${WORK}/ws"
mkdir -p "${WS}/aichallenge/workspace/src" "${WORK}/vault" "${WORK}/mnt"

# --- スタブ: ダウンロードは中身が team_id で分かる tar.gz を吐く ---
cat >"${WORK}/fake_download.sh" <<'STUB'
#!/usr/bin/env bash
# Emulates: download_submission.py --latest --dest-file <path> --user-id <id>
set -eo pipefail
dest=""; user=""
while [ $# -gt 0 ]; do
    case "$1" in
    --dest-file) dest="$2"; shift 2 ;;
    --user-id) user="$2"; shift 2 ;;
    *) shift ;;
    esac
done
tmp=$(mktemp -d)
mkdir -p "${tmp}/aichallenge_submit/pkg_${user}"
echo "source of ${user}" >"${tmp}/aichallenge_submit/pkg_${user}/node.cpp"
mkdir -p "$(dirname "${dest}")"
tar czf "${dest}" -C "${tmp}" aichallenge_submit
rm -rf "${tmp}"
STUB
chmod +x "${WORK}/fake_download.sh"

# --- スタブ: ビルドは install/ を捏造する ---
cat >"${WORK}/fake_build.sh" <<'STUB'
#!/usr/bin/env bash
# Emulates the containerised colcon build: produces workspace/install/
set -eo pipefail
ws="${FAKE_WS}/aichallenge/workspace"
mkdir -p "${ws}/install/pkg/lib" "${ws}/build/pkg"
echo "built artifact" >"${ws}/install/pkg/lib/libpkg.so"
echo "setup" >"${ws}/install/setup.bash"
STUB
chmod +x "${WORK}/fake_build.sh"

# --- teams.tsv ---
printf 't01\tuser-a\nt02\tuser-b\n' >"${WORK}/teams.tsv"

printf 'testpass\n' >"${WORK}/pw"
gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault" >/dev/null 2>&1

export PRESTAGE_PASSFILE="${WORK}/pw"
export PRESTAGE_DOWNLOAD_CMD="${WORK}/fake_download.sh"
export PRESTAGE_BUILD_CMD="FAKE_WS=${WS} ${WORK}/fake_build.sh"
export PRESTAGE_IMAGE_ID_CMD="echo sha256:testimage"
export PRESTAGE_WORKSPACE_ROOT="${WS}"

out=$("${PRESTAGE}" --vault "${WORK}/vault" --teams "${WORK}/teams.tsv" 2>&1)
rc=$?
echo "    ${out//$'\n'/$'\n    '}"
expect_eq "prestage exits 0" "0" "${rc}"

# --- ボールトを開けて中身を確認 ---
gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault" "${WORK}/mnt"
expect_eq "manifest exists" "yes" "$([ -f "${WORK}/mnt/manifest.json" ] && echo yes || echo no)"
expect_eq "t01 install archive" "yes" "$([ -f "${WORK}/mnt/team_t01/install.tar.zst" ] && echo yes || echo no)"
expect_eq "t02 install archive" "yes" "$([ -f "${WORK}/mnt/team_t02/install.tar.zst" ] && echo yes || echo no)"
expect_eq "t01 submission kept" "yes" "$([ -f "${WORK}/mnt/team_t01/submission.tar.gz" ] && echo yes || echo no)"
expect_eq "t01 status ok" "ok" "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt/manifest.json" --team-id t01 --field build_status)"
expect_eq "image id recorded" "sha256:testimage" "$(python3 "${MANIFEST_PY}" image-id "${WORK}/mnt/manifest.json")"

# アーカイブ直下が install/ の中身であること
listing=$(tar --zstd -tf "${WORK}/mnt/team_t01/install.tar.zst" | head -1)
expect_eq "archive root is install contents" "yes" "$(printf '%s' "${listing}" | grep -qv '^install/' && echo yes || echo no)"

fusermount -u "${WORK}/mnt"

# --- ボールトが暗号化されていること ---
expect_eq "no plaintext in cipherdir" "yes" \
    "$(grep -rl 'built artifact' "${WORK}/vault" >/dev/null 2>&1 && echo no || echo yes)"

# --- 走行後の workspace に平文が残っていないこと ---
expect_eq "workspace src cleaned" "no" \
    "$([ -e "${WS}/aichallenge/workspace/src/aichallenge_submit" ] && echo yes || echo no)"
expect_eq "workspace install cleaned" "no" \
    "$([ -e "${WS}/aichallenge/workspace/install" ] && echo yes || echo no)"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
