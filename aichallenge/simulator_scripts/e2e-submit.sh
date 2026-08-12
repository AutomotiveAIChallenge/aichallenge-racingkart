#!/bin/bash
# E2E の提出参考用（NPC を 2 体出現）

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

exec $AWSIM_DIRECTORY/AWSIM.x86_64 \
    --venue citycircuit \
    --start-mode count \
    --start-count-seconds 5 \
    --vehicles 1 \
    --npcs 2 \
    --boosts 2 \
    --laps 6 \
    --timeout 420.0 \
    --steer-source ackermann \
    --sound off \
    --collisions on \
    --handicap off \
    --wall-recovery off \
    --start-random off \
    --ranking off \
    --camera cpu \
    --lidar cpu \
    --imu off \
    --gnss off \
    --v2x off

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
