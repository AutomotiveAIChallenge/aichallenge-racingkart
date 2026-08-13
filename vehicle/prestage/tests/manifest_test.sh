#!/usr/bin/env bash
# manifest.py の CRUD と失敗時の終了コードを検証する。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_PY="${SCRIPT_DIR}/../manifest.py"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT INT TERM
M="${WORK}/manifest.json"

fails=0
expect_eq() { # $1=label $2=expected $3=actual
    if [ "$2" = "$3" ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 (expected '$2' got '$3')"
        fails=$((fails + 1))
    fi
}

python3 "${MANIFEST_PY}" init "${M}" --image-id "sha256:abc"
expect_eq "init records image id" "sha256:abc" "$(python3 "${MANIFEST_PY}" image-id "${M}")"
expect_eq "valid json" "ok" "$(python3 -c "import json,sys; json.load(open('${M}')); print('ok')")"

python3 "${MANIFEST_PY}" upsert "${M}" --team-id t01 --user-id u1 --submission-id s1 \
    --submitted-at 1000 --build-status ok --install-sha256 h1 --submission-sha256 h2
expect_eq "upsert build_status" "ok" "$(python3 "${MANIFEST_PY}" get "${M}" --team-id t01 --field build_status)"
expect_eq "upsert install sha" "h1" "$(python3 "${MANIFEST_PY}" get "${M}" --team-id t01 --field install_sha256)"
expect_eq "upsert submission id" "s1" "$(python3 "${MANIFEST_PY}" get "${M}" --team-id t01 --field submission_id)"

python3 "${MANIFEST_PY}" upsert "${M}" --team-id t01 --user-id u1 --submission-id s2 \
    --submitted-at 2000 --build-status failed --install-sha256 "" --submission-sha256 h3
expect_eq "upsert replaces same team" "failed" "$(python3 "${MANIFEST_PY}" get "${M}" --team-id t01 --field build_status)"
expect_eq "no duplicate team entry" "1" "$(python3 -c "import json; print(len(json.load(open('${M}'))['teams']))")"

python3 "${MANIFEST_PY}" upsert "${M}" --team-id t02 --user-id u2 --submission-id s3 \
    --submitted-at 3000 --build-status ok --install-sha256 h4 --submission-sha256 h5
expect_eq "two teams" "2" "$(python3 -c "import json; print(len(json.load(open('${M}'))['teams']))")"
expect_eq "summary lines" "2" "$(python3 "${MANIFEST_PY}" summary "${M}" | wc -l | tr -d ' ')"

python3 "${MANIFEST_PY}" get "${M}" --team-id t99 --field build_status >/dev/null 2>&1
expect_eq "missing team exits 1" "1" "$?"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
