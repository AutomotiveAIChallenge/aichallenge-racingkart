#!/bin/bash

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

$AWSIM_DIRECTORY/AWSIM.x86_64 \
    --multiplay host \
    --multiplay-address 127.0.0.1 \
    --multiplay-port 7777 \
    --multiplay-vehicle-index 1 \
    --handicap off \
    --ranking off \
    --vehicles 1
