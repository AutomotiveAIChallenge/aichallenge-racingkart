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
PHASE="all"
ENABLE_LOG=false
LOG_FILE="setup_check_$(date +'%Y%m%d_%H%M%S').log"
CAN_IFACE="${CAN_IFACE:-can0}"
CAN_SAMPLE_SEC="${CAN_SAMPLE_SEC:-3}"
CAN_MIN_FRAMES="${CAN_MIN_FRAMES:-100}"
GNSS_NAVPVT_TIMEOUT_SEC="${GNSS_NAVPVT_TIMEOUT_SEC:-8}"
ROS_TOPIC_TIMEOUT_SEC="${ROS_TOPIC_TIMEOUT_SEC:-4}"
ROS_TOPIC_RETRY="${ROS_TOPIC_RETRY:-2}"
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0
SECTION_INDEX=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "${REPO_ROOT}" ]; then
    REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

# shellcheck source-path=SCRIPTDIR source=vehicle_ports.sh
source "${SCRIPT_DIR}/vehicle_ports.sh"

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
  --phase PHASE   Check phase: preflight, runtime, or all [default: all]
  --log           Enable logging to file
  --help          Show this help

ENVIRONMENT:
  CAN_IFACE        CAN interface to check [default: can0]
  CAN_SAMPLE_SEC   candump sampling seconds [default: 3]
  CAN_MIN_FRAMES   minimum expected frames in the sample [default: 100]
  GNSS_NAVPVT_TIMEOUT_SEC
                   seconds to wait for /sensing/gnss/navpvt [default: 8]
  ROS_TOPIC_TIMEOUT_SEC
                   seconds to wait for each runtime ROS topic [default: 4]
  ROS_TOPIC_RETRY  attempts per runtime ROS topic before reporting a failure
                   [default: 2]

MODE:
  vehicle         Real vehicle mode (CAN + VCU required) [default]

Examples:
  $0
  $0 --phase preflight
  $0 --phase runtime
  $0 --log
  CAN_SAMPLE_SEC=5 CAN_MIN_FRAMES=200 $0 --log
EOF
}

# 引数解析
while [[ $# -gt 0 ]]; do
    case $1 in
    --phase)
        PHASE="${2-}"
        case "${PHASE}" in
        preflight | runtime | all) ;;
        *)
            echo "Invalid phase: ${PHASE}"
            show_help
            exit 1
            ;;
        esac
        shift 2
        ;;
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

# セクション番号は表示順に振るため、どのphaseでも1から連番になる
print_section() {
    SECTION_INDEX=$((SECTION_INDEX + 1))
    log "${INFO} ${SECTION_INDEX}. $1"
    log "----------------------------------------"
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

compose_running_services() {
    docker compose -f "${REPO_ROOT}/docker-compose.yml" ps --services --filter status=running 2>/dev/null
}

is_compose_service_running() {
    local service=$1
    compose_running_services | grep -Fxq "${service}"
}

ros_setup_command_for_service() {
    case "$1" in
    driver)
        cat <<'EOF'
set +u
source /opt/ros/humble/setup.bash
source /workspace/install/setup.bash
set -u
EOF
        ;;
    autoware)
        cat <<'EOF'
set +u
source /opt/ros/humble/setup.bash
source /aichallenge/workspace/install/setup.bash
if [ -f "${RACING_KART_INTERFACE_DIR:-/home/tier4/racing_kart_interface}/install/setup.bash" ]; then
    source "${RACING_KART_INTERFACE_DIR:-/home/tier4/racing_kart_interface}/install/setup.bash"
fi
set -u
EOF
        ;;
    *)
        return 1
        ;;
    esac
}

check_ros_topic_once() {
    local service=$1
    local topic=$2
    local label=$3
    local setup_cmd

    if ! is_compose_service_running "${service}"; then
        log "${FAIL} ${label}: ${service} service is not running"
        record_result "fail"
        return 0
    fi

    if ! setup_cmd="$(ros_setup_command_for_service "${service}")"; then
        log "${FAIL} ${label}: unknown compose service '${service}'"
        record_result "fail"
        return 0
    fi

    # runtimeチェックは docker compose up の直後に走るため、トピックがまだ出ていない
    # だけのケースがある。false failを避けるためリトライする。
    local attempt=1
    while [ "${attempt}" -le "${ROS_TOPIC_RETRY}" ]; do
        if docker compose -f "${REPO_ROOT}/docker-compose.yml" exec -T "${service}" bash -lc "
            ${setup_cmd}
            timeout '${ROS_TOPIC_TIMEOUT_SEC}' ros2 topic echo '${topic}' --once >/dev/null
        " >/dev/null 2>&1; then
            log "${OK} ${label}: ${topic}"
            record_result "pass"
            return 0
        fi
        attempt=$((attempt + 1))
    done

    log "${FAIL} ${label}: no message on ${topic} within ${ROS_TOPIC_TIMEOUT_SEC}s x ${ROS_TOPIC_RETRY}"
    record_result "fail"
}

