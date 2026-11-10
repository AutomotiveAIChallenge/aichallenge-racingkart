#!/usr/bin/env bash
# bench.sh v2 — 1走行を「結果ディレクトリ」として残す計測器（方針 §5 の (1)〜(8)）
#
# Usage: aichallenge/research_tools/bench.sh <label> [duration_sec]
#   環境変数: BENCH_ROSBAG=0 で rosbag を録らない。BENCH_MAKE=<cmd> は make の差し替え（起動失敗の模擬用。meta に残る）。
#
# 出力: aichallenge/research_tools/results/<YYYYMMDD-HHMMSS>_<label>_<commit>[+dirty]/
#   meta.json            開始時に書き、終了時に status を追記（completed | aborted | void）
#   bench.txt            旧 bench.sh と同じ本文（label 行、LAP 行、[mpc_stats] 末尾 40 行）
#   config.yaml          走行時の config.yaml のコピー
#   params_runtime.yaml  走行開始直後の `ros2 param dump /mpc_controller`
#   params_runtime_end.yaml 同、走行終了直前
#   param_diff.txt       config.yaml と params_runtime.yaml の差（compare_params.py）
#   awsim_result.json    make down 後に AWSIM が書く aichallenge/d1-result-details.json のコピー
#   awsim_summary.json   同 aichallenge/result-summary.json
#   autoware.log, awsim.log  make down 後の output/<run>/ からのコピー（完全なログ）
#   invariant.txt        invariant_check.py の出力
#   git_status.txt       `git status --porcelain --untracked-files=all`（dirty の根拠）
#   git_diff.patch       作業ツリーが dirty のときだけ
# rosbag は output/<run>/d1/rosbag2_all/ に残す（git には入れない。meta.json にパスと metadata.yaml の sha256）。
#
# 終了コード: 0 completed / 1 aborted / 4 void / 2 引数エラー / 3 ロックあり
# aborted: make dev 失敗、output/<run>/ ができない、AWSIM/Autoware が起動しない・走行中に落ちた、
#   lap_monitor が END まで行かない、結果 JSON が新しく書かれない、など。
#   起動前の abort では meta.json/config.yaml/git_* しか無い。走行後の abort（JSON が書かれない等）では
#   bench.txt・params_*・param_diff.txt・ログは残るが awsim_result.json は無い。summarize.py は aborted 行の指標を出さない。
# void: 走行中に MPC.py / mpc_controller.py / config.yaml が変わった（結果ファイルは揃うが採用不可）。
set -u

label="${1:?usage: bench.sh <label> [duration_sec]}"
dur="${2:-290}"
case "$label" in ''|*[!A-Za-z0-9_.-]*) echo "bench: label は英数字・_・-・. のみ: '$label'" >&2; exit 2;; esac
case "$dur" in ''|*[!0-9]*) echo "bench: duration_sec は整数: '$dur'" >&2; exit 2;; esac

MAKE="${BENCH_MAKE:-make}"
ROSBAG="${BENCH_ROSBAG:-1}"

cd "$(dirname "$0")/../.." || exit 1 # repo root
root="$(pwd)"
tools="$root/aichallenge/research_tools"
submit="$root/aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros"
watch_files=("$submit/multi_purpose_mpc_ros/core/MPC.py" "$submit/multi_purpose_mpc_ros/mpc_controller.py" "$submit/config/config.yaml")
result_json="$root/aichallenge/d1-result-details.json"
summary_json="$root/aichallenge/result-summary.json"
lock="$tools/.bench.lock"

commit="$(git -C "$root" rev-parse --short HEAD)"
git_status="$(git -C "$root" status --porcelain --untracked-files=all)"
dirty=""
# dirty = 追跡ファイルの変更（リポジトリ全体）か、提出コード配下の未追跡ファイル
if ! git -C "$root" diff --quiet HEAD -- . || grep -q '^?? aichallenge/workspace/src/' <<<"$git_status"; then dirty="+dirty"; fi
stamp="$(date +%Y%m%d-%H%M%S)"
run="$stamp"                       # output/<run>（make に LOG_DIR として渡す）
res="$tools/results/${stamp}_${label}_${commit}${dirty}"
logdir="$root/output/$run"

