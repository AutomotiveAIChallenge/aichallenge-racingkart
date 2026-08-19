#!/bin/bash

mode="${1}"
vehicle_id="${2-}"

# 遠隔PCでは zenoh 経由の車両トピックが /<VEHICLE_ID>/... の prefix 付きで届くが、
# autoware_vehicle.rviz が購読するのは prefix なしの名前なので、ここで中継して噛み合わせる。
#
# 中継先を列挙しているのは意図的。/<VEHICLE_ID>/* を丸ごと中継すると、車両から返ってくる
# /<VEHICLE_ID>/racing_kart/joy がローカルの /racing_kart/joy に流れ込み、manager が
# 自分の出したジョイスティック入力のエコーを掴む。
#
# /tf_static は入れない。base_link 配下は remote.launch.xml の robot_state_publisher が、
# map->viewer は同 launch の map_tf_generator がローカルで出している。加えて static TF は
# transient_local なので topic_tools relay では中継できない。
RELAY_TOPICS=(
    /tf
    /localization/kinematic_state
    /planning/scenario_planning/trajectory
    /vehicle/status/velocity_status
    /sensing/gnss/pose_with_covariance
)

start_relays() {
    local id="${1}"
    local topic

    # 車両IDの一覧は vehicle/vehicle_ports.sh が唯一の出どころ。ここで複製しない。
    # コンテナ内では /aichallenge と /vehicle が、ホストでは aichallenge/ と vehicle/ が
    # それぞれ兄弟なので、同じ相対パスで両方から引ける。
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source-path=SCRIPTDIR source=../../vehicle/vehicle_ports.sh
    source "${script_dir}/../../vehicle/vehicle_ports.sh"

    # 綴り違いは黙って「何も映らない」になるだけなので、起動前に弾く。
    if ! zenoh_port_for_vehicle_id "${id}" >/dev/null; then
        echo "invalid VEHICLE: ${id} (valid: ${VEHICLE_ID_VALID_LIST})" >&2
        exit 1
    fi

    for topic in "${RELAY_TOPICS[@]}"; do
        echo "[run_rviz] relay /${id}${topic} -> ${topic}"
        ros2 run topic_tools relay "/${id}${topic}" "${topic}" &
    done
}

case "${mode}" in
"awsim")
    opts=("use_sim_time:=true")
    ;;
"vehicle")
    opts=("use_sim_time:=false")
    ;;
"remote")
    opts=("use_sim_time:=false")
    ros2 launch aichallenge_system_launch remote.launch.xml "use_sim_time:=false" &
    if [ -n "${vehicle_id}" ]; then
        start_relays "${vehicle_id}"
    else
        echo "[run_rviz] VEHICLE 未指定のため地図のみ表示します。車両を映すには make rviz2 VEHICLE=A3" >&2
    fi
    ;;
*)
    echo "invalid argument (use 'awsim', 'vehicle', or 'remote')"
    exit 1
    ;;
esac

rviz2 -d /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/autoware_vehicle.rviz \
    -s /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/fast.png \
    --ros-args --remap "${opts[@]}"
# rviz2 -d /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/debug_sensing.rviz
