# Log設計計画書（/output 配下に集約）

作成日: 2026-01-27  
対象: `docker_run.sh`（従来フロー/rocker）・`docker-compose.yml`（make 経由）・`aichallenge/run_evaluation.bash`（評価オーケストレータ）

## 0. 目的（ゴール）

- **評価1回の結果（kekka）と実行時ログを、1つの「実行単位ディレクトリ」に揃えて追跡可能にする**
- **/output（ホストの `./output`）配下だけ見れば、何が起きたか・なぜ失敗したかが再現できる状態にする**
- **Docker 実行（rocker / docker compose）でパス構成が異なる点を吸収し、運用がブレないようにする**

## 1. 現状整理（調査結果）

### 1.1 実行経路

- **従来（rocker）**
  - `./docker_build.sh eval` → `./docker_run.sh eval`
  - `docker_run.sh` は `--volume output:/output` のみ（`eval` の場合）
  - `eval` イメージは `CMD ["bash", "/aichallenge/run_evaluation.bash"]` で評価が走る
  - **注意:** `eval` ではホストの `./aichallenge` がコンテナにマウントされないため、スクリプト変更を反映するには **eval イメージの再ビルドが必要**
- **推奨（docker compose）**
  - `./run_evaluation.bash`（必要なら `ROSBAG=true CAPTURE=true DOMAIN_IDS=...` などを付与）
  - `docker-compose.yml` で `./output:/output` と `./aichallenge:/aichallenge` をマウント
  - **注意:** compose ではホスト側の `aichallenge/` が見えるため、スクリプト変更は再ビルド無しで反映される（イメージ依存の部分を除く）

### 1.2 既存の出力仕様（現状）

- `aichallenge/run_evaluation.bash`
  - `/output/<timestamp>/d<domain_id>/` を作成し、そこで実行を継続（`autoware.log`、`capture/`、`result-details.json` 等が同階層に出る）
  - `/output/latest/d<id>/` に固定名リンク（`autoware.log` など）を集約
- `docker_build.sh` / `docker_run.sh`
  - ホスト側ログを `output/docker/<event_id>/` に出力し、`output/latest/docker_build.log` / `output/latest/docker_run.log` で最新を参照する
- `aichallenge/utils/topic_check.sh`
  - デフォルトでは `output/latest/`（`output/latest/topic_check.txt`）にログを出す

### 1.3 問題点（見直し対象）

- `docker_build.sh` / `docker_run.sh` のログは `output/docker` にイベントIDベース（タイムスタンプ+PID）で保存され、`output/latest/docker_*.log` へ固定参照を貼るため履歴追跡が容易になった

## 2. 要件（満たしたいこと）

### 2.1 機能要件

- 評価1回ごとに「実行単位ディレクトリ（Run）」を作り、**Run の中に成果物とログを集約**する
- Run を一意に識別できる（最低限: timestamp、できれば + 追加情報）
- いつでも **「最新 Run」へ安定パスでアクセス**できる（`/output/latest/d<id>/`）
- 失敗時も解析に必要なログが残る（標準出力だけに依存しない）
- host 側（build/run）ログも /output 配下に残す（Run と紐づけ可能にする）

### 2.2 非機能要件

- Docker 実行形態（rocker / compose）でブレない
- root/非root 実行でも破綻しない（ファイル権限の扱いを明確に）
- 既存の成果物ファイル名（`autoware.log`、`result-details.json` 等）を極力壊さない（互換性）

## 3. ディレクトリ設計（提案 v1）

### 3.1 /output トップの役割分離

`/output`（ホストの `./output`）直下を以下に整理する。

```
/output/
  <run_id>/                 # 評価1回の成果物（現行の timestamp ディレクトリを踏襲）
    d<domain_id>/           # ★追加: domain id ごとに成果物を分離
      autoware.log
      awsim.log
      rosbag.log
      autoware.log          # run_evaluation.bash の stdout/stderr もこのファイルへ集約
      result-details.json
      capture/...
      rosbag2_autoware/...
      ros/                  # ★追加: ROS の ~/.ros 相当（log を含む）
      meta.json             # ★追加: 実行条件/環境/終了コード
  latest/                   # ★固定: 最新結果参照用ディレクトリ
    d<domain_id>/           # run 内の固定名リンク（result-details.json / capture.mp4 / rosbag2_autoware.mcap / motion_analytics.html / autoware.log）
  docker/                   # host 側ログ（build/run）
    <host_event_id>/
      docker_build.log
      docker_run.log
      meta.json            # build/run 時の情報（イメージタグ、git hash 等）
    latest/docker_build.log -> <host_event_id>/docker_build-*.log
    latest/docker_run.log -> <host_event_id>/docker_run-*.log
```

