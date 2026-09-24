#!/bin/bash

# 接続先は引数、なければリポジトリの .env の VEHICLE_ID を使う。
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_VEHICLE_ID=""
if [ -f "$REPO_ROOT/.env" ]; then
    ENV_VEHICLE_ID=$(grep -E '^VEHICLE_ID=' "$REPO_ROOT/.env" | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' ')
fi

usage() {
    echo "使用法: $0 [[ユーザー名@]<A1-A8|test>] [実行するコマンド]"
    echo "  接続先を省略すると .env の VEHICLE_ID (現在: ${ENV_VEHICLE_ID:-未設定}) に接続する"
    echo "  車両 ID は小文字に変換して SSH の接続先に使う (例: A3 → a3)"
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
# 引数と .env のどちらから取得した車両 ID も小文字に統一する。
TARGET_ID=${TARGET_ID,,}
host="$TARGET_ID"
PORT_ARGS=()

# 2. 車両名で接続する。接続設定は SSH の設定に従う。
case "$TARGET_ID" in
a[1-8]) ;;
test)
    host="localhost"
    PORT_ARGS=(-p 22)
    ;;
*)
    echo "エラー: 不明な接続先です: $TARGET_ID"
    echo "利用可能な接続先: A1-A8, test (小文字も可)"
    usage
    exit 1
    ;;
esac

# 3. コマンド実行は ssh、対話接続は autossh を使う。
# 2番目以降の引数（現在は "$@" に格納されている）があれば、それがリモートコマンドとして実行される
if [ $# -gt 0 ]; then
    # 終了コードをそのまま返し、接続切断時にコマンドを再実行しない。
    SSH_COMMAND=(ssh)
    echo "Connecting to $TARGET_ID as $USERNAME to run command: '$*'"
else
    # コマンドが指定されていない場合（インタラクティブ接続）
    SSH_COMMAND=(autossh -M 0)
    echo "Connecting... Target Vehicle: $TARGET_ID, User: $USERNAME"
fi

exec "${SSH_COMMAND[@]}" -AC "${PORT_ARGS[@]}" \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    "${USERNAME}@${host}" \
    "$@" # 2番目以降の引数をすべてコマンドとして渡す