check_can_traffic() {
    local iface=$1

    log "${INFO} CAN traffic sample (${iface}, ${CAN_SAMPLE_SEC}s)"

    if ! command -v candump >/dev/null 2>&1; then
        log "${FAIL} candump command not found; cannot check CAN traffic"
        log "   Fix: sudo apt-get install -y can-utils"
        record_result "fail"
        return 0
    fi

    local link_details
    link_details="$(ip -details -statistics link show "${iface}" 2>/dev/null || true)"
    if grep -Eq "state (ERROR-PASSIVE|BUS-OFF|STOPPED)" <<<"${link_details}"; then
        local can_state
        can_state="$(grep -Eo "state (ERROR-PASSIVE|BUS-OFF|STOPPED)" <<<"${link_details}" | head -1)"
        log "${FAIL} CAN interface ${iface} is unhealthy: ${can_state}"
        log "   Check: motor/controller power, CAN wiring, termination, and bitrate."
        record_result "fail"
    elif grep -q "state ERROR-ACTIVE" <<<"${link_details}"; then
        log "${OK} CAN interface ${iface} state is ERROR-ACTIVE"
        record_result "pass"
    else
        log "${WARN} CAN interface ${iface} state could not be confirmed"
        log "   Details: $(grep -Eo 'state [A-Z_-]+' <<<"${link_details}" | head -1)"
        record_result "warn"
    fi

    local berr_counter
    berr_counter="$(grep -Eo "berr-counter tx [0-9]+ rx [0-9]+" <<<"${link_details}" | head -1 || true)"
    if [ -n "${berr_counter}" ]; then
        log "${INFO} CAN ${berr_counter}"
    fi

    local sample
    sample="$(timeout "${CAN_SAMPLE_SEC}" candump -ta -e "${iface}" 2>/dev/null || true)"
    local frame_count
    local error_count
    local unique_id_count
    frame_count="$(grep -cve '^[[:space:]]*$' <<<"${sample}" || true)"
    error_count="$(grep -ciE 'ERRORFRAME|CAN_ERR| error ' <<<"${sample}" || true)"
    unique_id_count="$(
        awk '
            NF >= 3 && $0 !~ /ERRORFRAME|CAN_ERR/ {
                id = $3
                sub(/#.*/, "", id)
                seen[id] = 1
            }
            END {
                for (id in seen) count++
                print count + 0
            }
        ' <<<"${sample}"
    )"

    if [ "${error_count}" -gt 0 ]; then
        log "${FAIL} CAN error frames observed during sample: ${error_count}/${frame_count}"
        log "   Check: motor/controller power, CAN-H/CAN-L wiring, termination, bitrate, and loose connectors."
        record_result "fail"
    elif [ "${frame_count}" -ge "${CAN_MIN_FRAMES}" ]; then
        log "${OK} CAN traffic observed: ${frame_count} frames, ${unique_id_count} IDs, no error frames"
        record_result "pass"
    elif [ "${frame_count}" -gt 0 ]; then
        log "${FAIL} CAN traffic is very low: ${frame_count} frames, ${unique_id_count} IDs"
        log "   Expected at least ${CAN_MIN_FRAMES} frames in ${CAN_SAMPLE_SEC}s. Check motor/controller power if this is lower than usual."
        record_result "fail"
    else
        log "${FAIL} No CAN traffic observed in ${CAN_SAMPLE_SEC}s"
        log "   Check: motor/controller power, VCU state, CAN wiring, termination, and bitrate."
        record_result "fail"
    fi
}

read_env_value() {
    local key=$1
    local env_file="${REPO_ROOT}/.env"

    [ -f "${env_file}" ] || return 0
    # 先頭の空白と `export ` を落として `KEY=value` に正規化してから読む
    sed -E 's/^[[:space:]]*(export[[:space:]]+)?//' "${env_file}" |
        awk -F= -v key="${key}" '
            $1 == key {
                value = substr($0, length(key) + 2)
                gsub(/^["'\'']|["'\'']$/, "", value)
                print value
            }
        ' | tail -1
}