sha_of() { sha256sum "$1" | cut -d' ' -f1; }
now() { date --iso-8601=seconds; }
loadavg() { cut -d' ' -f1-3 /proc/loadavg; }
jstr() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"; }
# meta.json のキーを1つ書き換える（JSON として扱い、途中で壊さない）。失敗したら abort
meta_set() { # meta_set key json_value
    python3 - "$res/meta.json" "$1" "$2" <<'PY' || abort "meta_set $1 failed"
import json, os, sys
p, k, v = sys.argv[1:]
d = json.load(open(p)); d[k] = json.loads(v)
tmp = p + ".tmp"
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False); f.write("\n")
os.replace(tmp, p)
PY
}
sha_json() { # 監視3ファイルの sha256 を JSON オブジェクトで
    local out="{" sep="" f
    for f in "${watch_files[@]}"; do out+="$sep$(jstr "$(basename "$f")"): $(jstr "$(sha_of "$f")")"; sep=", "; done
    echo "$out}"
}
compose_running() { docker compose ps --status running --services 2>/dev/null | grep -qx "$1"; }
rc_cmd() { # コンテナ内で ROS コマンドを1回実行（stdout を返す）
    CMD="$1" timeout "${2:-60}" docker compose run --rm --no-deps autoware-command 2>/dev/null
}
param_dump() { # param_dump <出力ファイル> → 0 なら ros__parameters を含む YAML が書けた
    rc_cmd "ros2 param dump /mpc_controller" 60 > "$1.tmp" && grep -q "ros__parameters" "$1.tmp" && mv "$1.tmp" "$1"
    local rc=$?; rm -f "$1.tmp"; return $rc
}

status="aborted"; reason=""; in_finish=0
finish() {
    rc=$?
    trap - EXIT
    in_finish=1
    $MAKE down >/dev/null 2>&1
    if [ -f "$res/meta.json" ]; then
        # (5) 終了時のハッシュ。走行中に変わっていれば void
        end_sha="$(sha_json)"
        meta_set sha256_end "$end_sha"
        if [ "$status" = "completed" ] && [ "$end_sha" != "$start_sha" ]; then
            status="void"; reason="watched file changed during run"
        fi
        meta_set status "$(jstr "$status")"
        meta_set reason "$(jstr "$reason")"
        meta_set end_time "$(jstr "$(now)")"
        meta_set load_avg_end "$(jstr "$(loadavg)")"
    fi
    rm -f "$lock"
    echo "RESULT_DIR=$res status=$status${reason:+ reason=$reason}"
    case "$status" in
        completed) exit 0 ;;
        void) exit 4 ;;
        *) if [ "${rc:-0}" -ne 0 ]; then exit "$rc"; else exit 1; fi ;;
    esac
}
abort() {
    reason="$1"; echo "bench: ABORT: $reason" >&2
    if [ "$in_finish" = 1 ]; then echo "bench: (abort during finish)" >&2; rm -f "$lock"; exit 1; fi
    exit 1
}

if [ -e "$lock" ]; then
    lock_pid="$(cut -d' ' -f1 "$lock" 2>/dev/null)"
    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
        echo "bench: $lock があります（bench が走行中。中身: $(cat "$lock")）" >&2; exit 3
    fi
    echo "bench: 古いロック（pid $lock_pid は居ない）を消します: $(cat "$lock")" >&2
    rm -f "$lock"
fi

mkdir -p "$res" || exit 1
echo "$$ $res $(now)" > "$lock"
trap finish EXIT
trap 'reason="interrupted (SIGINT/SIGTERM)"; exit 130' INT TERM

# (5) 開始時のハッシュ
start_sha="$(sha_json)"
image_id="$(docker image inspect -f '{{.Id}}' aichallenge-2025-dev 2>/dev/null || echo unknown)"
compose_file="$(grep -E '^COMPOSE_FILE=' "$root/.env" 2>/dev/null | cut -d= -f2-)"

