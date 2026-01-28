#!/bin/bash

set -euo pipefail

log() {
    if [ "${MOVE_WINDOW_DEBUG:-0}" != "0" ] || [ "${MOVE_WINDOW_QUIET:-1}" != "1" ]; then
        echo "[move_window] $*"
    fi
}

warn() {
    echo "[move_window][WARN] $*" >&2
}

AWSIM_TITLE_REGEX="${AWSIM_TITLE_REGEX:-^AWSIM($|[[:space:]-])}"
AWSIM_CLASS_REGEX="${AWSIM_CLASS_REGEX:-AWSIM}"
RVIZ_TITLE_REGEX="${RVIZ_TITLE_REGEX:-^RViz}"
RVIZ_CLASS_REGEX="${RVIZ_CLASS_REGEX:-rviz}"
MOVE_WINDOW_DEBUG="${MOVE_WINDOW_DEBUG:-0}"
MOVE_WINDOW_PREFER_LARGEST="${MOVE_WINDOW_PREFER_LARGEST:-1}"
MOVE_WINDOW_QUIET="${MOVE_WINDOW_QUIET:-1}"

log "Move window"

if ! command -v wmctrl >/dev/null 2>&1; then
    log "wmctrl command not available. Skipping window management."
    exit 0
fi

get_current_desktop() {
    wmctrl -d 2>/dev/null | awk '$2 == "*" {print $1; exit}' || true
}

pick_window_id() {
    local title_re="$1"
    local class_re="$2"
    local desktop="${3-}"

    local -a matches=()
    if [ -n "${desktop}" ]; then
        mapfile -t matches < <(wmctrl -lpx 2>/dev/null | awk -v title_re="$title_re" -v class_re="$class_re" -v desktop="$desktop" '
            BEGIN { IGNORECASE = 1 }
            {
                id = $1;
                desk = $2;
                cls = $5;
                $1 = $2 = $3 = $4 = $5 = "";
                sub(/^ +/, "", $0);
                title = $0;
                if ((desk == desktop || desk == -1) && title ~ title_re && cls ~ class_re) {
                    print id "\t" cls "\t" title;
                }
            }')
    else
        mapfile -t matches < <(wmctrl -lpx 2>/dev/null | awk -v title_re="$title_re" -v class_re="$class_re" '
            BEGIN { IGNORECASE = 1 }
            {
                id = $1;
                cls = $5;
                $1 = $2 = $3 = $4 = $5 = "";
                sub(/^ +/, "", $0);
                title = $0;
                if (title ~ title_re && cls ~ class_re) {
                    print id "\t" cls "\t" title;
                }
            }')
    fi

    if [ "${#matches[@]}" -eq 0 ]; then
        echo ""
        return 0
    fi

    declare -A width_by_id=()
    declare -A height_by_id=()
    if [ "${MOVE_WINDOW_PREFER_LARGEST}" != "0" ]; then
        while IFS=$'\t' read -r id w h; do
            [ -n "${id}" ] || continue
            width_by_id["$id"]="$w"
            height_by_id["$id"]="$h"
        done < <(wmctrl -lG 2>/dev/null | awk '{print $1 "\t" $5 "\t" $6}')
    fi

    if [ "${MOVE_WINDOW_DEBUG}" != "0" ]; then
        warn "Candidate windows for title_re=$title_re class_re=$class_re:"
        local line id cls title w h
        for line in "${matches[@]}"; do
            IFS=$'\t' read -r id cls title <<<"$line"
            w="${width_by_id[$id]:-?}"
            h="${height_by_id[$id]:-?}"
            warn "  id=$id geom=${w}x${h} class=$cls title=$title"
        done
    fi

    local best_id=""
    local best_area=-1
    local best_dec=-1
    local line id cls title w h area dec
    for line in "${matches[@]}"; do
        IFS=$'\t' read -r id cls title <<<"$line"
        [[ $id =~ ^0x[0-9a-fA-F]+$ ]] || continue

        w="${width_by_id[$id]-}"
        h="${height_by_id[$id]-}"
        area=-1
        if [ -n "${w}" ] && [ -n "${h}" ] && [[ ${w} =~ ^[0-9]+$ ]] && [[ ${h} =~ ^[0-9]+$ ]]; then
            area=$((w * h))
        fi
        dec=$((id))

        if [ "${MOVE_WINDOW_PREFER_LARGEST}" != "0" ] && [ "$area" -ge 0 ]; then
            if [ "$area" -gt "$best_area" ] || { [ "$area" -eq "$best_area" ] && [ "$dec" -gt "$best_dec" ]; }; then
                best_area="$area"
                best_dec="$dec"
                best_id="$id"
            fi
        else
            if [ "$dec" -gt "$best_dec" ]; then
                best_dec="$dec"
                best_id="$id"
            fi
        fi
    done

    if [ -n "$best_id" ] && [ "${#matches[@]}" -gt 1 ]; then
        if [ "${MOVE_WINDOW_PREFER_LARGEST}" != "0" ]; then
            warn "Multiple windows matched (title_re=$title_re, class_re=$class_re). Picking largest: $best_id"
        else
            warn "Multiple windows matched (title_re=$title_re, class_re=$class_re). Picking newest: $best_id"
        fi
    fi
    echo "$best_id"
}

has_gpu=0
if command -v nvidia-smi >/dev/null 2>&1; then
    has_gpu=1
fi

timeout_s=10
elapsed=0

desktop="$(get_current_desktop)"
awsim_id=""
rviz_id=""
has_awsim=0
has_rviz=0
while [ $elapsed -lt $timeout_s ]; do
    awsim_id="$(pick_window_id "$AWSIM_TITLE_REGEX" "$AWSIM_CLASS_REGEX" "$desktop")"
    rviz_id="$(pick_window_id "$RVIZ_TITLE_REGEX" "$RVIZ_CLASS_REGEX" "$desktop")"
    [ -n "$awsim_id" ] && has_awsim=1 || has_awsim=0
    [ -n "$rviz_id" ] && has_rviz=1 || has_rviz=0

    if [ "$has_rviz" -eq 1 ] && { [ "$has_awsim" -eq 1 ] || [ "$has_gpu" -eq 0 ]; }; then
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
    log "Move window: ${elapsed}s elapsed"
done

if [ $elapsed -ge $timeout_s ]; then
    warn "Timeout waiting for AWSIM/RViz windows after ${timeout_s}s"
    warn "AWSIM window found: $has_awsim"
    warn "RViz window found: $has_rviz"
    warn "GPU available: $has_gpu"
    warn "Continuing without window positioning..."
    exit 1
fi

log "AWSIM and RViz windows found"

{ wmctrl -i -a "$rviz_id" && wmctrl -i -r "$rviz_id" -e 0,0,0,1920,1043; } || true
sleep 1
if [ -n "$awsim_id" ]; then
    { wmctrl -i -a "$awsim_id" && wmctrl -i -r "$awsim_id" -e 0,0,0,900,1043; } || true
fi
sleep 2
