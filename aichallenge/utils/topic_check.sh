#!/usr/bin/env bash
# Pre-drive ROS 2 topic checker (presence-only)
#
# 前提:
#   - コンテナ内で実行
#   - `source aichallenge/workspace/install/setup.bash` 済み
# 使い方:
#   bash utils/topic_check.sh [--timeout 15] [--output output/latest/topic_check.txt] [--list]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_repo_root() {
    local dir="$1"
    local i parent

    for ((i = 0; i < 10; i++)); do
        if [ -d "${dir}/output" ]; then
            echo "$dir"
            return 0
        fi
        parent="$(cd "${dir}/.." && pwd)"
        if [ "$parent" = "$dir" ]; then
            break
        fi
        dir="$parent"
    done

    # Fallback: keep previous behavior-ish, but avoid pointing under /aichallenge.
    if [ -d "/output" ]; then
        echo "/"
        return 0
    fi
    { cd "$1/../.." 2>/dev/null && pwd; } || true
}

REPO_ROOT="$(find_repo_root "$SCRIPT_DIR")"

TIMEOUT=3
HZ_SECS=5
HZ_WINDOW=2
THRESHOLD_HZ=5.0
LOG_FILE="$REPO_ROOT/output/latest/topic_check.txt"

# 必須トピック（vehicle/topic_check.md を元に選定）
CRITICAL_TOPICS=(
    "/tf"
    "/control/command/control_cmd"
    "/control/command/actuation_cmd"
    "/vehicle/status/velocity_status"
    "/sensing/imu/imu_data"
    "/sensing/gnss/nav_sat_fix"
)

# Hz は全必須トピックで計測（存在確認に加えレート目安を出力）

usage() {
    cat <<EOF
Topic Check (presence-only) for Autoware/AWSIM

Options:
  --timeout <sec>   必須トピックが出揃うまでの待機秒数（デフォルト: ${TIMEOUT}s）
  --hz-secs <sec>   Hz 計測の観測秒数（デフォルト: ${HZ_SECS}s）
  --hz-window <n>   ros2 topic hz のウィンドウ数（デフォルト: ${HZ_WINDOW}）
  --output <path>   ログ出力先（デフォルト: ${LOG_FILE}）
  --list            必須トピック一覧を表示
  -h, --help        このヘルプ

例:
  source aichallenge/workspace/install/setup.bash
  bash utils/topic_check.sh --timeout 20
EOF
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[ERROR] '$1' が見つかりません。コンテナ内で実行し、ROS 2 環境を source 済みか確認してください。" >&2
        exit 127
    fi
}

print_lists() {
    echo "[Required Topics]"
    printf '%s\n' "${CRITICAL_TOPICS[@]}"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
        --timeout)
            TIMEOUT=${2:-10}
            shift 2
            ;;
        --hz-secs)
            HZ_SECS=${2:-5}
            shift 2
            ;;
        --hz-window)
            HZ_WINDOW=${2:-20}
            shift 2
            ;;
        --output)
            if command -v realpath >/dev/null 2>&1; then
                LOG_FILE=$(realpath "$2")
            elif command -v readlink >/dev/null 2>&1; then
                LOG_FILE=$(readlink -f "$2" 2>/dev/null || echo "$2")
            else
                LOG_FILE="$2"
            fi
            shift 2
            ;;
        --list)
            print_lists
            exit 0
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "不明な引数: $1"
            usage
            exit 2
            ;;
        esac
    done
}

log_init() {
    mkdir -p "$(dirname "$LOG_FILE")"
    exec > >(tee -a "$LOG_FILE") 2>&1
    echo "===== Topic Check $(date -Iseconds) ====="
}

collect_topics_once() {
    ros2 topic list 2>/dev/null | head -n 1000 || true
}

