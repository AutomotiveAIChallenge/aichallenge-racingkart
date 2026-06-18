#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

# 車両数: 第1引数（既定 1）
vehicles="${1:-1}"

$AWSIM_DIRECTORY/AWSIM.x86_64 \
    --start-mode count \
    --start-count-seconds 5 \
    --vehicles "${vehicles}" \
    --npcs 0 \
    --boosts 2 \
    --laps unlimited \
    --timeout 10000000.0 \
    --steer-source ackermann \
    --sound off \
    --collisions on \
    --handicap off \
    --wall-recovery on \
    --ranking off \
    --camera off \
    --lidar off

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
