#!/bin/bash

# 1. 引数が2つ以上指定されているかチェック
if [ $# -lt 2 ]; then
    echo "エラー: 接続先とユーザー名を指定してください。 / Error: specify the target and the user name."
    echo "使用法 / Usage: $0 [A2|A3|A6|A7] ユーザー名/user [実行するコマンド/command]"
    exit 1
fi

TARGET_ID=$1
USERNAME=$2
PORT=""

# 2. 引数に応じてポート番号を設定
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
*)
    echo "エラー: 不明な接続先です / Error: unknown target: $TARGET_ID"
    echo "利用可能な接続先 / Available targets: A2, A3, A6, A7"
    exit 1
    ;;
esac

# 最初の2つの引数（接続先とユーザー名）を引数リストから削除
shift 2

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
    "${USERNAME}@zenoh.dev.aichallenge-board.jsae.or.jp" \
    "$@" # 3番目以降の引数をすべてコマンドとして渡す
