#!/bin/bash
# racing_kart_manager と操作 GUI の起動。
#
#   manager.bash            # manager ノード
#   manager.bash gui        # 操作 GUI
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

cd "${SCRIPT_DIR}"

case "${1:-manager}" in
manager)
    exec python3 racing_kart_manager.py
    ;;
gui)
    exec python3 racing_kart_manager_gui.py
    ;;
*)
    echo "Usage: $0 {manager|gui}" >&2
    exit 1
    ;;
esac
