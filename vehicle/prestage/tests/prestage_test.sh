#!/usr/bin/env bash
# prestage_all.sh を、ダウンロードとビルドをスタブに差し替えて検証する。
# ネットワークも docker も使わない。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRESTAGE="${SCRIPT_DIR}/../prestage_all.sh"
MANIFEST_PY="${SCRIPT_DIR}/../manifest.py"
WORK="$(mktemp -d)"
trap 'fusermount -u "${WORK}/mnt" 2>/dev/null; fusermount -u "${WORK}/mnt2" 2>/dev/null; fusermount -u "${WORK}/mnt3" 2>/dev/null; fusermount -u "${WORK}/mnt4" 2>/dev/null; fusermount -u "${WORK}/mnt5" 2>/dev/null; fusermount -u "${WORK}/mnt6" 2>/dev/null; rm -rf "${WORK}"' EXIT INT TERM

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

# --- 1チームのビルドが install/ を作らずに exit 0 しても、他チームは止まらないこと ---
mkdir -p "${WORK}/vault2" "${WORK}/mnt2"
printf 'tgood\tuser-good\ntbad\tuser-bad\n' >"${WORK}/teams2.tsv"

cat >"${WORK}/fake_build_partial.sh" <<'STUB'
#!/usr/bin/env bash
# Emulates a build that exits 0 but, for one team, silently produces no
# install/ at all (e.g. a swallowed cmake error). Used to prove that a single
# team's build failure does not abort the remaining teams.
set -eo pipefail
ws="${FAKE_WS}/aichallenge/workspace"
mkdir -p "${ws}/build/pkg"
if [ -d "${ws}/src/aichallenge_submit/pkg_user-bad" ]; then
    exit 0
fi
mkdir -p "${ws}/install/pkg/lib"
echo "built artifact" >"${ws}/install/pkg/lib/libpkg.so"
echo "setup" >"${ws}/install/setup.bash"
STUB
chmod +x "${WORK}/fake_build_partial.sh"

gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault2" >/dev/null 2>&1

partial_out=$(PRESTAGE_BUILD_CMD="FAKE_WS=${WS} ${WORK}/fake_build_partial.sh" \
    "${PRESTAGE}" --vault "${WORK}/vault2" --teams "${WORK}/teams2.tsv" 2>&1)
partial_rc=$?
echo "    ${partial_out//$'\n'/$'\n    '}"
expect_eq "partial run exits non-zero (one team failed)" "1" "${partial_rc}"

gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault2" "${WORK}/mnt2"
expect_eq "bad team marked failed" "failed" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt2/manifest.json" --team-id tbad --field build_status)"
expect_eq "good team still ok despite bad team" "ok" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt2/manifest.json" --team-id tgood --field build_status)"
expect_eq "good team install archive exists" "yes" \
    "$([ -f "${WORK}/mnt2/team_tgood/install.tar.zst" ] && echo yes || echo no)"
expect_eq "bad team has no install archive" "no" \
    "$([ -f "${WORK}/mnt2/team_tbad/install.tar.zst" ] && echo yes || echo no)"
fusermount -u "${WORK}/mnt2"

# --- usage は PRESTAGE_USERNAME / PRESTAGE_PASSWORD を案内し、裸の USERNAME/PASSWORD は使わないこと ---
usage_out=$("${PRESTAGE}" --help 2>&1 || true)
expect_eq "usage mentions PRESTAGE_USERNAME" "yes" \
    "$(printf '%s' "${usage_out}" | grep -q 'PRESTAGE_USERNAME' && echo yes || echo no)"
expect_eq "usage mentions PRESTAGE_PASSWORD" "yes" \
    "$(printf '%s' "${usage_out}" | grep -q 'PRESTAGE_PASSWORD' && echo yes || echo no)"
expect_eq "usage does not mention bare USERNAME" "no" \
    "$(printf '%s' "${usage_out}" | grep -qE '\bUSERNAME\b' && echo yes || echo no)"
expect_eq "usage does not mention bare PASSWORD" "no" \
    "$(printf '%s' "${usage_out}" | grep -qE '\bPASSWORD\b' && echo yes || echo no)"

# --- 4 列目 (label) があっても、submission_id が空でも受け付けること ---
mkdir -p "${WORK}/vault3" "${WORK}/mnt3"
printf 'general-01\tuser-a\t\tShibaura Univ\nstudent-02\tuser-b\tsub-b\tChiba Tech (student)\n' >"${WORK}/teams3.tsv"
gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault3" >/dev/null 2>&1
label_out=$("${PRESTAGE}" --vault "${WORK}/vault3" --teams "${WORK}/teams3.tsv" 2>&1)
label_rc=$?
echo "    ${label_out//$'\n'/$'\n    '}"
expect_eq "labelled tsv exits 0" "0" "${label_rc}"
gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault3" "${WORK}/mnt3"
expect_eq "general-01 ok (empty submission_id + label)" "ok" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt3/manifest.json" --team-id general-01 --field build_status)"
expect_eq "student-02 keeps its submission_id" "sub-b" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt3/manifest.json" --team-id student-02 --field submission_id)"
fusermount -u "${WORK}/mnt3"

