#!/bin/bash
# Pack aichallenge/workspace/src/aichallenge_submit into submit/aichallenge_submit.tar.gz.
set -euo pipefail

# Work from the repository root so the script can be called from any directory.
cd "$(dirname "${BASH_SOURCE[0]}")"

out="submit/aichallenge_submit.tar.gz"
# Upload limit of the submission site (MB). Override with AIC_SUBMIT_MAX_MB if it changes.
max_mb="${AIC_SUBMIT_MAX_MB:-20}"

mkdir -p submit
tar zcvf "${out}" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C ./aichallenge/workspace/src aichallenge_submit

size_bytes="$(wc -c <"${out}")"
echo "[INFO] ${out}: ${size_bytes} bytes"
if [ "${size_bytes}" -gt $((max_mb * 1024 * 1024)) ]; then
    echo "[WARN] ${out} is larger than ${max_mb} MB and may be rejected by the submission site." >&2
fi
