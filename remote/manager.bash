#!/bin/bash
# racing_kart_manager と操作 GUI の起動。
#
#   manager.bash manager A2 A3 A7   # manager ノード（対象車両を指定）
#   manager.bash gui                # 操作 GUI（対象車両は status から知る）
#
# manager と GUI は別プロセス。GUI が落ちても manager は joy を流し続ける。
set -eo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# ROS 環境。docker-entrypoint.sh を経由しない起動でも動くように自分で読む。
# setup.bash は未定義変数を触るので set -u は使わない。
# shellcheck disable=SC1091
{
    [ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
    [ -f /autoware/install/setup.bash ] && source /autoware/install/setup.bash
    [ -f /aichallenge/workspace/install/setup.bash ] &&
        source /aichallenge/workspace/install/setup.bash
} >/dev/null 2>&1 || true

if ! python3 -c "import rclpy" 2>/dev/null; then
    echo "Error: rclpy が見つかりません。コンテナ内で実行してください。" >&2
    exit 1
fi

# 起動時点の欠落はここで落とす。manager 自身は racing_kart_msgs が無くても起動でき、
# emergency を UNKNOWN 扱いにして全操作を塞ぐ (通信途絶と同じ安全側の挙動)。ただし GUI に
# 出るのは「状態不明」であって、オペレータには原因がビルド漏れだと分からない。実行中の
# 途絶と起動時の未ビルドを区別するため、後者は起動前に非ゼロで終わらせる。
if ! python3 -c "import racing_kart_msgs.msg" 2>/dev/null; then
    echo "Error: racing_kart_msgs が見つかりません。" >&2
    echo "       make autoware-build を実行してください。" >&2
    exit 1
fi

cd "${SCRIPT_DIR}"

case "${1:-manager}" in
manager)
    shift || true
    exec python3 racing_kart_manager.py "$@"
    ;;
gui)
    exec python3 racing_kart_manager_gui.py
    ;;
*)
    echo "Usage: $0 manager <VEHICLE_ID> [VEHICLE_ID ...]" >&2
    echo "       $0 gui" >&2
    exit 1
    ;;
esac
