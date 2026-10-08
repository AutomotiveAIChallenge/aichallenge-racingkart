#!/bin/bash
# 決勝練習用（make practice-4car が起動する）。既定値は s2r-final.sh と同じ引数で、差は --sound off のみ。
# 練習で変えたい条件だけを環境変数で切り替える（決勝スクリプト自体は変更しない）:
#   PRACTICE_CLASS=s2r|e2e      e2e にすると e2e-final.sh と同じセンサー構成・オーバーテイクレーン off
#   PRACTICE_HANDICAP=on|off    既定 on（決勝と同じ）
#   PRACTICE_NPCS=0..3          既定 0（決勝と同じ）
#   PRACTICE_VEHICLES=1..4      既定 4（決勝と同じ）
#   PRACTICE_HEADLESS=1         -headless（カメラ・LiDAR が無効になるので S2R のみ）
# 決勝スクリプトとの引数の食い違いは aichallenge/practice/test_practice.py が検査する。

AWSIM_DIRECTORY=/aichallenge/simulator/AWSIM
export ROS_DOMAIN_ID=0

class="${PRACTICE_CLASS:-s2r}"
handicap="${PRACTICE_HANDICAP:-on}"
npcs="${PRACTICE_NPCS:-0}"
vehicles="${PRACTICE_VEHICLES:-4}"

fail() {
    echo "[ERROR] practice-final.sh: $*" >&2
    exit 1
}

case "${class}" in
s2r) class_args=(--overtaking-lane on --camera off --lidar off) ;;
e2e) class_args=(--overtaking-lane off --camera cpu --lidar cpu --imu off --gnss off --v2x off) ;;
*) fail "PRACTICE_CLASS must be s2r or e2e (got '${class}')" ;;
esac
[[ ${handicap} =~ ^(on|off)$ ]] || fail "PRACTICE_HANDICAP must be on or off (got '${handicap}')"
[[ ${npcs} =~ ^[0-3]$ ]] || fail "PRACTICE_NPCS must be 0..3 (got '${npcs}')"
[[ ${vehicles} =~ ^[1-4]$ ]] || fail "PRACTICE_VEHICLES must be 1..4 (got '${vehicles}')"
extra_args=()
if [ "${PRACTICE_HEADLESS:-0}" = 1 ]; then
    [ "${class}" = s2r ] || fail "PRACTICE_HEADLESS=1 disables the camera and LiDAR that PRACTICE_CLASS=${class} needs (S2R only)"
    extra_args+=(-headless)
fi

# AWSIM は result-summary.json / dN-result-details.json を自身の CWD に書き出すので、run ディレクトリへ移動する。
cd "${LOG_DIR:-/output}" || fail "cannot cd to LOG_DIR '${LOG_DIR:-/output}'"

exec "${AWSIM_DIRECTORY}/AWSIM.x86_64" \
    --venue citycircuit \
    --start-mode sync \
    --start-count-seconds 10 \
    --vehicles "${vehicles}" \
    --npcs "${npcs}" \
    --boosts 2 \
    --laps 6 \
    --timeout 420.0 \
    --steer-source ackermann \
    --sound off \
    --collisions on \
    --handicap "${handicap}" \
    --wall-recovery off \
    --start-random off \
    --ranking on \
    "${class_args[@]}" \
    "${extra_args[@]}"
