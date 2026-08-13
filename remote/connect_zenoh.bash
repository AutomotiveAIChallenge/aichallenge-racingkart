#!/bin/bash
# 遠隔操作PC側の zenoh ブリッジを1台分だけ起動する。
#
#   connect_zenoh.bash {A1|A2|A3|A5|A6|A7|A8}
#   connect_zenoh.bash {test-remote|test-vehicle|test-server}
#
# 4台を同時に扱うときは、車両IDを変えてこのスクリプトを台数分だけ起動する。
#
# 設定は remote/zenoh-user.json5.template から車両ごとに生成する。許可リストは車両側の
# vehicle/zenoh.json5 と揃えること。片側だけ直すと値が遠隔PCへ届かず、しかも自動テストでは
# 検出できない。

set -eo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TEMPLATE="${SCRIPT_DIR}/zenoh-user.json5.template"
ROUTER_HOST="zenoh.dev.aichallenge-board.jsae.or.jp"

if [ "$#" -ne 1 ]; then
    echo "エラー: Vehicle ID を指定してください。" >&2
    echo "使用法: $0 {A1|A2|A3|A5|A6|A7|A8|test-remote|test-vehicle|test-server}" >&2
    exit 1
fi

VEHICLE_ID="$1"

# 車両IDと EC2 ルータのポートの対応。vehicle/run_zenoh.bash と揃えること。
case "${VEHICLE_ID}" in
A2) PORT=7448 ;; # ECU-RK-01
A3) PORT=7449 ;; # ECU-RK-02
A6) PORT=7450 ;; # ECU-RK-06
A7) PORT=7451 ;; # ECU-RK-00
A1) PORT=7452 ;;
A5) PORT=7453 ;;
A8) PORT=7454 ;;
test-remote)
    ENDPOINT="${ZENOH_LOCAL_ENDPOINT:-tcp/127.0.0.1:7448}"
    echo "Connecting Zenoh. Target Vehicle: 'local' - Endpoint ${ENDPOINT}"
    config="$(mktemp -t "zenoh-user-test-XXXXXX.json5")"
    sed "s/__VEHICLE_ID__/A2/g" "${TEMPLATE}" >"${config}"
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
*)
    echo "エラー: 無効な Vehicle ID です: '${VEHICLE_ID}'" >&2
    echo "A1, A2, A3, A5, A6, A7, A8, test-* のいずれかを指定してください。" >&2
    exit 1
    ;;
esac

# 遠隔側のブリッジには名前空間を付けない。トピック名にあらかじめ車両IDが入っており、
# 車両側のブリッジが -n /<VEHICLE_ID> で剥がす。これが両者を噛み合わせている。
# exec でこのシェルが置き換わったあともブリッジが読み続けるため、生成した設定は消さない。
config="$(mktemp -t "zenoh-user-${VEHICLE_ID}-XXXXXX.json5")"
sed "s/__VEHICLE_ID__/${VEHICLE_ID}/g" "${TEMPLATE}" >"${config}"

echo "Connecting Zenoh. Target Vehicle: '${VEHICLE_ID}' - Port ${PORT}"
exec env RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
    -e "tls/${ROUTER_HOST}:${PORT}" \
    -c "${config}"
