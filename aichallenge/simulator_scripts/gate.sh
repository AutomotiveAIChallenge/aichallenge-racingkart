#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

# テスト指定: 第1引数（1/2/3 または all、既定 all = test1〜3 を順次実行）
test="${1:-all}"

$AWSIM_DIRECTORY/AWSIM.x86_64 \
    --vehicles 1 \
    --safety-gate "${test}" \
    --steer-source ackermann \
    --sound off \
    --collisions on \
    --handicap off \
    --ranking off \
    --camera off \
    --lidar off

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
