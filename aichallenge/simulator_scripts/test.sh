#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

$AWSIM_DIRECTORY/AWSIM.x86_64 \
    --camera false \
    --lidar false \
    --start-mode sync \
    --start-count-seconds 5 \
    --vehicles 1 \
    --npcs 0 \
    --boosts 2 \
    --laps 1 \
    --timeout 90 \
    --steer-source ackermann \
    --sound off \
    --collisions on \
    --handicap on \
    --wall-recovery on \
    --ranking on \
    --ros2-base-domain 1

# GPU描画を使う場合の書き換え:
#   カメラ: --camera false -> real（フル画像）または lite（軽量・GPU非搭載でも可）
#   LiDAR : --lidar false -> on（CPU版は末尾に --lidar-backend unity、GPU版は rgl を追加）
