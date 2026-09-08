#!/bin/bash

# 1. 接続先が指定されているかチェック
if [ $# -lt 1 ]; then
    echo "エラー: 接続先を指定してください。"
    echo "使用法: $0 [A2|A3|A6|A7|test] [ユーザー名] [実行するコマンド]"
    echo "  ユーザー名を省略するとローカルのユーザー名 ($USER) で接続する"
    echo "  test: 踏み台を通さず localhost:22 へ接続する (動作確認用)"
    exit 1
fi

TARGET_ID=$1
USERNAME=${2:-$USER}
HOST="zenoh.dev.aichallenge-board.jsae.or.jp"
PORT=""

# 2. 引数に応じて接続先ホストとポート番号を設定
case "$TARGET_ID" in
A2)
    PORT=10025
    ;;
A3)
    PORT=10024
    ;;
A6)
    PORT=10023
    ;;
A7)
    PORT=10022
    ;;
test)
    HOST="localhost"
    PORT=22
    ;;
*)
    echo "エラー: 不明な接続先です: $TARGET_ID"
    echo "利用可能な接続先: A2, A3, A6, A7, test"
    exit 1
    ;;
esac

# 接続先とユーザー名を引数リストから削除
shift
[ $# -gt 0 ] && shift

# 3. 選択されたポートとユーザーでautosshを実行
# 3番目以降の引数（現在は "$@" に格納されている）があれば、それがリモートコマンドとして実行される
if [ $# -gt 0 ]; then
    # コマンドが指定されている場合
    echo "Connecting to $TARGET_ID as $USERNAME to run command: '$*'"
else
    # コマンドが指定されていない場合（インタラクティブ接続）
    echo "Connecting... Target Vehicle: $TARGET_ID, User: $USERNAME"
fi

autossh -AC -M 0 -p "$PORT" \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    "${USERNAME}@${HOST}" \
    "$@" # 3番目以降の引数をすべてコマンドとして渡す