detect_vehicle_id() {
    local vehicle_id="${VEHICLE_ID-}"

    if [ -z "${vehicle_id}" ]; then
        vehicle_id="$(read_env_value VEHICLE_ID)"
    fi

    if [ -z "${vehicle_id}" ]; then
        vehicle_id="$(vehicle_id_for_hostname "$(hostname)" || true)"
    fi

    printf '%s\n' "${vehicle_id}"
}

# ヘッダー表示
print_header() {
    log ""
    log "========================================"
    log "Racing Kart Setup Check"
    log "Mode: $MODE"
    log "Phase: $PHASE"
    log "Time: $(date)"
    log "========================================"
    log ""
}

# 物理デバイス・ハードウェア確認 (preflight)
check_hardware() {
    print_section "Hardware Device Check"

    # CANデバイス確認
    if ip link show "${CAN_IFACE}" >/dev/null 2>&1; then
        log "${OK} CAN interface ${CAN_IFACE} exists"
        record_result "pass"
    else
        log "${FAIL} CAN interface ${CAN_IFACE} not found"
        log "   Fix: Check CAN hardware connection"
        record_result "fail"
    fi

    check_command "candump" "candump (can-utils)"

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

# 起動後ハードウェア通信確認 (runtime)
check_runtime_hardware() {
    print_section "Runtime Hardware Communication Check"

    if ip link show "${CAN_IFACE}" >/dev/null 2>&1; then
        if ip link show "${CAN_IFACE}" | grep -q "UP"; then
            log "${OK} CAN interface ${CAN_IFACE} is UP"
            record_result "pass"
            check_can_traffic "${CAN_IFACE}"
        else
            log "${FAIL} CAN interface ${CAN_IFACE} exists but not UP"
            log "   Fix: sudo ip link set ${CAN_IFACE} up type can bitrate 1000000"
            record_result "fail"
        fi
    else
        log "${FAIL} CAN interface ${CAN_IFACE} not found"
        log "   Fix: Check CAN hardware connection"
        record_result "fail"
    fi

    log ""
}

# ネットワーク・通信確認 (preflight)
check_network() {
    print_section "Network & Communication Check"

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
        if timeout 5 bash -c "echo >/dev/tcp/${zenoh_host}/${zenoh_port}" 2>/dev/null; then
            log "${OK} Zenoh endpoint connectivity (${vehicle_id_for_zenoh}: ${zenoh_host}:${zenoh_port})"
            record_result "pass"
        else
            log "${FAIL} Cannot reach Zenoh endpoint (${vehicle_id_for_zenoh}: ${zenoh_host}:${zenoh_port})"
            log "   Check: VEHICLE_ID, internet route, firewall, and server-side tunnel/port availability."
            record_result "fail"
        fi
    else
        log "${FAIL} Invalid VEHICLE_ID for Zenoh: ${vehicle_id_for_zenoh}"
        log "   Valid: ${VEHICLE_ID_VALID_LIST}"
        record_result "fail"
    fi

    log ""
}

# Docker・環境確認 (preflight)
check_docker() {
    print_section "Docker & Environment Check"

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

    log ""
}

# 起動後Dockerサービス確認 (runtime)
check_runtime_docker_services() {
    print_section "Runtime Docker Service Check"

    if ! command -v docker >/dev/null 2>&1; then
        log "${FAIL} Docker command not found"
        record_result "fail"
        log ""
        return 0
    fi

    if ! docker ps >/dev/null 2>&1; then
        log "${FAIL} Docker daemon not accessible"
        log "   Fix: sudo systemctl start docker"
        record_result "fail"
        log ""
        return 0
    fi

    local required_services=(driver autoware rosbag zenoh)
    local running_services
    local missing_services=()
    if running_services="$(compose_running_services)"; then
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

    log ""
}

# GNSS/RTK状態確認 (runtime)
check_gnss_rtk_status() {
    print_section "GNSS/RTK Status Check"

    if ! command -v docker >/dev/null 2>&1; then
        log "${FAIL} Docker command not found; cannot check /sensing/gnss/navpvt"
        record_result "fail"
        log ""
        return 0
    fi

    local running_services
    if ! running_services="$(compose_running_services)"; then
        log "${FAIL} Cannot inspect docker compose services for GNSS/RTK check"
        record_result "fail"
        log ""
        return 0
    fi

    if ! grep -Fxq "driver" <<<"${running_services}"; then
        log "${FAIL} driver service is not running; cannot read /sensing/gnss/navpvt"
        log "   Fix: start driver service and wait for GNSS messages."
        record_result "fail"
        log ""
        return 0
    fi

    local output
    local flags
    output="$(
        docker compose -f "${REPO_ROOT}/docker-compose.yml" exec -T driver bash -lc "
            set +u
            source /opt/ros/humble/setup.bash
            source /workspace/install/setup.bash
            set -u
            timeout '${GNSS_NAVPVT_TIMEOUT_SEC}' ros2 topic echo /sensing/gnss/navpvt --once --field flags
        " 2>&1
    )"
    flags="$(awk '/^[[:space:]]*[0-9]+[[:space:]]*$/ { value = $1 } END { print value }' <<<"${output}")"

    if [ -z "${flags}" ]; then
        log "${FAIL} Could not read /sensing/gnss/navpvt flags within ${GNSS_NAVPVT_TIMEOUT_SEC}s"
        log "   Check: driver logs, GNSS antenna, sky visibility, and /dev/gnss/usb."
        log "   Do not start autonomous driving until GNSS RTK fixed is confirmed."
        record_result "fail"
    elif [ "${flags}" = "131" ]; then
        log "${OK} GNSS RTK fixed: NavPVT flags=${flags}"
        record_result "pass"
    elif [ "${flags}" = "67" ]; then
        log "${WARN} GNSS RTK float: NavPVT flags=${flags}"
        log "   Check: ichimil account/status, correction data connection, and open-sky GNSS conditions."
        log "   Recommendation: wait for RTK fixed before starting autonomous driving."
        record_result "warn"
    else
        log "${FAIL} GNSS RTK status is not acceptable: NavPVT flags=${flags}"
        log "   Expected: 131=fixed (${OK}), 67=float (${WARN})"
        log "   Check: ichimil account/status, correction data connection, and open-sky GNSS conditions."
        log "   Do not start autonomous driving until GNSS RTK fixed is confirmed."
        record_result "fail"
    fi

    log ""
}

