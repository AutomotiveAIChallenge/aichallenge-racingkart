#!/bin/bash
#
# Racing Kart Setup Check Script
#
# Usage: ./setup_check.sh
#

# set -e  # エラー時の自動終了を無効化してすべてのチェックを実行

# カラー定義（使用しないがshellcheck対策でexport）
export RED='\033[0;31m'
export GREEN='\033[0;32m'
export YELLOW='\033[1;33m'
export BLUE='\033[0;34m'
export NC='\033[0m' # No Color

# 絵文字定義
OK="✅"
WARN="⚠️"
FAIL="❌"
INFO="ℹ️"

# デフォルト設定
MODE="vehicle"
ENABLE_LOG=false
LOG_FILE="setup_check_$(date +'%Y%m%d_%H%M%S').log"
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "${REPO_ROOT}" ]; then
    REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

# ログ関数
log() {
    echo -e "$1" | tee -a "$LOG_FILE" 2>/dev/null || echo -e "$1"
}

# 実際は使用しないがshellcheck対策で残す
# shellcheck disable=SC2317
log_only() {
    if [ "$ENABLE_LOG" = true ]; then
        echo -e "$1" >>"$LOG_FILE" 2>/dev/null || true
    fi
}

# ヘルプ表示
show_help() {
    cat <<EOF
Racing Kart Setup Check Script

Usage: $0 [OPTIONS]

OPTIONS:
  --log           Enable logging to file
  --help          Show this help

MODE:
  vehicle         Real vehicle mode (CAN + VCU required) [default]

Examples:
  $0
  $0 --log
EOF
}

# 引数解析
while [[ $# -gt 0 ]]; do
    case $1 in
    --log)
        ENABLE_LOG=true
        shift
        ;;
    --help)
        show_help
        exit 0
        ;;
    *)
        echo "Unknown option: $1"
        show_help
        exit 1
        ;;
    esac
done

# チェック結果記録
record_result() {
    local status=$1
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    case $status in
    "pass") PASSED_CHECKS=$((PASSED_CHECKS + 1)) ;;
    "fail") FAILED_CHECKS=$((FAILED_CHECKS + 1)) ;;
    "warn") WARNING_CHECKS=$((WARNING_CHECKS + 1)) ;;
    esac
}

# チェック関数
check_command() {
    local cmd=$1
    local name=$2
    if command -v "$cmd" >/dev/null 2>&1; then
        log "${OK} $name command available"
        record_result "pass"
        return 0
    else
        log "${FAIL} $name command not found"
        record_result "fail"
        return 1
    fi
}

check_file_exists() {
    local file=$1
    local name=$2
    local required=$3

    if [ -e "$file" ]; then
        log "${OK} $name exists: $file"
        record_result "pass"
        return 0
    else
        if [ "$required" = "required" ]; then
            log "${FAIL} $name missing: $file"
            record_result "fail"
        else
            log "${WARN} $name missing (optional): $file"
            record_result "warn"
        fi
        return 0 # エラーでも継続するためreturn 0に変更
    fi
}

read_env_value() {
    local key=$1
    local env_file="${REPO_ROOT}/.env"

    [ -f "${env_file}" ] || return 0
    awk -F= -v key="${key}" '
        $1 == key {
            value = substr($0, length(key) + 2)
            gsub(/^["'\'']|["'\'']$/, "", value)
            print value
        }
    ' "${env_file}" | tail -1
}

detect_vehicle_id() {
    local vehicle_id="${VEHICLE_ID:-}"

    if [ -z "${vehicle_id}" ]; then
        vehicle_id="$(read_env_value VEHICLE_ID)"
    fi

    if [ -z "${vehicle_id}" ]; then
        case "$(hostname)" in
        ECU-RK-01) vehicle_id="A2" ;;
        ECU-RK-02) vehicle_id="A3" ;;
        ECU-RK-06) vehicle_id="A6" ;;
        ECU-RK-00) vehicle_id="A7" ;;
        esac
    fi

    printf '%s\n' "${vehicle_id}"
}

