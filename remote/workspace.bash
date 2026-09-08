#!/bin/bash
# 遠隔操作用ワークスペース。terminator を 4 分割 (ssh×3 + GUI tools) で立ち上げる。
#   workspace.bash                          terminator を起動 (make workspace から呼ばれる)
#   workspace.bash tui|monitor|spare|gui    各ペイン内で実行され、ヒントを表示して bash に移る
set -euo pipefail

CONNECT="./remote/connect_ssh.bash <A2|A3|A6|A7|test>"

hint() {
    echo "================================================================"
    printf '%s\n' "$@"
    echo "================================================================"
    # ペインは小さく生成された後に最大化で広がるため、bash の再描画が直前の行を潰す。
    # その犠牲用に空行を 1 つ置く。
    echo
}

case "${1-}" in
"")
    cd "$(dirname "$0")/.."
    # terminator は -g で渡したファイルを終了時に書き戻すので、リポジトリのファイルは直接渡さない。
    # -u: 既存 terminator への DBus 委譲を避ける (委譲先は自分の config しか見ずレイアウトが無視される)
    cfg=$(mktemp)
    trap 'rm -f "$cfg"' EXIT
    cp remote/terminator.config "$cfg"
    terminator -u -g "$cfg" -l aic-workspace
    ;;
tui)
    hint "[ssh 1/3] 車両 TUI" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make vehicle-tui"
    exec bash
    ;;
monitor)
    hint "[ssh 2/3] 監視用" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make autoware-bash" \
        "  (autoware コンテナ内の bash が開く。ros2 topic echo / ros2 node list などで状態を見る)"
    exec bash
    ;;
spare)
    hint "[ssh 3/3] 予備" \
        "  ${CONNECT}" \
        "  自由に使うシェル"
    exec bash
    ;;
gui)
    hint "[GUI tools] remote/gui_tools.py を起動中" \
        "  zenoh / RViz / joy はこの GUI から操作する" \
        "  GUI を閉じるとこのシェルに戻る"
    ./remote/gui_tools.py || true
    exec bash
    ;;
*)
    echo "Usage: $0 [tui|monitor|spare|gui]" >&2
    exit 1
    ;;
esac
