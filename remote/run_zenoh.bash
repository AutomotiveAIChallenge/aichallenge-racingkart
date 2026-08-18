#!/bin/bash
# 遠隔操作PC側の zenoh ブリッジを、対象車両ぶんまとめて起動する。
#
#   run_zenoh.bash "A2 A3 A7" [LOG_DIR]
#
# 車両1台につき1プロセス。遠隔側は常に ROS_DOMAIN_ID 0 で、トピック名に車両IDが入った
# まま見える (/A2/racing_kart/joy)。車両側のブリッジが -n /<VEHICLE_ID> でそれを剥がす
# ことで両者が噛み合う。
#
# 1台だけ手で試したいときは connect_zenoh.bash を使う。こちらは compose の
# zenoh-remote サービス (make remote) から呼ばれる想定。
set -eo pipefail

if [ "$#" -lt 1 ] || [ -z "${1}" ]; then
    echo 'usage: run_zenoh.bash "A2 A3 A7" [LOG_DIR]' >&2
    exit 1
fi

read -r -a vehicles <<<"${1}"
log_dir="${2-}"
out_dir="${log_dir:+${log_dir}/remote}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/remote}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ポート表は vehicle/vehicle_ports.sh が唯一の出どころ。ここで複製しない。
# shellcheck source-path=SCRIPTDIR source=../vehicle/vehicle_ports.sh
source "${script_dir}/../vehicle/vehicle_ports.sh"

template="${script_dir}/zenoh-user.json5.template"
router_host="zenoh.dev.aichallenge-board.jsae.or.jp"

# 遠隔側は常に domain 0。ここで全車両がまとめて見える。
export ROS_DOMAIN_ID=0

# 車両IDを先に全部検証する。1台でも綴り違いがあれば1本も起動せずに落とす。半端に上がると
# その車両だけ永久に UNKNOWN になり、停止確認が取れずすべての操作が塞がるため。
for vehicle_id in "${vehicles[@]}"; do
    if ! zenoh_port_for_vehicle_id "${vehicle_id}" >/dev/null; then
        echo "Invalid VEHICLE_ID: ${vehicle_id} (valid: ${VEHICLE_ID_VALID_LIST})" >&2
        exit 1
    fi
done

mkdir -p "${out_dir}"

pids=()
for vehicle_id in "${vehicles[@]}"; do
    port="$(zenoh_port_for_vehicle_id "${vehicle_id}")"

    # 設定は実機・リハーサル共通のテンプレートから生成する。車両IDとmTLS資材の場所だけを
    # 埋める。資材の場所が可変なのは、このスクリプトがコンテナ内 (/remote)、
    # connect_zenoh.bash がホスト (リポジトリの remote/) で動くため。
    config="${out_dir}/zenoh-user-${vehicle_id}.json5"
    sed -e "s/__VEHICLE_ID__/${vehicle_id}/g" \
        -e "s#__TLS_DIR__#${script_dir}#g" \
        "${template}" >"${config}"

    (
        exec >>"${out_dir}/zenoh-${vehicle_id}.log" 2>&1
        while true; do
            echo "[run_zenoh] ${vehicle_id}: connecting to ${router_host}:${port}"
            status=0
            zenoh-bridge-ros2dds client -e "tls/${router_host}:${port}" -c "${config}" || status=$?
            echo "[run_zenoh] ${vehicle_id}: bridge exited with status ${status}; retrying in 5s..."
            sleep 5
        done
    ) &
    pids+=("$!")
    echo "[run_zenoh] ${vehicle_id}: port ${port} -> ${out_dir}/zenoh-${vehicle_id}.log"
done

echo "[run_zenoh] ${#pids[@]} bridge(s) up on ROS_DOMAIN_ID 0: ${vehicles[*]}"

# 監視プロセスが1つでも消えたら全部畳んで非ゼロで終わる。ブリッジ単体の落下は上の
# リトライループが面倒みるので、ここに来るのは kill されたときだけ。
trap 'kill "${pids[@]}" 2>/dev/null || true' TERM INT
wait -n || true
echo "[run_zenoh] a supervisor exited; shutting down the remaining bridges" >&2
kill "${pids[@]}" 2>/dev/null || true
wait || true
exit 1
