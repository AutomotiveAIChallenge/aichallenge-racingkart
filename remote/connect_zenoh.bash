#!/bin/bash
# 遠隔操作PC側の zenoh ブリッジを1台分だけ起動する。
#
#   connect_zenoh.bash {A1|A2|A3|A5|A6|A7|A8}
#   connect_zenoh.bash {test-remote|test-vehicle|test-server}
#
# 複数台をまとめて起動するときは make remote (compose の zenoh-remote サービス、
# remote/run_zenoh.bash) を使う。こちらは1台だけ手で試すための道具。
#
# 設定は remote/zenoh-user.json5.template から車両ごとに生成する。許可リストは車両側の
# vehicle/zenoh.json5 と揃えること。片側だけ直すと値が遠隔PCへ届かず、しかも自動テストでは
# 検出できない。

set -eo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TEMPLATE="${SCRIPT_DIR}/zenoh-user.json5.template"
ROUTER_HOST="zenoh.dev.aichallenge-board.jsae.or.jp"

# ポート表は vehicle/vehicle_ports.sh が唯一の出どころ。ここで複製しない。
# shellcheck source-path=SCRIPTDIR source=../vehicle/vehicle_ports.sh
source "${SCRIPT_DIR}/../vehicle/vehicle_ports.sh"

# 設定を1本生成する。__TLS_DIR__ にはこのスクリプトが居る場所を入れる (ホスト実行のため)。
render_config() {
    local vehicle_id="$1" out
    out="$(mktemp -t "zenoh-user-${vehicle_id}-XXXXXX.json5")"
    sed -e "s/__VEHICLE_ID__/${vehicle_id}/g" \
        -e "s#__TLS_DIR__#${SCRIPT_DIR}#g" \
        "${TEMPLATE}" >"${out}"
    echo "${out}"
}

if [ "$#" -ne 1 ]; then
    echo "エラー: Vehicle ID を指定してください。" >&2
    echo "使用法: $0 {A1|A2|A3|A5|A6|A7|A8|test-remote|test-vehicle|test-server}" >&2
    exit 1
fi

VEHICLE_ID="$1"

case "${VEHICLE_ID}" in
test-remote)
    ENDPOINT="${ZENOH_LOCAL_ENDPOINT:-tcp/127.0.0.1:7448}"
    echo "Connecting Zenoh. Target Vehicle: 'local' - Endpoint ${ENDPOINT}"
    config="$(render_config A2)"
    exec env RUST_BACKTRACE=1 zenoh-bridge-ros2dds client -e "${ENDPOINT}" -c "${config}"
    ;;
test-vehicle)
    ENDPOINT="${ZENOH_LOCAL_ENDPOINT:-tcp/127.0.0.1:7448}"
    echo "Connecting Zenoh. Target Vehicle: 'local' - Endpoint ${ENDPOINT}"
    exec env RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e "${ENDPOINT}" -n /A2 -c "${SCRIPT_DIR}/../vehicle/zenoh.json5"
    ;;
test-server)
    exec zenohd --listen tcp/127.0.0.1:7448
    ;;
esac

if ! PORT="$(zenoh_port_for_vehicle_id "${VEHICLE_ID}")"; then
    echo "エラー: 無効な Vehicle ID です: '${VEHICLE_ID}'" >&2
    echo "${VEHICLE_ID_VALID_LIST}, test-* のいずれかを指定してください。" >&2
    exit 1
fi

# 遠隔側のブリッジには名前空間を付けない。トピック名にあらかじめ車両IDが入っており、
# 車両側のブリッジが -n /<VEHICLE_ID> で剥がす。これが両者を噛み合わせている。
# exec でこのシェルが置き換わったあともブリッジが読み続けるため、生成した設定は消さない。
config="$(render_config "${VEHICLE_ID}")"

echo "Connecting Zenoh. Target Vehicle: '${VEHICLE_ID}' - Port ${PORT}"
exec env RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
    -e "tls/${ROUTER_HOST}:${PORT}" \
    -c "${config}"
