#!/bin/bash

mode="${1}"
id="${2:-${ROS_DOMAIN_ID:-0}}"
out_dir="${3:+${3}/d${id}}"
out_dir="${out_dir:-/output/$(date +%Y%m%d-%H%M%S)/d${id}}"

export ROS_DOMAIN_ID=$id

mkdir -p "${out_dir}"
exec >"${out_dir}/driver.log" 2>&1

cd "${out_dir}" || exit
export ROS_HOME="${out_dir}/ros"
export ROS_LOG_DIR="${ROS_HOME}/log"
mkdir -p "${ROS_LOG_DIR}"

/entrypoint.sh "${mode}" "${@:4}"
