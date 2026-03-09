#!/bin/bash

mode="${1}"

case "${mode}" in
"awsim")
    opts=("use_sim_time:=true")
    ;;
"vehicle")
    opts=("use_sim_time:=false")
    ;;
"remote")
    opts=("use_sim_time:=false")
    ros2 launch aichallenge_system_launch remote.launch.xml "use_sim_time:=false" &
    ;;
*)
    echo "invalid argument (use 'awsim', 'vehicle', or 'remote')"
    exit 1
    ;;
esac

rviz2 -d /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/autoware_vehicle.rviz \
    -s /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/fast.png \
    --ros-args --remap "${opts[@]}"
# rviz2 -d /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/debug_sensing.rviz
