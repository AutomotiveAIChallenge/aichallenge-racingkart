#!/usr/bin/env bash
# stage_from_env.sh: .env の TEAM_NAME / VAULT_DIR / PASS_PHRASE を読み、
# 別チームが staged なら unstage してから stage すること、同じチームなら何もしないこと、
# PASS_PHRASE が空ならパスフレーズをファイルで渡さない（対話入力に落ちる）ことを検証する。
# stage_team.sh / unstage_team.sh はスタブに差し替える（gocryptfs 不要）。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/../stage_from_env.sh"
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
CALLS="${WORK}/calls"
mkdir -p "${WS}"

# --- スタブ: 呼び出しを記録し、marker と install/ の有無だけ本物と同じに振る舞う ---
cat >"${WORK}/stage_stub.sh" <<EOF
#!/usr/bin/env bash
set -eu
team="\${@: -1}"
passfile="\${PRESTAGE_PASSFILE-unset}"
pass="unset"
[ "\${passfile}" = "unset" ] || pass="\$(cat "\${passfile}")"
printf 'stage %s vault=%s pass=%s\n' "\${team}" "\$2" "\${pass}" >>"${CALLS}"
mkdir -p "${WS}/install"
echo "setup of \${team}" >"${WS}/install/setup.bash"
printf '%s now\n' "\${team}" >"${WS}/.staged_team"
EOF
cat >"${WORK}/unstage_stub.sh" <<EOF
#!/usr/bin/env bash
set -eu
printf 'unstage %s\n' "\$*" >>"${CALLS}"
rm -rf "${WS}/install" "${WS}/.staged_team"
EOF
chmod +x "${WORK}/stage_stub.sh" "${WORK}/unstage_stub.sh"

export PRESTAGE_WORKSPACE_ROOT="${ROOT}"
export PRESTAGE_STAGE_CMD="${WORK}/stage_stub.sh"
export PRESTAGE_UNSTAGE_CMD="${WORK}/unstage_stub.sh"
ENV_FILE="${ROOT}/.env"

run() { # runs the target, prints output indented, sets rc
    out=$("${TARGET}" 2>&1)
    rc=$?
    # shellcheck disable=SC2001 # prefixing every line needs a regex, not just substring replace
    echo "${out}" | sed 's/^/    /'
}

# --- .env が無い ---
run
expect_eq "missing .env exits 1" "1" "${rc}"

# --- TEAM_NAME が空 ---
printf 'TEAM_NAME=\nVAULT_DIR=/vault\nPASS_PHRASE=pw\n' >"${ENV_FILE}"
run
expect_eq "empty TEAM_NAME exits 1" "1" "${rc}"
expect_eq "nothing staged on empty TEAM_NAME" "no" "$([ -e "${CALLS}" ] && echo yes || echo no)"

# --- VAULT_DIR が空 ---
printf 'TEAM_NAME=t01\nVAULT_DIR=\n' >"${ENV_FILE}"
run
expect_eq "empty VAULT_DIR exits 1" "1" "${rc}"

# --- 正常系: 引用符付きの値、パスフレーズあり。他の変数は無視する ---
cat >"${ENV_FILE}" <<'EOF'
NTRIP_PASSWORD=secret
TEAM_NAME="t01"
VAULT_DIR='/path/to vault'
PASS_PHRASE="open sesame"
COMPOSE_FILE=docker-compose.yml
EOF
run
expect_eq "stage exits 0" "0" "${rc}"
expect_eq "stage called with team, vault and passphrase" \
    "stage t01 vault=/path/to vault pass=open sesame" "$(cat "${CALLS}")"
expect_eq "marker written" "yes" "$(grep -q '^t01 ' "${WS}/.staged_team" && echo yes || echo no)"

# --- 同じチームなら何もしない ---
run
expect_eq "same team exits 0" "0" "${rc}"
expect_eq "same team makes no further calls" "1" "$(wc -l <"${CALLS}" | tr -d ' ')"

# --- 別チームに変えると unstage してから stage ---
sed -i 's/^TEAM_NAME=.*/TEAM_NAME=t02/' "${ENV_FILE}"
run
expect_eq "switch exits 0" "0" "${rc}"
expect_eq "switch unstages then stages" \
    "unstage --yes
stage t02 vault=/path/to vault pass=open sesame" "$(sed -n '2,3p' "${CALLS}")"
expect_eq "marker now t02" "yes" "$(grep -q '^t02 ' "${WS}/.staged_team" && echo yes || echo no)"
expect_eq "t01 setup gone" "setup of t02" "$(cat "${WS}/install/setup.bash")"

# --- PASS_PHRASE が空なら passfile を渡さない（stage_team.sh が対話入力する） ---
rm -f "${WS}/.staged_team"
sed -i 's/^PASS_PHRASE=.*/PASS_PHRASE=/' "${ENV_FILE}"
run
expect_eq "empty PASS_PHRASE exits 0" "0" "${rc}"
expect_eq "empty PASS_PHRASE leaves PRESTAGE_PASSFILE unset" \
    "stage t02 vault=/path/to vault pass=unset" "$(tail -n1 "${CALLS}")"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
