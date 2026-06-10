#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

$AWSIM_DIRECTORY/AWSIM.x86_64 \
    --camera off \
    --lidar off \
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
    -window-mode borderless # Unity default arg

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
