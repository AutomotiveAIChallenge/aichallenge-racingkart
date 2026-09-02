#!/bin/bash

# Safety gate for the evaluation environment (run_safety_gate.bash): all scenarios, fixed args.
# For per-test debugging use gate.sh (make gate1..gate3).
AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

exec "$AWSIM_DIRECTORY/AWSIM.x86_64" \
    --vehicles 1 \
    --safety-gate "all" \
    --steer-source ackermann \
    --sound off \
    --collisions on \
    --handicap off \
    --ranking off \
    --camera off \
    --lidar off \
    -screen-fullscreen 1 \
    -screen-width 1280 \
    -screen-height 720 \
    -screen-quality Low \
    -window-mode borderless # Unity default arg

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