# (1) 開始時に meta.json
cat > "$res/meta.json" <<EOF2
{
  "bench_version": 2,
  "label": $(jstr "$label"),
  "commit": $(jstr "$commit"),
  "dirty": $([ -n "$dirty" ] && echo true || echo false),
  "branch": $(jstr "$(git -C "$root" branch --show-current)"),
  "duration_sec": $dur,
  "sim_mode": "dev",
  "make_cmd": $(jstr "$MAKE"),
  "rosbag": $([ "$ROSBAG" = "1" ] && echo true || echo false),
  "compose_file": $(jstr "$compose_file"),
  "image_id": $(jstr "$image_id"),
  "sha256_dev_sh": $(jstr "$(sha_of "$root/aichallenge/simulator_scripts/dev.sh")"),
  "host": $(jstr "$(hostname)"),
  "start_time": $(jstr "$(now)"),
  "load_avg_start": $(jstr "$(loadavg)"),
  "output_dir": $(jstr "output/$run"),
  "sha256_start": $start_sha,
  "status": "running"
}
EOF2
python3 -m json.tool "$res/meta.json" >/dev/null || abort "meta.json is not valid JSON"
printf '%s\n' "$git_status" > "$res/git_status.txt"
[ -n "$dirty" ] && git -C "$root" diff HEAD > "$res/git_diff.patch"
cp "$submit/config/config.yaml" "$res/config.yaml"

# 前回の残骸を落としてから、結果 JSON の「前回の」状態を取る（この後に書かれたものだけを今回の結果と認める）
$MAKE down >/dev/null 2>&1
json_mtime_before=$(stat -c %Y "$result_json" 2>/dev/null || echo 0)
json_sha_before=$([ -f "$result_json" ] && sha_of "$result_json" || echo none)
meta_set result_json_before "{\"mtime\": $json_mtime_before, \"sha256\": $(jstr "$json_sha_before")}"

if ! $MAKE dev LOG_DIR="/output/$run" >"$res/make_dev.log" 2>&1; then
    abort "make dev failed (see make_dev.log)"
fi
meta_set sim_start_time "$(jstr "$(now)")"

# (2) 新しい output/<run>/ と両ログができるのを待つ（前回のログを拾わない）
for i in $(seq 1 60); do
    [ -f "$logdir/awsim.log" ] && [ -f "$logdir/d1/autoware.log" ] && break
    sleep 1
done
[ -d "$logdir" ] || abort "output/$run was not created"
[ -f "$logdir/awsim.log" ] || abort "awsim.log did not appear in output/$run"
[ -f "$logdir/d1/autoware.log" ] || abort "d1/autoware.log did not appear in output/$run"
sleep 5
if grep -q "\[ERROR\]" "$logdir/awsim.log"; then
    abort "awsim.log has [ERROR]: $(grep -m1 "\[ERROR\]" "$logdir/awsim.log")"
fi
compose_running simulator || abort "simulator container is not running"
compose_running autoware || abort "autoware container is not running"

# (3) rosbag（dev と同じ LOG_DIR → output/<run>/d1/rosbag2_all）
if [ "$ROSBAG" = "1" ]; then
    $MAKE rosbag LOG_DIR="/output/$run" >"$res/make_rosbag.log" 2>&1 || abort "make rosbag failed"
fi

# (4) /mpc_controller が上がるのを待って ros2 param dump
params_ok=0
for i in $(seq 1 12); do
    if param_dump "$res/params_runtime.yaml"; then params_ok=1; break; fi
    sleep 5
done
[ "$params_ok" = 1 ] || abort "ros2 param dump /mpc_controller did not succeed"
python3 "$tools/compare_params.py" "$res/config.yaml" "$res/params_runtime.yaml" > "$res/param_diff.txt"; cmp_rc=$?
grep -q '^node=' "$res/param_diff.txt" || cmp_rc=2
case $cmp_rc in
    0) meta_set param_override false ;;
    1) meta_set param_override true ;;
    *) abort "compare_params.py failed (rc=$cmp_rc)" ;;
esac

