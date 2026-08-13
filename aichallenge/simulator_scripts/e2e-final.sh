#!/bin/bash
# E2E の決勝用（4台 / handicap・ranking あり）

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

exec $AWSIM_DIRECTORY/AWSIM.x86_64 \
    --venue citycircuit \
    --start-mode sync \
    --start-count-seconds 10 \
    --vehicles 4 \
    --npcs 0 \
    --boosts 2 \
    --laps 6 \
    --timeout 420.0 \
    --steer-source ackermann \
    --sound on \
    --collisions on \
    --handicap on \
    --wall-recovery off \
    --start-random off \
    --ranking on \
    --camera cpu \
    --lidar cpu \
    --imu off \
    --gnss off \
    --v2x off

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
