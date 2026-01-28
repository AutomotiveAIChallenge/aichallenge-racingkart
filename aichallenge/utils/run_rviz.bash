#!/bin/bash
# shellcheck disable=SC1091

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
    source /aichallenge/workspace/install/setup.bash
    sudo ip link set multicast on lo
    sudo sysctl -w net.core.rmem_max=2147483647 >/dev/null
    ros2 launch aichallenge_system_launch remote.launch.xml "use_sim_time:=false" &
    ;;
*)
    echo "invalid argument (use 'awsim', 'vehicle', or 'remote')"
    exit 1
    ;;
esac

source /aichallenge/workspace/install/setup.bash
sudo ip link set multicast on lo
sudo sysctl -w net.core.rmem_max=2147483647 >/dev/null

rviz2 -d /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/autoware_vehicle.rviz \
    -s /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/fast.png \
    --ros-args --remap "${opts[@]}"
# rviz2 -d /aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/config/debug_sensing.rviz