zenoh_port_for_vehicle_id() {
    case "$1" in
    A2) echo 7448 ;;
    A3) echo 7449 ;;
    A6) echo 7450 ;;
    A7) echo 7451 ;;
    A1) echo 7452 ;;
    A5) echo 7453 ;;
    A8) echo 7454 ;;
    *) return 1 ;;
    esac
}

# ヘッダー表示
print_header() {
    log ""
    log "========================================"
    log "Racing Kart Setup Check"
    log "Mode: $MODE"
    log "Time: $(date)"
    log "========================================"
    log ""
}

# 1. 物理デバイス・ハードウェア確認
check_hardware() {
    log "${INFO} 1. Hardware Device Check"
    log "----------------------------------------"

    # CANデバイス確認
    if ip link show can0 >/dev/null 2>&1; then
        if ip link show can0 | grep -q "UP"; then
            log "${OK} CAN interface can0 is UP"
            record_result "pass"
        else
            log "${FAIL} CAN interface can0 exists but not UP"
            log "   Fix: sudo ip link set can0 up type can bitrate 1000000"
            record_result "fail"
        fi
    else
        log "${FAIL} CAN interface can0 not found"
        log "   Fix: Check CAN hardware connection"
        record_result "fail"
    fi

    # VCUデバイス確認 (vehicleモードで必須)
    check_file_exists "/dev/vcu" "VCU directory" "required"
    check_file_exists "/dev/vcu/usb" "VCU USB device" "required"

    # GNSSデバイス確認
    if ls /dev/gnss* >/dev/null 2>&1 || ls /dev/ttyACM1* >/dev/null 2>&1; then
        log "${OK} GNSS serial devices found"
        record_result "pass"
    else
        log "${WARN} No GNSS serial devices found"
        record_result "warn"
    fi

    check_file_exists "/dev/gnss/usb" "GNSS symlink" "optional"

    log ""
}

