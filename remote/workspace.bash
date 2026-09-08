#!/bin/bash
# 遠隔操作用ワークスペース。terminator を 4 分割 (ssh×3 + GUI tools) で立ち上げる。
#   workspace.bash              terminator を起動 (make workspace から呼ばれる)
#   workspace.bash pane <role>  各ペイン内で実行され、ヒントを表示して bash に移る
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

CONNECT="./remote/connect_ssh.bash <A2|A3|A6|A7|test>"

hint() {
    echo "================================================================"
    printf '%s\n' "$@"
    echo "================================================================"
    echo
}

if [ $# -eq 0 ]; then
    cd "${REPO_ROOT}"
    # -u: 既存 terminator への DBus 委譲を避ける (委譲先は自分の config しか見ずレイアウトが無視される)
    exec terminator -u -g remote/terminator.config -l aic-workspace
fi

if [ "$1" != "pane" ] || [ $# -ne 2 ]; then
    echo "Usage: $0 [pane tui|monitor|spare|gui]" >&2
    exit 1
fi

case "$2" in
tui)
    hint "[ssh 1/3] 車両 TUI" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make vehicle-tui"
    ;;
monitor)
    hint "[ssh 2/3] 監視用" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make autoware-bash" \
        "  (autoware コンテナ内の bash が開く。ros2 topic echo / ros2 node list などで状態を見る)"
    ;;
spare)
    hint "[ssh 3/3] 予備" \
        "  ${CONNECT}" \
        "  自由に使うシェル"
    ;;
gui)
    hint "[GUI tools] remote/gui_tools.py を起動中" \
        "  zenoh / RViz / joy はこの GUI から操作する" \
        "  GUI を閉じるとこのシェルに戻る"
    ./remote/gui_tools.py || true
    ;;
*)
    echo "Unknown pane role: $2" >&2
    exit 1
    ;;
esac

exec bash