- **Run ディレクトリ**は現状の `YYYYMMDD-HHMMSS` を `run_id` として踏襲（移行コスト最小）
  - 将来的に衝突回避が必要なら `YYYYMMDD-HHMMSS-<pid>` 等に拡張
- host 側ログは `output/docker` に集約し、`output/latest/docker_*.log` で共通の最新ログに接続する

### 3.2 Run 内のログ/成果物の「最低限の規約」

- `autoware.log`: `run_evaluation.bash` 自身の標準出力/標準エラーを含めて保存（将来的には `logs/` 配下に整理し、互換 symlink を残す）
- `ros/`: ROS2 の log 出力先（`ROS_HOME` 等で誘導）
- `meta.json`: 実行条件を機械可読で保存（後述）

## 4. ログ設計（中身の規約）

### 4.1 `meta.json`（Run）

Run ディレクトリに以下の情報を保存する（例）。

- `run_id`, `started_at`, `finished_at`, `exit_code`
- `mode`: `awsim` / `vehicle` / `rosbag` 等
- `domain_id`, `capture_enabled`, `rosbag_enabled`
- `image`: 可能なら `aichallenge-2025-eval` 等のタグ/ID
- `host`: hostname, uid/gid
- `container`: container id/name（取得できる経路のみ）

### 4.2 ログファイルの基本方針

- 重要ログは「標準出力に出す」だけでなく、**必ずファイルに tee** する
- 既存の prefix（`[run_evaluation]`）は維持しつつ、
  - 必要なら `INFO/WARN/ERROR` の粒度を追加
  - 時刻は `date -Iseconds` で揃える（必要な箇所のみ）

## 5. Docker 実行時のパス差異の吸収方針

### 5.1 rocker（`docker_run.sh eval`）の注意

- `eval` は `./aichallenge:/aichallenge` をマウントしないため、**スクリプト変更は `./docker_build.sh eval` が必要**
- ただし `/output` は常にマウントされるため、**ログ/成果物の集約先は一貫して `/output` にできる**

### 5.2 compose（`./run_evaluation.bash`）の注意

- `/aichallenge` はホストマウントされるため、スクリプト改修は反映されやすい
- 一方で複数サービスを跨ぐため、`make` / compose 実行結果の host 側ログは `output/latest/docker_build.log` や `output/latest/docker_run.log` へ集約する運用を想定する

## 6. 互換性・移行計画（安全第一）

### 6.1 既存の `output/latest/` ディレクトリの扱い

- 現状の `output/latest/` は
  - 主に `topic_check.txt` の保存先として使われる
- `run_evaluation` は `/output/latest/...` を使うため、直近の衝突は発生しない
- **移行方針**
  1. `topic_check.sh` のデフォルトは `output/latest/topic_check.txt` のまま維持
  2. host 側ログは `output/docker/<event_id>/` へ集約し、固定シンボリックリンクは `output/latest/docker_*.log` を更新する

### 6.2 既存 Run との整合

- 既存の `output/<timestamp>/` はそのまま残す（破壊的移動はしない）
- 新しい Run から `autoware.log` / `meta.json` / `ros/` を追加していく

## 7. 実装計画（段階的に）

### Phase 1（衝突解消 + Run の安定入口を復活）

- `docker_build.sh` / `docker_run.sh` のログ出力先を `output/docker/...` に変更（上書き回避で event_id 付与）
- `run_evaluation.bash` は `/output/latest` を更新する前提で整理

### Phase 2（Run 内に必須ログを確実に残す）

- `run_evaluation.bash` の stdout/stderr を `autoware.log` に tee（Run ディレクトリ確定後に `exec > >(tee ...) 2>&1`）
- `run_simulator.bash` の出力も Run 内に保存（現状 `/dev/null` のため）
- ROS ログを `ROS_HOME=$OUTPUT_DIRECTORY/ros` 等で Run 配下に固定

### Phase 3（見通し改善）

- Run 配下の `logs/`, `results/`, `artifacts/` への整理（互換 symlink を残しつつ移行）
- `meta.json` の充実（image id、git hash、CPU/GPU、引数、終了コード）
- 保守: 古い Run の整理（ポリシーが固まってから）

## 8. 次に確認したい事項（質問/合意ポイント）

- Run ディレクトリ名は現状の `YYYYMMDD-HHMMSS` のままで良いか（衝突対策が必要か）
- host 側ログは Run と 1:1 紐付けが必要か（必要なら `run_id` を host → container に渡す仕組みを追加）
- `docker_run.sh eval` を「起動してから手動で `run_evaluation`」運用にするか、現状通り「CMD で自動実行」を前提にするか
