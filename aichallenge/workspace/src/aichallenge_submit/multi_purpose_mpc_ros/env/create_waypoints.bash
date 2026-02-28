#!/bin/bash

SCRIPT_DIR="$(dirname "$0")"
cd "${SCRIPT_DIR}" || exit

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install matplotlib
    pip install pyyaml
else
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

python3 create_waypoints.py
