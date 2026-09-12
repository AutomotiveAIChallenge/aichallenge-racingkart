# shellcheck shell=bash
#
# Shared VEHICLE_ID mappings for the racing kart fleet.
#
# Sourced from both the host (vehicle/setup_check.sh) and from inside containers
# (/vehicle/run_zenoh.bash), since ./vehicle is mounted at /vehicle.
#
# Add a new vehicle here only. Do not duplicate these mappings in callers.

# shellcheck disable=SC2034  # consumed by the scripts that source this file
VEHICLE_ID_VALID_LIST="A1, A2, A3, A5, A6, A7, A8, test"

# VEHICLE_ID -> Zenoh bridge port on the tournament server.
zenoh_port_for_vehicle_id() {
    case "$1" in
    A2) echo 7448 ;;
    A3) echo 7449 ;;
    A6) echo 7450 ;;
    A7) echo 7451 ;;
    A1) echo 7452 ;;
    A5) echo 7453 ;;
    A8) echo 7454 ;;
    *) return 1 ;;
    esac
}

# VEHICLE_ID -> Zenoh endpoint the vehicle bridge connects to.
# "test" targets a local zenohd (remote/connect_zenoh.bash test-server) over plain
# tcp; override the address with ZENOH_LOCAL_ENDPOINT.
zenoh_endpoint_for_vehicle_id() {
    local port
    case "$1" in
    test) echo "${ZENOH_LOCAL_ENDPOINT:-tcp/127.0.0.1:7448}" ;;
    *)
        port="$(zenoh_port_for_vehicle_id "$1")" || return 1
        echo "tls/zenoh.dev.aichallenge-board.jsae.or.jp:${port}"
        ;;
    esac
}

# VEHICLE_ID -> ROS namespace on the Zenoh side. "test" reuses /A2 so that the
# remote side (connect_zenoh.bash test-remote) sees the same namespace.
zenoh_namespace_for_vehicle_id() {
    case "$1" in
    test) echo "/A2" ;;
    *) echo "/$1" ;;
    esac
}

# ECU hostname -> VEHICLE_ID, used as a fallback when neither the environment
# nor .env provides VEHICLE_ID.
vehicle_id_for_hostname() {
    case "$1" in
    ECU-RK-01) echo A2 ;;
    ECU-RK-02) echo A3 ;;
    ECU-RK-06) echo A6 ;;
    ECU-RK-00) echo A7 ;;
    *) return 1 ;;
    esac
}
