#!/bin/bash
AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM

mode="${1}"

if [[ -e /dev/nvidia0 ]]; then
    echo "[INFO] NVIDIA GPU detected"
    opts=()
else
    echo "[INFO] No NVIDIA GPU detected → running on headless mode"
    # opts=("-headless")
    opts=("--camera" "false" "--lidar" "false")
fi

case "${mode}" in
"dev")
    opts+=("--vehicles" "1" "--laps" "600" "--timeout" "60000000")
    ;;
"eval")
    opts+=("--vehicles" "1" "--laps" "6" "--timeout" "600")
    ;;
"2p")
    opts+=("--vehicles" "2" "--laps" "6" "--timeout" "1200")
    ;;
"3p")
    opts+=("--vehicles" "3" "--laps" "6" "--timeout" "1200")
    ;;
"4p")
    opts+=("--vehicles" "4" "--laps" "6" "--timeout" "1200")
    ;;
*) ;;
esac

# shellcheck disable=SC1091
source /aichallenge/workspace/install/setup.bash
sudo ip link set multicast on lo
sudo sysctl -w net.core.rmem_max=2147483647 >/dev/null
export ROS_DOMAIN_ID=0
$AWSIM_DIRECTORY/AWSIM.x86_64 "${opts[@]}"
