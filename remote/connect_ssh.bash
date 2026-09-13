#!/bin/bash

# 接続先は引数、なければリポジトリの .env の VEHICLE_ID を使う。
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_VEHICLE_ID=""
if [ -f "$REPO_ROOT/.env" ]; then
    ENV_VEHICLE_ID=$(grep -E '^VEHICLE_ID=' "$REPO_ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' ')
fi

usage() {
    echo "使用法: $0 [[ユーザー名@]<A2|A3|A6|A7|test>] [実行するコマンド]"
    echo "  接続先を省略すると .env の VEHICLE_ID (現在: ${ENV_VEHICLE_ID:-未設定}) に接続する"
    echo "  ユーザー名を省略するとローカルのユーザー名 ($USER) で接続する"
    echo "  test: 踏み台を通さず localhost:22 へ接続する (動作確認用)"
}

# 1. 接続先を決める
if [ $# -ge 1 ]; then
    SPEC=$1
    shift
elif [ -n "$ENV_VEHICLE_ID" ]; then
    SPEC=$ENV_VEHICLE_ID
else
    echo "エラー: 接続先を指定してください (.env に VEHICLE_ID もありません)。"
    usage
    exit 1
fi

# [ユーザー名@]接続先 として解釈する
USERNAME=$USER
TARGET_ID=$SPEC
if [[ $SPEC == *@* ]]; then
    USERNAME=${SPEC%@*}
    TARGET_ID=${SPEC#*@}
fi
host="zenoh.dev.aichallenge-board.jsae.or.jp"
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
    host="localhost"
    PORT=22
    ;;
*)
    echo "エラー: 不明な接続先です: $TARGET_ID"
    echo "利用可能な接続先: A2, A3, A6, A7, test"
    usage
    exit 1
    ;;
esac

# 3. 選択されたポートとユーザーでautosshを実行
# 2番目以降の引数（現在は "$@" に格納されている）があれば、それがリモートコマンドとして実行される
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
    "${USERNAME}@${host}" \
    "$@" # 2番目以降の引数をすべてコマンドとして渡す
