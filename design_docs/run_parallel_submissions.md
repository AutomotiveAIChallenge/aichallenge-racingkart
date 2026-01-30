# run_parallel_submissions.bash（複数提出物の同時起動）設計メモ

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

### 3) 結果の回収（collect）

```bash
./run_parallel_submissions.bash collect --vehicles 2
```

AWSIM が生成しがちな `dN-result*.json` を、`output/<run_id>/dN/` へ移動して整理します。
（`--vehicles` は省略時に `output/latest` と既存ディレクトリから推定します）

## 出力ディレクトリ構成（重要）

`run_parallel_submissions.bash` は、ホスト側ログと評価結果を **すべて `output/` 配下に集約**します。

### Run（評価単位）

```
output/<run_id>/
  <script_name>.log
  compose.autoware_multi.yml
  d1/
    autoware.log
    d1-result-details.json ...（collect で移動される想定）
  d2/
    autoware.log
  ...
output/latest -> <run_id>
```

## 実行フロー（高レベル）

1. `--submit` の引数をパース（起動数は `--submit` の数）
2. `output/` 配下のディレクトリを準備
   - `output/<run_id>/d1..dN` を作成
   - `output/latest -> <run_id>` を張る
3. ログを初期化
   - `output/<run_id>/<script_name>.log` に `tee`（stdout/stderr を保存）
4. 提出物ごとに eval イメージをビルド（`Dockerfile` の `eval` target）
   - `SUBMIT_TAR=<repo内相対パス>` を build arg として渡す
   - `aichallenge-2025-eval-<submit>-<run_id>-d<domain>` のようなタグを生成
5. compose override を生成（`output/<run_id>/compose.autoware_multi.yml`）
   - `autoware-d1..autoware-dN` を定義（各 service は対応する eval イメージを使う）
   - `working_dir` を `/output/<run_id>/dN` にして `autoware.log` をその中に出す
6. AWSIM（simulator）を起動（`docker-compose.yml` の `simulator`。GPU 時は `docker-compose.gpu.yml` を併用）
   - `SIM_MODE` は起動数に応じて `eval` / `2p` / `3p` / `4p` を自動指定
7. Autoware を並列起動（`docker compose -f docker-compose.yml [-f docker-compose.gpu.yml] -f compose.autoware_multi.yml up -d ...`）

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
  - `cmd_down()`（override を特定して down、必要なら `collect_results()` を呼ぶ）
  - `cmd_collect()` / `collect_results()`（`dN-result*.json` を `output/<run_id>/dN/` に整理）

## compose override の中身（要点）

- service 名: `autoware-d1..dN`
- `network_mode: host` / `privileged: true`
- X11/DRI/Input のマウント（GUI/アクセラレーション用途）
- `command` は `/aichallenge/run_autoware.bash awsim <domain_id>` を実行し、`autoware.log` にリダイレクト

## 改善の余地（メモ）

「無理に変えない」前提で、必要になったら検討できる改善案です。

- **override の DRY 化**: `docker-compose.yml` の `autoware` を再利用する（image と DOMAIN_ID だけ差し替え）と drift が減る
- **ROS ログの集約**: `ROS_HOME/ROS_LOG_DIR` を `output/<run_id>/dN/ros/log` へ寄せると解析が楽
- **並列起動数の拡張**: 4 を超える場合は Domain/bridge/評価仕様と合わせて設計し直す（単純拡張は危険）
