# 実現したいこと

## 評価のオーケストレーションの簡素化

目指したい姿として下記が挙げられる。

1. make devのような簡素なコマンドのみで評価を完了すること
2. 複数台走行をオンラインで実行するためのオーケストレーションと管理の徹底

> Note:
> 本ドキュメントは **ホスト側** の複数台起動オーケストレーション（`run_parallel_submissions.bash`）の指針です。
> 車両状態（`/dN/awsim/state`）に基づく「initial pose / control mode / capture / rosbag / finish 後処理」などの詳細は、
> `aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md` を参照してください。

## 現在の実装

shell scriptの組み合わせによるオーケストレーションだが、なかなか複雑な実装となっており、扱いやすいとは言い難い。
できるだけ複数台走行の完成系から入りたいが、一旦テストなども考慮し、まずは単独で評価ができるところまでを目指したい。

- run_parallel_submissions.bashが複数台の走行起動コマンド
- run_evaluation.bashが単独走行の評価コマンドとなっている

## 課題

### P0（いま困る・壊れやすい）

- **終了条件が手動**:
  `run_parallel_submissions.bash` は基本的に「起動」までで、終了/結果確定は `./run_parallel_submissions.bash down` に依存します。
  AWSIM の `dN-result*.json` は simulator 終了後に生成されるため、`down` せずに出力確認すると「結果が無い」と誤解されがちです。
- **オーケストレーション依存が暗黙**:
  capture/rosbag は `AIC_CAPTURE/AIC_ROSBAG` を Autoware コンテナに渡すだけなので、
  `awsim.launch` 側で `autostart_orchestrator_py` が起動していないと **何も起きません**（“効いていない”に見える）。
- **admin readiness が domain0 前提**:
  readiness は `env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-state` の成功が前提で、
  ここが失敗/ハングすると全体が止まります（タイムアウト/ログ導線が重要）。
- **CPU/GPU 分岐の破綻リスク**:
  GPU override（例: `docker-compose.gpu.yml` や override 内の NVIDIA device reservation）を CPU-only ホストに混ぜると、
  `docker compose up` 自体が失敗し得ます。**GPU を有効にしない時は GPU compose を混ぜない**ことが必須です。

### P1（すぐではないが積み残すと辛い）

- **compose override の drift**:
  `docker-compose.yml` の `x-autoware-base` と `run_parallel_submissions.bash` の override が二重管理になっており、
  volume/env の差分がズレると不具合が出ます。
- **ビルド時間が支配的**:
  submit ごとに eval image build するため、台数が増えるほど待ち時間が増えます（キャッシュ前提・再利用の余地）。
- **デバッグ動線が弱い**:
  失敗時に「どの domain のどのログを見るか」が迷子になりやすいです。

## まず見るログ（最短導線）

失敗時は次の順で見るのが最短です。

1. `output/<run_id>/run_parallel_submissions.log`（ホスト側の起動ログ）
2. `output/<run_id>/awsim.log`（simulator 側ログ）
3. `output/<run_id>/dN/autoware.log`（domain ごとの Autoware ログ）

capture/rosbag の開始/停止が動いているかは、上記に加えて
`aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md` の成果物/ログ規約（出力先）も確認してください。

## 複数台走行の評価実行フローとして目指したい形（高レベル）

1. `--submit` の引数をパースをして何台動かすか把握
2. `output/` 配下のディレクトリを準備
   1. `output/<run_id>/d1..dN` を作成
   2. `output/latest -> <run_id>` を張る
3. ログを初期化
   1. `output/<run_id>/<script_name>.log` に `tee`（stdout/stderr を保存）
4. 提出物ごとに eval イメージをビルド（`Dockerfile` の `eval` で合わせてautowareがbuildされる）
   1. `SUBMIT_TAR=<repo内相対パス>` を build arg として渡す
   2. `aichallenge-2025-eval-<submit>-<run_id>-d<domain>` のようなタグを生成
5. compose override を生成（`output/<run_id>/compose.autoware_multi.yml`）
   - `autoware-d1..autoware-dN` を定義（各 service は対応する eval イメージを使う）
   - `working_dir` を `/output/<run_id>/dN` にして `autoware.log` をその中に出す
6. AWSIM（simulator）を起動（`docker-compose.yml` の `simulator`。GPU 時は `docker-compose.gpu.yml` を併用）
   - `SIM_MODE` は起動数に応じて `eval` / `2p` / `3p` / `4p` を自動指定
7. Autoware を並列起動
   1. （`docker compose -f docker-compose.yml [-f docker-compose.gpu.yml] -f compose.autoware_multi.yml up -d ...`）
   2. オーケストレーターがAWSIMからstateをもらい、一斉に走行許可
   3. 走行終了後にオーケストレーターが後処理
8. 後処理・起動終了

## run_parallel_submissions.bash（複数提出物の同時起動）以下すべて設計メモ

`run_parallel_submissions.bash` は、**複数の提出物（`aichallenge_submit.tar.gz`）をそれぞれ別の eval イメージとしてビルド**し、**Domain ID を 1..N で割り当てた Autoware コンテナ（`autoware-d1..dN`）を並列起動**するためのホスト側オーケストレータです。

> 前提: `docker compose` が使えること（Docker Desktop / Docker Engine + Compose v2）。

## 目的

- 1台の AWSIM（simulator）を起動したまま、複数提出物の Autoware を並列に立ち上げて確認する
- 生成物（Autoware の stdout/stderr、compose 生成ファイル、結果 json）を `output/` 配下に集約する