all_required_present_within_timeout() {
    local -r deadline=$(($(date +%s) + TIMEOUT))
    local -a remaining=("${CRITICAL_TOPICS[@]}")
    local listed
    while :; do
        listed=$(collect_topics_once)
        local -a still_missing=()
        for t in "${remaining[@]}"; do
            if ! grep -xq "$t" <<<"$listed"; then
                still_missing+=("$t")
            fi
        done
        if [[ ${#still_missing[@]} -eq 0 ]]; then
            echo "$listed"
            return 0
        fi
        if (($(date +%s) >= deadline)); then
            echo "$listed"
            return 1
        fi
        sleep 1
        remaining=("${still_missing[@]}")
    done
}

measure_hz() {
    local topic="$1"
    # timeout 不在時はスキップ
    if ! command -v timeout >/dev/null 2>&1; then
        echo "SKIP(no-timeout)"
        return 0
    fi
    # ros2 topic hz を一定秒観測し、出力から average rate を抽出
    local out rc rate
    set +e
    out=$(timeout "${HZ_SECS}s" ros2 topic hz --window "${HZ_WINDOW}" "$topic" 2>&1)
    rc=$?
    set -e
    rate=$(printf '%s\n' "$out" | sed -n 's/.*average rate: \([0-9.][0-9.]*\).*/\1/p' | tail -n 1)
    if [[ -n $rate ]]; then
        echo "$rate"
    else
        echo "NA"
    fi
}
check_actuation_cmd() {
    local topic="/control/command/actuation_cmd"
    # トピックが存在するかチェック（BrokenPipeError回避）
    local topic_exists
    topic_exists=$(ros2 topic list 2>/dev/null | grep -x "$topic" || true)
    if [[ -z $topic_exists ]]; then
        echo "MISSING"
        return 1
    fi
    # メッセージを1回取得
    local msg
    if ! msg=$(timeout 3s ros2 topic echo "$topic" --once 2>/dev/null); then
        echo "NO_DATA"
        return 1
    fi
    # accel_cmd と brake_cmd を抽出
    local accel_cmd brake_cmd
    accel_cmd=$(echo "$msg" | grep "accel_cmd:" | sed 's/.*accel_cmd: \([0-9.-][0-9.-]*\).*/\1/')
    brake_cmd=$(echo "$msg" | grep "brake_cmd:" | sed 's/.*brake_cmd: \([0-9.-][0-9.-]*\).*/\1/')
    # 値が取得できたかチェック
    if [[ -z $accel_cmd || -z $brake_cmd ]]; then
        echo "PARSE_ERROR"
        return 1
    fi
    # accel_cmd > 0 かつ brake_cmd = 0.0 をチェック
    local accel_ok brake_ok
    accel_ok=$(awk -v a="$accel_cmd" 'BEGIN{exit !(a>0)}' && echo "true" || echo "false")
    brake_ok=$(awk -v b="$brake_cmd" 'BEGIN{exit !(b==0.0)}' && echo "true" || echo "false")
    if [[ $accel_ok == "true" && $brake_ok == "true" ]]; then
        echo "PASS (accel:$accel_cmd, brake:$brake_cmd)"
        return 0
    else
        echo "FAIL (accel:$accel_cmd, brake:$brake_cmd)"
        return 1
    fi
}

check_awsim_topics() {
    # awsim を含むトピックがないことを確認（実車環境確認）
    local topic_list awsim_topics
    topic_list=$(ros2 topic list 2>/dev/null) || return 1
    # set -o pipefail の影響を避けるため、別々に実行
    awsim_topics=$(echo "$topic_list" | grep -i awsim || true)
    if [[ -z $awsim_topics ]]; then
        echo "PASS (no awsim topics found)"
        return 0
    else
        echo "FAIL (awsim topics found: $(echo "$awsim_topics" | tr '\n' ' '))"
        return 1
    fi
}

is_number() {
    [[ $1 =~ ^[0-9]+([.][0-9]+)?$ ]]
}

is_ge() {
    # return 0 if $1 >= $2 (float)
    awk -v a="$1" -v b="$2" 'BEGIN{exit !(a>=b)}'
}

main() {
    parse_args "$@"
    require_cmd ros2
    log_init

    echo "[INFO] 必須トピックの出現を最大 ${TIMEOUT}s 待機します…"
    local listed
    if listed=$(all_required_present_within_timeout); then
        echo "[OK] 必須トピックは全て一覧に存在します。"
    else
        echo "[ERROR] 必須トピックの一部が見つかりません。"
    fi

    local -a missing_required=()
    for t in "${CRITICAL_TOPICS[@]}"; do
        if ! grep -xq "$t" <<<"$listed"; then
            missing_required+=("$t")
        fi
    done

    if ((${#missing_required[@]} > 0)); then
        echo "[MISSING] 必須トピック（不足）:"
        printf '  %s\n' "${missing_required[@]}"
    fi

    # Hz 計測（存在する対象のみ）
    echo ""
    echo "[INFO] Hz 計測（約 ${HZ_SECS}s 観測, window=${HZ_WINDOW}、閾値: ${THRESHOLD_HZ}Hz）"
    printf '%-50s %-10s %s\n' "Topic" "Hz" ">= ${THRESHOLD_HZ}"
    printf '%-50s %-10s %s\n' "-----" "-----" "---------"
    slow_count=0
    na_count=0
    for t in "${CRITICAL_TOPICS[@]}"; do
        if grep -xq "$t" <<<"$listed"; then
            hz=$(measure_hz "$t") || hz="NA"
            if is_number "$hz"; then
                if is_ge "$hz" "$THRESHOLD_HZ"; then
                    printf '%-50s %-10s %s\n' "$t" "$hz" "PASS"
                else
                    printf '%-50s %-10s %s\n' "$t" "$hz" "FAIL"
                    slow_count=$((slow_count + 1))
                fi
            else
                printf '%-50s %-10s %s\n' "$t" "$hz" "FAIL"
                na_count=$((na_count + 1))
            fi
        else
            printf '%-50s %-10s %s\n' "$t" "missing" "FAIL"
        fi
    done
    # actuation_cmd の内容チェック
    echo ""
    echo "[INFO] actuation_cmd 内容チェック（accel_cmd > 0 かつ brake_cmd = 0.0）"
    actuation_result=$(check_actuation_cmd) || true
    actuation_rc=$?
    printf '%-50s %s\n' "/control/command/actuation_cmd" "$actuation_result"

    # awsim トピックチェック
    echo ""
    echo "[INFO] AWSIM トピックチェック（実車環境確認）"
    awsim_result=$(check_awsim_topics) || true
    awsim_rc=$?
    printf '%-50s %s\n' "AWSIM topics absence" "$awsim_result"
    echo ""
    echo "===== SUMMARY ====="
    echo "Total required : ${#CRITICAL_TOPICS[@]}"
    echo "Required missing : ${#missing_required[@]}"
    echo "Hz window/sec  : ${HZ_WINDOW}/${HZ_SECS}"
    echo "Hz threshold   : ${THRESHOLD_HZ}"
    echo "Hz < threshold : ${slow_count} (NA含む: ${na_count})"
    echo "Actuation check : $(if ((actuation_rc == 0)); then echo "PASS"; else echo "FAIL"; fi)"
    echo "AWSIM check     : $(if ((awsim_rc == 0)); then echo "PASS"; else echo "FAIL"; fi)"
    echo "Log file        : $LOG_FILE"

    rc=0
    if ((${#missing_required[@]} > 0)); then rc=2; fi
    if ((slow_count > 0 || na_count > 0)); then rc=3; fi
    if ((actuation_rc != 0)); then rc=4; fi
    if ((awsim_rc != 0)); then rc=5; fi
    exit "$rc"
}

main "$@"