# 2. ネットワーク・通信確認
check_network() {
    log "${INFO} 2. Network & Communication Check"
    log "----------------------------------------"

    # 基本的な接続確認
    if ping -c 3 -W 5 8.8.8.8 >/dev/null 2>&1; then
        log "${OK} Internet connectivity (8.8.8.8)"
        record_result "pass"
    else
        log "${FAIL} No internet connectivity"
        log "   Fix: Check network configuration"
        record_result "fail"
    fi

    # デフォルトルート確認。特定の回線名には依存しない。
    if ip route get 8.8.8.8 >/dev/null 2>&1; then
        ROUTE_INFO=$(ip route get 8.8.8.8 2>/dev/null | head -1)
        log "${OK} Internet route available"
        log "   Route: ${ROUTE_INFO}"
        record_result "pass"
    else
        log "${FAIL} No internet route available"
        log "   Fix: Check Wi-Fi/LTE/router/default route configuration."
        record_result "fail"
    fi

    # DNS確認。Zenoh bridge はホスト名を使うため名前解決も確認する。
    if getent hosts zenoh.dev.aichallenge-board.jsae.or.jp >/dev/null 2>&1 ||
        getent hosts google.com >/dev/null 2>&1; then
        log "${OK} DNS resolution works"
        record_result "pass"
    else
        log "${FAIL} DNS resolution failed"
        log "   Fix: Check DNS settings and internet connectivity."
        record_result "fail"
    fi

    if command -v nmcli >/dev/null 2>&1; then
        ACTIVE_CONNECTIONS=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | sed 's/:/ on /g' | paste -sd ', ' -)
        if [ -n "${ACTIVE_CONNECTIONS}" ]; then
            log "${INFO} Active NetworkManager connections: ${ACTIVE_CONNECTIONS}"
        else
            log "${INFO} Active NetworkManager connections: none reported"
        fi
    fi

    # リバースSSHサービス状態確認
    if systemctl is-active --quiet reverse-ssh.service; then
        log "${OK} reverse-ssh.service is active (running)"
        record_result "pass"
    else
        log "${WARN} reverse-ssh.service is not active"
        log "   Fix: sudo systemctl start reverse-ssh.service"
        record_result "warn"
    fi

    # Zenohサーバー疎通確認。run_zenoh.bash と同じ VEHICLE_ID -> port 対応を使う。
    local zenoh_host="${ZENOH_HOST:-zenoh.dev.aichallenge-board.jsae.or.jp}"
    local vehicle_id_for_zenoh
    local zenoh_port
    vehicle_id_for_zenoh="$(detect_vehicle_id)"
    if [ -z "${vehicle_id_for_zenoh}" ]; then
        log "${FAIL} VEHICLE_ID is not set; cannot choose Zenoh endpoint"
        log "   Fix: export VEHICLE_ID=A6 or add VEHICLE_ID=A6 to .env"
        record_result "fail"
    elif zenoh_port="$(zenoh_port_for_vehicle_id "${vehicle_id_for_zenoh}")"; then
        if timeout 5 bash -c 'echo >/dev/tcp/"$1"/"$2"' _ "${zenoh_host}" "${zenoh_port}" 2>/dev/null; then
            log "${OK} Zenoh endpoint connectivity (${vehicle_id_for_zenoh}: ${zenoh_host}:${zenoh_port})"
            record_result "pass"
        else
            log "${FAIL} Cannot reach Zenoh endpoint (${vehicle_id_for_zenoh}: ${zenoh_host}:${zenoh_port})"
            log "   Check: VEHICLE_ID, internet route, firewall, and server-side tunnel/port availability."
            record_result "fail"
        fi
    else
        log "${FAIL} Invalid VEHICLE_ID for Zenoh: ${vehicle_id_for_zenoh}"
        log "   Valid: A1, A2, A3, A5, A6, A7, A8"
        record_result "fail"
    fi

    log ""
}

# 3. Docker・環境確認
check_docker() {
    log "${INFO} 3. Docker & Environment Check"
    log "----------------------------------------"

    # Docker確認
    check_command "docker" "Docker"

    if command -v docker >/dev/null 2>&1; then
        if docker ps >/dev/null 2>&1; then
            log "${OK} Docker daemon is running"
            record_result "pass"
        else
            log "${FAIL} Docker daemon not accessible"
            log "   Fix: sudo systemctl start docker"
            record_result "fail"
        fi

        # 必要なDockerイメージ確認
        RKI_INFO=$(docker images --format "{{.Repository}}:{{.Tag}} ({{.CreatedAt}})" | grep "racing_kart_interface" | head -1)
        if [ -n "$RKI_INFO" ]; then
            log "${OK} Racing kart interface image: $RKI_INFO"
            record_result "pass"
        else
            log "${WARN} Racing kart interface image not found"
            log "   Fix: Pull or build racing_kart_interface image"
            record_result "warn"
        fi

        AIC_INFO=$(docker images --format "{{.Repository}}:{{.Tag}} ({{.CreatedAt}})" | grep "aichallenge-2025-dev" | head -1)
        if [ -n "$AIC_INFO" ]; then
            log "${OK} Aichallenge dev image: $AIC_INFO"
            record_result "pass"
        else
            log "${WARN} Aichallenge dev image not found"
            log "   Fix: Build aichallenge development image"
            record_result "warn"
        fi

        local required_services=(driver autoware rosbag zenoh)
        local running_services
        local missing_services=()
        if running_services="$(docker compose -f "${REPO_ROOT}/docker-compose.yml" ps --services --filter status=running 2>/dev/null)"; then
            for service in "${required_services[@]}"; do
                if ! grep -Fxq "${service}" <<<"${running_services}"; then
                    missing_services+=("${service}")
                fi
            done

            if [ "${#missing_services[@]}" -eq 0 ]; then
                log "${OK} Required compose services are running: ${required_services[*]}"
                record_result "pass"
            else
                log "${FAIL} Required compose services not running: ${missing_services[*]}"
                log "   Expected running services: ${required_services[*]}"
                record_result "fail"
            fi
        else
            log "${FAIL} Cannot inspect docker compose services"
            log "   Fix: Check docker-compose.yml and Docker daemon"
            record_result "fail"
        fi
    fi

    # 環境変数確認
    if [ -n "$XAUTHORITY" ]; then
        log "${OK} XAUTHORITY is set: $XAUTHORITY"
        record_result "pass"
    else
        log "${WARN} XAUTHORITY not set"
        log "   Fix: export XAUTHORITY=~/.Xauthority"
        record_result "warn"
    fi

    # ユーザーグループ確認
    if groups "$USER" | grep -q "dialout"; then
        log "${OK} User $USER in dialout group"
        record_result "pass"
    else
        log "${WARN} User $USER not in dialout group"
        log "   Fix: sudo usermod -a -G dialout $USER"
        record_result "warn"
    fi

    log ""
}

