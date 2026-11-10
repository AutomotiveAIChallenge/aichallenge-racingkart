# research_tools

研究用の計測ツール（方針の「計測器」= LM 層。変更の diff は人間が全文読む）。コンテナからは `/aichallenge/research_tools` として見えます。

## 1走行を記録する: `bench.sh`

```
aichallenge/research_tools/bench.sh <label> [秒]      # 既定 290 秒。約5〜6分かかる
```

`make down → make dev → 走行 → make down` を行い、結果を **`results/<日時>_<label>_<commit>[+dirty]/`** に残す。
標準出力は旧 bench.sh と同じ（LAP 行と `[mpc_stats]` 末尾 40 行）で、`bench.txt` にも入る。最後に `RESULT_DIR=... status=...` を出す。

| ファイル | 中身 |
|---|---|
| `meta.json` | 開始時に書き、終了時に `status` を追記。`completed` / `aborted`（起動失敗など。結果ファイルは書かない）/ `void`（走行中に MPC.py・mpc_controller.py・config.yaml が変わった）。commit、dirty、開始終了時刻、load average、3ファイルの sha256（開始・終了）、`param_override`、`params_changed_during_run`、`lap_n`、`invariant_rc`、rosbag の場所 |
| `bench.txt` | 旧形式の本文 |
| `config.yaml` | 走行時の config.yaml のコピー |
| `params_runtime.yaml` / `params_runtime_end.yaml` | 走行開始直後／終了直前の `ros2 param dump /mpc_controller` |
| `param_diff.txt` | `compare_params.py` の出力（config.yaml と実行時パラメータの差） |
| `awsim_result.json` / `awsim_summary.json` | `make down` 後に AWSIM が書く `aichallenge/d1-result-details.json` / `result-summary.json` のコピー（元は .gitignore に当たる）。前回の値を拾わないよう mtime が進んだときだけコピー |
| `autoware.log` / `awsim.log` | `output/<run>/` からのコピー（autoware.log は sha256 を meta に記録） |
| `invariant.txt` | `invariant_check.py` の出力 |
| `git_diff.patch` | 作業ツリーが dirty のときだけ |
| `make_dev.log` / `make_rosbag.log` | 起動コマンドの出力 |

- rosbag は `output/<run>/d1/rosbag2_all/` に残る（git には入れない。`meta.json` の `rosbag_dir`）。`BENCH_ROSBAG=0` で録らない。
- 走行中は `research_tools/.bench.lock` がある。他の重い作業（ビルド・pytest）はこれが消えるまで待つ。
- `BENCH_MAKE=false` にすると `make` が失敗したときの経路を試せる（AWSIM は起動しない）。
- 結果ディレクトリは削除・上書き・ラベル変更しない（親 CLAUDE.md の規則）。

## 集計する: `summarize.py` / `invariant_check.py`

```
python3 aichallenge/research_tools/summarize.py            # results/ 全体を Markdown の表に（「出典」列 = ディレクトリ名）
python3 aichallenge/research_tools/invariant_check.py aichallenge/research_tools/results/<run>
```

指標の定義は `bench_common.py` の先頭に書いてある（1窓目の `[mpc_stats]` は除外、`lap_med_2p` は2周目以降の中央値、など）。
`invariant_check.py` の閾値は暫定（手順6 のベースライン5走行で確定）。I9 と I1〜I4 は未実装。

## その他

- `lap_monitor.py`: `/awsim/status` を購読して周回タイムを表示する（bench.sh が使う）
- `compare_params.py <config.yaml> <params_runtime.yaml>`: 差があれば終了コード 1

## `results/` の旧結果（2026-11-10 に新形式へ移した）

| ディレクトリ | 元ファイル | 備考 |
|---|---|---|
| `20261109-154314_baseline_a284274/` | `bench_baseline.txt` | 公式サンプルのまま。commit は推定（旧 bench.sh は記録しなかった） |
| `20261109-155004_fixed_all_c388dbf+dirty/` | `bench_fixed_all.txt` | 修正 (a)(b)(c) 全部。コミット前の作業ツリー（推定）。**研究のベースライン** |
| `20261109-155527_fixed_bc_only_nocommit/` | `bench_fixed_bc_only.txt` | 修正 (b)(c) だけ。該当コミット無し（再現不可）。1周目の途中で停止 |
| `20261109-173947_step2_probe_0e57977/` | `bench_step2_probe.*` | 手順2 の確認走行（ベースラインと同条件、JSON 付き） |
| `20261109-174532_step2_dev2_probe_0e57977/` | `bench_step2_dev2_probe.*` | `make dev2` の JSON の出方の確認（bench.sh 不使用） |

いずれも `meta.json` の `status` は `migrated`、`commit_note` に出自を書いてある。
