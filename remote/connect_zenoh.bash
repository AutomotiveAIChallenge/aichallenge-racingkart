#!/bin/bash

# スクリプトに引数が1つだけ渡されているかチェック
if [ "$#" -ne 1 ]; then
    echo "エラー: Vechicle IDを指定してください。" >&2
    echo "使用法: $0 {A1|A2|A3|A5|A6|A7|A8|test-*}" >&2
    exit 1
fi

NAMESPACE=$1

case "$NAMESPACE" in
A2)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' (ECU-RK-01) - Port 7448"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7448 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
A3)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' (ECU-RK-02) - Port 7449"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7449 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
A6)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' (ECU-RK-06) - Port 7450"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7450 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
A7)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' (ECU-RK-00) - Port 7451"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7451 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
A1)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' - Port 7452"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7452 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
A5)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' - Port 7453"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7453 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
A8)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' - Port 7454"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:7454 \
        -n "/${NAMESPACE}" \
        -c zenoh-user.json5
    ;;
test-remote)
    ENDPOINT="${ZENOH_LOCAL_ENDPOINT:-tcp/127.0.0.1:7448}"
    echo "Connecting Zenoh. Target Vehicle: 'local' - Endpoint ${ENDPOINT}"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e "${ENDPOINT}" \
        -n /A2 \
        -c zenoh-user.json5
    ;;
test-vehicle)
    ENDPOINT="${ZENOH_LOCAL_ENDPOINT:-tcp/127.0.0.1:7448}"
    echo "Connecting Zenoh. Target Vehicle: 'local' - Endpoint ${ENDPOINT}"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e "${ENDPOINT}" \
        -n /A2 \
        -c ../vehicle/zenoh.json5
    ;;
test-server)
    zenohd --listen tcp/127.0.0.1:7448
    ;;
*)
    echo "エラー: 無効な名前空間です: '$NAMESPACE'" >&2
    echo "A1, A2, A3, A5, A6, A7, A8, test-* のいずれかを指定してください。" >&2
    exit 1
    ;;
esac
