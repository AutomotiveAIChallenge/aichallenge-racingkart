#!/bin/bash
# 遠隔操作用ワークスペース。terminator を 4 分割 (ssh×3 + GUI tools) で立ち上げる。
#   workspace.bash                          terminator を起動 (make workspace から呼ばれる)
#   workspace.bash tui|monitor|staff|gui    各ペイン内で実行され、ヒントを表示して bash に移る
set -euo pipefail

# 接続予定の車両。connect_ssh.bash が引数なしのとき見るのと同じ .env の VEHICLE_ID。
VEHICLE_ID=""
ENV_FILE="$(dirname "$0")/../.env"
if [ -f "$ENV_FILE" ]; then
    VEHICLE_ID=$(grep -E '^VEHICLE_ID=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' ')
fi
if [ -n "$VEHICLE_ID" ]; then
    TARGET="接続先: ${VEHICLE_ID} (.env の VEHICLE_ID)"
else
    TARGET="接続先: 未設定 (.env に VEHICLE_ID がない。connect_ssh.bash に引数で渡す)"
fi
CONNECT="遠隔ssh操作：./remote/connect_ssh.bash"

hint() {
    echo "================================================================"
    printf '%s\n' "$@"
    echo "================================================================"
    # ペインは小さく生成された後に最大化で広がるため、bash の再描画が直前の行を潰す。
    # その犠牲用に空行を 1 つ置く。
    echo
}

# ヒントのコマンドを bash の履歴に入れて exec する。ペインで ↑ を押せば貼らずに出てくる。
# 引数の順に登録するので、最後の引数が ↑ 1 回目になる。
exec_bash_with_history() {
    local rc
    rc=$(mktemp)
    {
        [ -f ~/.bashrc ] && cat ~/.bashrc
        echo "rm -f '$rc'"
        printf 'history -s %q\n' "$@"
    } >"$rc"
    exec bash --rcfile "$rc"
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
    hint "[ssh 1/3] 車両 TUI  ${TARGET}" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make vehicle-tui" \
        "  (↑ キーで両方のコマンドが出る)"
    exec_bash_with_history \
        "cd aichallenge-racingkart && make vehicle-tui" \
        "./remote/connect_ssh.bash"
    ;;
monitor)
    hint "[ssh 2/3] 監視用  ${TARGET}" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make autoware-bash" \
        "  (autoware コンテナ内の bash が開く。ros2 topic echo / ros2 node list などで状態を見る)"
    exec bash
    ;;
staff)
    hint "[ssh 3/3] 運営 TUI  ${TARGET}" \
        "  ${CONNECT}" \
        "  接続後、車両側で: cd aichallenge-racingkart && make vehicle-tui-staff" \
        "  (↑ キーで両方のコマンドが出る)"
    exec_bash_with_history \
        "cd aichallenge-racingkart && make vehicle-tui-staff" \
        "./remote/connect_ssh.bash"
    ;;
gui)
    hint "[GUI tools] remote/gui_tools.py を起動中" \
        "  zenoh / RViz / joy はこの GUI から操作する" \
        "  GUI を閉じるとこのシェルに戻る"
    ./remote/gui_tools.py || true
    exec bash
    ;;
*)
    echo "Usage: $0 [tui|monitor|staff|gui]" >&2
    exit 1
    ;;
esac