# --- F1: build/download コマンドが teams.tsv の stdin を横取りしないこと ---
# BUILD_CMD が cat >/dev/null で stdin を吸い込んでも、while read ループの
# 残り行が消費されてはならない（3 チーム全部処理されること）。
mkdir -p "${WORK}/vault4" "${WORK}/mnt4"
printf 'f1-a\tuser-a\nf1-b\tuser-b\nf1-c\tuser-c\n' >"${WORK}/teams4.tsv"

cat >"${WORK}/fake_build_drain.sh" <<'STUB'
#!/usr/bin/env bash
# stdin を丸ごと読み捨てる。teams.tsv が build コマンドの stdin に
# 漏れていないかを検出するためのスタブ。
set -eo pipefail
cat >/dev/null
ws="${FAKE_WS}/aichallenge/workspace"
mkdir -p "${ws}/install/pkg/lib" "${ws}/build/pkg"
echo "built artifact" >"${ws}/install/pkg/lib/libpkg.so"
echo "setup" >"${ws}/install/setup.bash"
STUB
chmod +x "${WORK}/fake_build_drain.sh"

gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault4" >/dev/null 2>&1

drain_out=$(PRESTAGE_BUILD_CMD="FAKE_WS=${WS} ${WORK}/fake_build_drain.sh" \
    "${PRESTAGE}" --vault "${WORK}/vault4" --teams "${WORK}/teams4.tsv" 2>&1)
drain_rc=$?
echo "    ${drain_out//$'\n'/$'\n    '}"
expect_eq "stdin-draining build still exits 0" "0" "${drain_rc}"
expect_eq "stdin-draining build: summary is 3 ok" "yes" \
    "$(printf '%s' "${drain_out}" | grep -q '3 ok' && echo yes || echo no)"

gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault4" "${WORK}/mnt4"
for t in f1-a f1-b f1-c; do
    expect_eq "${t} ok despite stdin-draining build" "ok" \
        "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt4/manifest.json" --team-id "${t}" --field build_status)"
done
fusermount -u "${WORK}/mnt4"

# --- F2: タブの連続 (空の submission_id 列) が後続列を巻き込んで潰れないこと ---
mkdir -p "${WORK}/vault5" "${WORK}/mnt5"
: >"${WORK}/fake_dl_log5"
cat >"${WORK}/fake_download_logged.sh" <<'STUB'
#!/usr/bin/env bash
set -eo pipefail
printf '%s\n' "$*" >>"${FAKE_DL_LOG}"
dest=""
while [ $# -gt 0 ]; do
    case "$1" in
    --dest-file) dest="$2"; shift 2 ;;
    *) shift ;;
    esac
done
tmp=$(mktemp -d)
mkdir -p "${tmp}/aichallenge_submit/pkg"
echo "source" >"${tmp}/aichallenge_submit/pkg/node.cpp"
mkdir -p "$(dirname "${dest}")"
tar czf "${dest}" -C "${tmp}" aichallenge_submit
rm -rf "${tmp}"
STUB
chmod +x "${WORK}/fake_download_logged.sh"

printf 'general-01\tuser-a\t\tShibaura Univ\nstudent-02\tuser-b\tsub-b\tChiba Tech\n' >"${WORK}/teams5.tsv"
gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault5" >/dev/null 2>&1

FAKE_DL_LOG="${WORK}/fake_dl_log5" PRESTAGE_DOWNLOAD_CMD="${WORK}/fake_download_logged.sh" \
    "${PRESTAGE}" --vault "${WORK}/vault5" --teams "${WORK}/teams5.tsv" >/dev/null 2>&1
expect_eq "empty submission_id: called with --latest" "yes" \
    "$(grep -F 'general-01' "${WORK}/fake_dl_log5" | grep -q -- '--latest' && echo yes || echo no)"
expect_eq "empty submission_id: NOT called with --submission-id" "no" \
    "$(grep -F 'general-01' "${WORK}/fake_dl_log5" | grep -q -- '--submission-id' && echo yes || echo no)"
gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault5" "${WORK}/mnt5"
expect_eq "empty submission_id: user_id not swallowed into submission_id" "" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt5/manifest.json" --team-id general-01 --field submission_id 2>/dev/null || true)"
expect_eq "non-empty submission_id: called with --submission-id sub-b" "yes" \
    "$(grep -F 'student-02' "${WORK}/fake_dl_log5" | grep -q -- '--submission-id sub-b' && echo yes || echo no)"
fusermount -u "${WORK}/mnt5"

# --- F3: CRLF 改行の TSV を扱えること (Excel/Windows エクスポート対策) ---
mkdir -p "${WORK}/vault6" "${WORK}/mnt6"
: >"${WORK}/fake_dl_log6"
printf 'crlf-01\tuser-c\r\n' >"${WORK}/teams6.tsv"
gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault6" >/dev/null 2>&1

FAKE_DL_LOG="${WORK}/fake_dl_log6" PRESTAGE_DOWNLOAD_CMD="${WORK}/fake_download_logged.sh" \
    "${PRESTAGE}" --vault "${WORK}/vault6" --teams "${WORK}/teams6.tsv" >/dev/null 2>&1
gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault6" "${WORK}/mnt6"
expect_eq "crlf-01 ok" "ok" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt6/manifest.json" --team-id crlf-01 --field build_status)"
expect_eq "crlf-01: user-id has no trailing CR" "yes" \
    "$(grep -F 'crlf-01' "${WORK}/fake_dl_log6" | grep -q -- '--user-id user-c$' && echo yes || echo no)"
fusermount -u "${WORK}/mnt6"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