# 4. past_log.md既知問題チェック
check_known_issues() {
    log "${INFO} 4. Known Issues Prevention Check"
    log "----------------------------------------"

    # バッテリー警告
    log "${WARN} Remember: Check battery level manually (display values unreliable)"
    log "${WARN} Remember: Avoid direct sunlight exposure for batteries"
    record_result "warn"

    # 実行前Wait推奨（GNSSのため）
    log "${INFO} Recommendation: Wait outside for GNSS Fix before driving"
    log "${INFO} Recommendation: Check Fix status reaches ~80% before starting"

    log ""
}

# 5. 実行準備確認
check_execution_readiness() {
    log "${INFO} 5. Execution Readiness Check (Vehicle Mode)"
    log "----------------------------------------"

    # Docker Composeファイル存在確認（repo root基準、missingでも致命扱いしない）
    COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
    if [ -f "${COMPOSE_FILE}" ]; then
        log "${OK} docker-compose.yml exists at repo root: ${COMPOSE_FILE}"
        record_result "pass"
    else
        log "${INFO} docker-compose.yml not found at repo root (skipping; not a vehicle hardware failure)"
    fi

    # 現在のブランチ確認
    if git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
        BRANCH=$(git -C "${REPO_ROOT}" branch --show-current)
        log "${INFO} Current git branch: $BRANCH"
    fi

    log ""
}

# 結果サマリー表示
print_summary() {
    log "========================================"
    log "📊 Check Results Summary"
    log "========================================"
    log "Total checks: $TOTAL_CHECKS"
    log "${OK} Passed: $PASSED_CHECKS"
    log "${WARN} Warnings: $WARNING_CHECKS"
    log "${FAIL} Failed: $FAILED_CHECKS"
    log ""

    if [ $FAILED_CHECKS -eq 0 ] && [ $WARNING_CHECKS -eq 0 ]; then
        log "${OK} All checks passed! System ready for vehicle mode."
        exit 0
    elif [ $FAILED_CHECKS -eq 0 ]; then
        log "${WARN} Some warnings found. Review before proceeding with vehicle mode."
        exit 0
    else
        log "${FAIL} Critical issues found! Fix failures before running vehicle mode."
        log ""
        log "Recommended actions:"
        log "1. Address all failed checks above"
        log "2. Re-run this script"
        exit 1
    fi
}

# メイン実行
main() {
    if [ "$ENABLE_LOG" = true ]; then
        log "${INFO} Logging enabled: $LOG_FILE"
        log ""
    fi

    print_header
    check_hardware
    check_network
    check_docker
    check_known_issues
    check_execution_readiness
    print_summary
}

# スクリプト実行
main "$@"
