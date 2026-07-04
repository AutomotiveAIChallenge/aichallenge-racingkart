#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

SCENARIO_DIRECTORY=/aichallenge/simulator/AWSIM/AWSIM_Data/StreamingAssets/Race/official.yaml

exec $AWSIM_DIRECTORY/AWSIM.x86_64 \
    --sound off \
    --collisions on \
    --scenario "${SCENARIO_DIRECTORY}" \
    --camera off \
    --lidar off

# Cameraを使う場合 : --camera cpu or gpu
# LiDARを使う場合 : --lidar cpu or gpu
# GPUがない場合 -headlessを末尾に追加
