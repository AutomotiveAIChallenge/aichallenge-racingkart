#!/usr/bin/env bash
# Run every prestage test. Requires gocryptfs, fuse and zstd on the host.
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fails=0

for tool in gocryptfs fusermount zstd python3; do
    command -v "${tool}" >/dev/null 2>&1 || {
        echo "SKIP ALL: ${tool} not installed"
        exit 77
    }
done

for t in "${SCRIPT_DIR}"/*_test.sh; do
    echo "=== $(basename "${t}") ==="
    if bash "${t}"; then
        :
    else
        fails=$((fails + 1))
    fi
done

echo "=== download_submission_test.py ==="
if python3 -m unittest discover -s "${SCRIPT_DIR}/../../tests" -p 'download_submission_test.py'; then
    :
else
    fails=$((fails + 1))
fi

if [ "${fails}" -eq 0 ]; then
    echo "ALL SUITES PASS"
else
    echo "${fails} SUITE(S) FAILED"
fi
exit "${fails}"
