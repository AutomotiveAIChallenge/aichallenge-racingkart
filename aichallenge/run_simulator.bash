#!/bin/bash
AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM

mode="${1}"

if [[ -e /dev/nvidia0 ]]; then
    echo "[INFO] NVIDIA GPU detected"
    opts=()
else
    echo "[INFO] No NVIDIA GPU detected → running on headless mode"
    opts=("-headless")
fi

case "${mode}" in
"endless")
    opts+=(" --endless")
    ;;
*) ;;
esac

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source /autoware/install/setup.bash
# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash
sudo ip link set multicast on lo
sudo sysctl -w net.core.rmem_max=2147483647 >/dev/null
export ROS_DOMAIN_ID=0
export ROS_LOCAL_HOST_ONLY=1
$AWSIM_DIRECTORY/AWSIM.x86_64 "${opts[@]}"
