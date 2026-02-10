#!/bin/bash

SCRIPT_DIR=`dirname $0`
cd ${SCRIPT_DIR}

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  source .venv/bin/activate
  pip install matplotlib
  pip install pyyaml
else
  source .venv/bin/activate
fi

python3 create_waypoints.py --obs