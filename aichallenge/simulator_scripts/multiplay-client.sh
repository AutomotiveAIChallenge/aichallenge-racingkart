#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

exec $AWSIM_DIRECTORY/AWSIM.x86_64 \
    --collisions off \
    --start-mode count \
    --start-count-seconds 0 \
    --wall-recovery off \
    --laps unlimited \
    --timeout 10000000.0 \
    --multiplay client \
    --multiplay-address 10.0.0.1 \
    --multiplay-port 7777 \
    --sound off \
    --camera cpu \
    --lidar cpu \
    --multiplay-name <あなたの名前>