# 走行（旧 bench.sh と同じ本文を bench.txt に残す）
meta_set monitor_start_time "$(jstr "$(now)")"
{
    echo "label=$label logdir=output/$run commit=${commit}${dirty}"
    CMD="python3 /aichallenge/research_tools/lap_monitor.py $dur" timeout $((dur + 60)) docker compose run --rm --no-deps autoware-command 2>/dev/null | grep -E "LAP|END"
    echo "--- mpc_stats ---"
    grep -a "mpc_stats" "$logdir/d1/autoware.log" | sed -E 's/.*\[mpc_stats\]/[mpc_stats]/' | tail -n 40
} | tee "$res/bench.txt"
meta_set monitor_end_time "$(jstr "$(now)")"
grep -q '^END:' "$res/bench.txt" || abort "lap_monitor did not reach END (see bench.txt)"
# 走行後にも生存確認（走行中に落ちた走行を completed にしない）
compose_running simulator || abort "simulator container died during run"
compose_running autoware || abort "autoware container died during run"

# (4) 終了直前のパラメータ（走行中に変えられていないか）
if param_dump "$res/params_runtime_end.yaml"; then
    if cmp -s "$res/params_runtime.yaml" "$res/params_runtime_end.yaml"; then
        meta_set params_changed_during_run false
    else
        meta_set params_changed_during_run true
    fi
else
    meta_set params_changed_during_run null
    meta_set params_end_dump_failed true
fi

# rosbag を先に止めて録り終える（stop_grace_period 60s）
if [ "$ROSBAG" = "1" ]; then
    docker compose stop rosbag >/dev/null 2>&1
    bag="$logdir/d1/rosbag2_all"
    if ls "$bag"/*.mcap >/dev/null 2>&1 && [ -f "$bag/metadata.yaml" ]; then
        meta_set rosbag_dir "$(jstr "output/$run/d1/rosbag2_all")"
        meta_set rosbag_bytes "$(du -sb "$bag" | cut -f1)"
        meta_set rosbag_files "$(ls "$bag"/*.mcap | wc -l)"
        meta_set sha256_rosbag_metadata "$(jstr "$(sha_of "$bag/metadata.yaml")")"
    else
        meta_set rosbag_missing true
    fi
fi

# (6) make down で AWSIM が結果 JSON を書く → 今回の走行で新しく書かれたことを確かめてコピー
$MAKE down >/dev/null 2>&1
meta_set sim_end_time "$(jstr "$(now)")"
json_mtime_after=$(stat -c %Y "$result_json" 2>/dev/null || echo 0)
json_sha_after=$([ -f "$result_json" ] && sha_of "$result_json" || echo none)
if [ "$json_mtime_after" -le "$json_mtime_before" ] || [ "$json_sha_after" = "$json_sha_before" ]; then
    abort "result JSON was not rewritten by make down (mtime $json_mtime_before -> $json_mtime_after, sha same=$([ "$json_sha_after" = "$json_sha_before" ] && echo yes || echo no))"
fi
[ -f "$summary_json" ] && [ "$(stat -c %Y "$summary_json")" -ge "$json_mtime_after" ] || abort "result-summary.json missing or older than d1-result-details.json"
python3 -m json.tool "$result_json" >/dev/null || abort "d1-result-details.json is not valid JSON"
python3 -m json.tool "$summary_json" >/dev/null || abort "result-summary.json is not valid JSON"
cp "$result_json" "$res/awsim_result.json" || abort "cp awsim_result.json failed"
cp "$summary_json" "$res/awsim_summary.json" || abort "cp awsim_summary.json failed"
meta_set result_json_after "{\"mtime\": $json_mtime_after, \"sha256\": $(jstr "$json_sha_after")}"
lap_n=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["lap_count"]))' "$res/awsim_result.json") || abort "lap_count missing in awsim_result.json"
meta_set lap_n "$lap_n"

# (7) ログのコピー（make down 後なので完全）
cp "$logdir/d1/autoware.log" "$res/autoware.log" || abort "cp autoware.log failed"
cp "$logdir/awsim.log" "$res/awsim.log" || abort "cp awsim.log failed"
meta_set sha256_autoware_log "$(jstr "$(sha_of "$res/autoware.log")")"
meta_set autoware_log_lines "$(wc -l < "$res/autoware.log")"
meta_set mpc_stats_lines "$(grep -ac "mpc_stats" "$res/autoware.log")"

# (8) 不変条件。失敗項目だけ表示（終了コードは記録のみ。採否は人間）
python3 "$tools/invariant_check.py" "$res" > "$res/invariant.txt"; inv_rc=$?
meta_set invariant_rc "$inv_rc"
cat "$res/invariant.txt"

status="completed"
