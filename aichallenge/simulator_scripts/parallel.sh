#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

$AWSIM_DIRECTORY/AWSIM.x86_64 \
    --camera false \
    --lidar false \
    --start-mode sync \
    --start-count-seconds 5 \
    --vehicles 3 \
    --npcs 0 \
    --boosts 2 \
    --laps 6 \
    --timeout 600 \
    --steer-source ackermann \
    --sound off \
    --collisions off \
    --handicap on \
    --wall-recovery on \
    --ranking on \
    -screen-fullscreen 1 \
    -screen-width 1920 \
    -screen-height 1080 \
    -screen-quality low \
    --window-mode borderless

# GPU描画を使う場合の書き換え:
#   カメラ: --camera false -> real（フル画像）または lite（軽量・GPU非搭載でも可）
#   LiDAR : --lidar false -> on（CPU版は末尾に --lidar-backend unity、GPU版は rgl を追加）
