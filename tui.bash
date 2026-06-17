#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-${0-}}"
case "$SCRIPT_PATH" in
*/*) SCRIPT_DIR="${SCRIPT_PATH%/*}" ;;
*) SCRIPT_DIR="." ;;
esac
SCRIPT_DIR="$(cd "$SCRIPT_DIR" && pwd)"
cd "$SCRIPT_DIR"

exec python3 ./tui.py "$@"