# ROS topic出力確認 (runtime)
check_runtime_ros_topics() {
    print_section "Runtime ROS Topic Output Check"

    log "${INFO} Racing kart hardware/status topics"
    check_ros_topic_once "driver" "/racing_kart/vcu/status" "VCU status"
    check_ros_topic_once "driver" "/racing_kart/steer/status" "Steer status"
    check_ros_topic_once "driver" "/racing_kart/brake/status" "Brake status"
    check_ros_topic_once "driver" "/racing_kart/joy" "Joy input"

    log "${INFO} Racing kart final command topics"
    check_ros_topic_once "driver" "/racing_kart/vcu/command" "VCU command"
    check_ros_topic_once "driver" "/racing_kart/steer/command" "Steer command"
    check_ros_topic_once "driver" "/racing_kart/brake/command" "Brake command"

    log "${INFO} Autoware vehicle status topics"
    check_ros_topic_once "autoware" "/vehicle/status/velocity_status" "Velocity status"
    check_ros_topic_once "autoware" "/vehicle/status/steering_status" "Steering status"
    check_ros_topic_once "autoware" "/vehicle/status/gear_status" "Gear status"
    check_ros_topic_once "autoware" "/vehicle/status/actuation_status" "Actuation status"

    log "${INFO} Autoware downstream control command topics"
    check_ros_topic_once "autoware" "/control/command/control_cmd" "Control command"
    check_ros_topic_once "autoware" "/control/command/actuation_cmd" "Actuation command"

    log ""
}

# past_log.md既知問題チェック (preflight)
check_known_issues() {
    print_section "Known Issues Prevention Check"

    # バッテリー警告
    log "${WARN} Remember: Check battery level manually (display values unreliable)"
    record_result "warn"

    # 実行前Wait推奨（GNSSのため）
    log "${INFO} Recommendation: Wait outside for GNSS Fix before driving"
    log "${INFO} Recommendation: Check Fix status reaches ~80% before starting"

    log ""
}

# 実行準備確認 (preflight)
check_execution_readiness() {
    print_section "Execution Readiness Check (Vehicle Mode)"

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
    case "${PHASE}" in
    preflight)
        check_hardware
        check_network
        check_docker
        check_known_issues
        check_execution_readiness
        ;;
    runtime)
        check_runtime_hardware
        check_runtime_docker_services
        check_gnss_rtk_status
        check_runtime_ros_topics
        ;;
    all)
        check_hardware
        check_network
        check_docker
        check_runtime_hardware
        check_runtime_docker_services
        check_gnss_rtk_status
        check_runtime_ros_topics
        check_known_issues
        check_execution_readiness
        ;;
    esac
    print_summary
}

# スクリプト実行
main "$@"
