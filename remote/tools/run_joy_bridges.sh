#!/bin/bash
# joy 遅延測定用に、遠隔側と車両側の zenoh ブリッジを 1 台の PC 上で両方起動する。
#
#   run_joy_bridges.sh [-v A2] [-d 61] [-f keep|off|<Hz>] [-e on|off]
#
# 両ブリッジは EC2 router の同じポートへ繋ぐ。遠隔側は ROS_DOMAIN_ID 0、車両側は
# -d で指定したドメイン（既定 61）で動かす。ドメインを分けることが要点で、分けないと
# 2 本が DDS で直接つながり、EC2 を経由せずに joy が届いてしまい測定にならない。
#
# 前景で動き続ける。Ctrl+C で両方畳む。測定は別シェルから measure_joy_latency.py を叩く。
#
# 注意: 指定した車両 ID のポートに割り込む。実車がそのポートで走っている時間帯には使わない。

set -eo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REMOTE_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
REPO_DIR=$(cd "${REMOTE_DIR}/.." && pwd)
ROUTER_HOST="${ROUTER_HOST:-zenoh.dev.aichallenge-board.jsae.or.jp}"

VEHICLE_ID="A2"
# 既定を 61 にしてあるのは、51/52 が V2X の 2 車両模擬で埋まっていることがあるため。
# 使用中のドメインに重ねると、そこに居るノードのトピックまでブリッジが拾ってしまう。
RX_DOMAIN="61"
MAX_FREQ="keep"
EXPRESS="on"

while getopts "v:d:f:e:h" opt; do
    case "${opt}" in
    v) VEHICLE_ID="${OPTARG}" ;;
    d) RX_DOMAIN="${OPTARG}" ;;
    f) MAX_FREQ="${OPTARG}" ;;
    e) EXPRESS="${OPTARG}" ;;
    h)
        sed -n '2,15p' "${BASH_SOURCE[0]}"
        exit 0
        ;;
    *) exit 1 ;;
    esac
done

# ポート表は vehicle/vehicle_ports.sh が唯一の出どころ。ここで複製しない。
# shellcheck source-path=SCRIPTDIR source=../../vehicle/vehicle_ports.sh
source "${REPO_DIR}/vehicle/vehicle_ports.sh"

if ! PORT="$(zenoh_port_for_vehicle_id "${VEHICLE_ID}")"; then
    echo "エラー: 無効な Vehicle ID: '${VEHICLE_ID}' (有効: ${VEHICLE_ID_VALID_LIST})" >&2
    exit 1
fi

OUT_DIR="${OUT_DIR:-${REPO_DIR}/output/joy-latency/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "${OUT_DIR}"

# pub_max_frequencies の書き換え。
#
# 現行の設定値は "/*=10" だが、zenoh はこれを glob ではなく正規表現として扱う。
# "/*" は「/ が 0 回以上」の意味なので /A2/racing_kart/joy にはマッチしない。
# つまり現行設定では間引きが効いていない疑いがある。keep（既定）で現行のまま測り、
# 数値 Hz を渡すと正しい正規表現（".*=<Hz>"）に直したうえで効かせられる。
rewrite_max_freq() {
    case "${MAX_FREQ}" in
    keep) cat ;;
    off) sed -e 's#^\( *\)pub_max_frequencies: \[.*\],#\1pub_max_frequencies: [],#' ;;
    *) sed -e "s#^\( *\)pub_max_frequencies: \[.*\],#\1pub_max_frequencies: [\".*=${MAX_FREQ}\"],#" ;;
    esac
}

# express を外すと、zenoh はバッチにまとめてから送る。遅延と引き換えにスループットを取る。
rewrite_express() {
    if [ "${EXPRESS}" = "off" ]; then
        sed -e 's#racing_kart/joy=1:express#racing_kart/joy=1#'
    else
        cat
    fi
}

REMOTE_CONFIG="${OUT_DIR}/zenoh-user-${VEHICLE_ID}.json5"
sed -e "s/__VEHICLE_ID__/${VEHICLE_ID}/g" -e "s#__TLS_DIR__#${REMOTE_DIR}#g" \
    "${REMOTE_DIR}/zenoh-user.json5.template" | rewrite_max_freq | rewrite_express >"${REMOTE_CONFIG}"

# 車両側の設定は TLS 資材の場所がコンテナ内パス (/remote) で固定されているため、
# ホストで動かすにはリポジトリの remote/ を指すよう書き換える必要がある。
VEHICLE_CONFIG="${OUT_DIR}/zenoh-vehicle-${VEHICLE_ID}.json5"
sed -e "s#/remote/tls/#${REMOTE_DIR}/tls/#g" \
    "${REPO_DIR}/vehicle/zenoh.json5" | rewrite_max_freq >"${VEHICLE_CONFIG}"

pids=()
cleanup() {
    kill "${pids[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup TERM INT EXIT

echo "router      : tls/${ROUTER_HOST}:${PORT}  (vehicle ${VEHICLE_ID})"
echo "remote side : ROS_DOMAIN_ID 0   ${REMOTE_CONFIG}"
echo "vehicle side: ROS_DOMAIN_ID ${RX_DOMAIN}  ${VEHICLE_CONFIG}  -n /${VEHICLE_ID}"
echo "max_freq    : ${MAX_FREQ}   express: ${EXPRESS}"
echo "logs        : ${OUT_DIR}"
echo

ROS_DISTRO=humble ROS_DOMAIN_ID=0 zenoh-bridge-ros2dds client \
    -e "tls/${ROUTER_HOST}:${PORT}" -c "${REMOTE_CONFIG}" \
    >"${OUT_DIR}/bridge-remote.log" 2>&1 &
pids+=("$!")

ROS_DISTRO=humble ROS_DOMAIN_ID="${RX_DOMAIN}" zenoh-bridge-ros2dds client \
    -e "tls/${ROUTER_HOST}:${PORT}" -n "/${VEHICLE_ID}" -c "${VEHICLE_CONFIG}" \
    >"${OUT_DIR}/bridge-vehicle.log" 2>&1 &
pids+=("$!")

echo "両ブリッジ起動。Ctrl+C で停止。別シェルで measure_joy_latency.py を実行すること。"
wait -n || true
echo "ブリッジが 1 本落ちた。残りを畳む。" >&2