## 関連ファイル

- 実行エントリ:
  - `run_parallel_submissions.bash`（ホスト側オーケストレータ）
- 参照する compose:
  - `docker-compose.yml`（simulator 起動に使用）
  - `docker-compose.gpu.yml`（GPU 設定上書き）
  - `run_parallel_submissions.bash` は **実行時に compose override を生成**して `autoware-d*` を定義する
- eval イメージのビルド:
  - `Dockerfile` の `eval` target（`--build-arg SUBMIT_TAR=...`）
- 出力:
  - `output/`（結果・ログの集約先）

## コマンド（CLI）

### 1) 実行（複数提出物の起動）

```bash
./run_parallel_submissions.bash \
  --submit \
    submit/aichallenge_submit_A.tar.gz \
    submit/aichallenge_submit_B.tar.gz
```

- `--submit`（必須）: 提出物 tar.gz（**リポジトリ配下のパスである必要**あり。`docker build` のコンテキスト制約）
  - `--submit A B C` のように **1つの `--submit` に複数ファイルを並べる**（最大4つ）

制約:
- 起動数は `--submit` の数で決定（`1..4`）
- Domain ID は `--submit` の順に `1..N` を割り当て
- AWSIM のモードは起動数で自動選択（`eval` / `2p` / `3p` / `4p`）
- GPU/CPU は環境変数 `DEVICE=auto|gpu|cpu` で選択
  - `auto` は **`/dev/nvidia0` の有無のみ**で判定（`nvidia-smi` 非依存）
- `run_id` は自動生成（`<timestamp>-<script_name>-<pid>`）

### 2) 停止（down）

```bash
./run_parallel_submissions.bash down
```

- `output/latest -> <run_id>` を参照し、`output/<run_id>/compose.autoware_multi.yml` を使って `docker compose down --remove-orphans` します
- AWSIM の結果（`dN-result*.json`）は **AWSIM 終了後に生成される**ため、`down` の後に `output/<run_id>/dN/` へ移動して整理します

### 3) 結果の回収（collect）

```bash
./run_parallel_submissions.bash collect --vehicles 2
```

AWSIM が生成しがちな `dN-result*.json` を、`output/<run_id>/dN/` へ移動して整理します。
（`--vehicles` は省略時に `output/latest` と既存ディレクトリから推定します）
（通常は `down` 実行時に整理される想定ですが、手動で整理したい場合の保険として `collect` を残しています）

## 出力ディレクトリ構成（重要）

`run_parallel_submissions.bash` は、ホスト側ログと評価結果を **すべて `output/` 配下に集約**します。

### Run（評価単位）

```
output/<run_id>/
  <script_name>.log
  compose.autoware_multi.yml
  d1/
    autoware.log
    d1-result-details.json ...（down/collect で移動される想定）
  d2/
    autoware.log
  ...
output/latest -> <run_id>
```



## スクリプト内部の構成（主な関数と責務）

読み解く時に「何がどこで決まるか」が追えるよう、主要な関数の責務をまとめます。

- `REPO_ROOT`
  - スクリプト自身の位置（`BASH_SOURCE[0]`）から repo ルートを解決
- 引数/入力検証
  - `is_number()` / `gpu_enabled_from_device()` / `require_submit_in_build_context()`
- 出力ディレクトリ
  - `ensure_output_dirs(run_id, vehicles)`（`output/<run_id>/dN`、`output/latest`）
- `init_run_log(run_id)`（`output/<run_id>/<script_name>.log` への `tee` の設定）
- ビルド
  - `build_eval_image(submit_rel, run_id, domain_id)`（eval target をビルドしてイメージ tag を返す）
- compose 生成/起動
  - `write_compose_override(out_file, run_id, vehicles, gpu_enabled, images...)`（`autoware-d*` を定義する override を出力）
  - `compose_up(gpu_enabled, ...)`（GPU 時のみ NVIDIA env を付与して `docker compose ...` を呼ぶ薄いラッパ）
- 終了/回収
  - `cmd_down()`（override を特定して down の後に `collect_results()` を呼ぶ）
  - `cmd_collect()` / `collect_results()`（`dN-result*.json` を `output/<run_id>/dN/` に整理）

## compose override の中身（要点）

- service 名: `autoware-d1..dN`
- `network_mode: host` / `privileged: true`
- X11/DRI/Input のマウント（GUI/アクセラレーション用途）
- `command` は `/aichallenge/run_autoware.bash awsim <domain_id>` を実行し、`autoware.log` にリダイレクト

## 改善の余地（メモ）

「無理に変えない」前提で、必要になったら検討できる改善案です。

- **P0: 運用の明文化**:
  - `down` が「停止 + 結果整理（dN-result*.json 回収）」も含むことを明記し、推奨手順（起動→観察→down→確認）を固定する
  - capture/rosbag は `autostart_orchestrator_py` 起動が前提であることを明記し、参照先を統一する
- **P1: ログ導線**:
  - “最初に見るログ3点セット” を README/スクリプト出力と一致させる
  - `ROS_LOG_DIR` などで ROS2 のログを `output/<run_id>/dN/ros/log` に寄せる（必要になってから）
- **P1: override の DRY 化**:
  - `docker-compose.yml` の `x-autoware-base` をテンプレにして差分だけ生成すると drift が減る（急がない）
- **P2: 並列起動数の拡張**:
  - 4 を超える場合は Domain/bridge/評価仕様/負荷を含めて再設計が必要（単純拡張は禁止）
