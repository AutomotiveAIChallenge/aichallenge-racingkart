#!/usr/bin/env bash
# image_transfer.sh: export は docker save の出力を zstd で固め、import は zstd 展開を docker load に流すこと。
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XFER="${SCRIPT_DIR}/../image_transfer.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT INT TERM
fails=0
expect_eq() { if [ "$2" = "$3" ]; then echo "PASS: $1"; else
    echo "FAIL: $1 (expected '$2' got '$3')"
    fails=$((fails + 1))
fi; }

# docker スタブ: save は固定バイト列を吐き、load は stdin を記録する
cat >"${WORK}/fake_docker.sh" <<'STUB'
#!/usr/bin/env bash
case "$1" in
save) printf 'IMAGE-BYTES-%s' "$2" ;;
load) cat >"${FAKE_LOADED}" ; echo "Loaded image: fake" ;;
image) echo "sha256:fakeid" ;;
*) exit 1 ;;
esac
STUB
chmod +x "${WORK}/fake_docker.sh"
export PRESTAGE_DOCKER_CMD="${WORK}/fake_docker.sh"
export PRESTAGE_IMAGE="aichallenge-2025-dev"
export FAKE_LOADED="${WORK}/loaded.bin"

out=$("${XFER}" export "${WORK}/img.tar.zst" 2>&1)
rc=$?
expect_eq "export exits 0" "0" "${rc}"
expect_eq "export wrote zstd" "IMAGE-BYTES-aichallenge-2025-dev" "$(zstd -dc "${WORK}/img.tar.zst")"
expect_eq "export prints image id" "yes" "$(printf '%s' "${out}" | grep -q 'sha256:fakeid' && echo yes || echo no)"

out=$("${XFER}" import "${WORK}/img.tar.zst" 2>&1)
rc=$?
expect_eq "import exits 0" "0" "${rc}"
expect_eq "import fed docker load" "IMAGE-BYTES-aichallenge-2025-dev" "$(cat "${FAKE_LOADED}")"

"${XFER}" import "${WORK}/missing.tar.zst" >/dev/null 2>&1
expect_eq "import rejects missing file" "1" "$?"
"${XFER}" bogus x >/dev/null 2>&1
expect_eq "unknown verb exits 2" "2" "$?"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
