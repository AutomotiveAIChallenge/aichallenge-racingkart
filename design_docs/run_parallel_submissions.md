# run_parallel_submissions.bash 設計メモ

> Note:
> 本ドキュメントは **ホスト側** の複数台起動オーケストレーション（`run_parallel_submissions.bash`）の説明です。
> 車両状態（`/dN/awsim/state`）に基づく initial pose / control mode / finish 後処理の詳細は、
> `aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md` を参照してください。
> recordとcaptureは処理負荷がそこそこかかるので、現在は未実装です。実際に複数台走行に実装する場合は「あり方」から考えます。
> (Rvizによるcaptureは現在音が取れないなどの課題があります)

## 概要

`run_parallel_submissions.bash` は、複数の提出物（`aichallenge_submit.tar.gz`）をそれぞれ eval イメージとしてビルドし、
`docker-compose.yml` に固定定義された `autoware-d1..autoware-d4` を使って並列起動します。

- `--submit` 件数に応じて 1〜4 台（`autoware-d1..autoware-dN`）を起動
- simulator は `simulator.launch.xml` を 1 つ起動（AWSIM + awsim_state_manager を含む）
- autoware は `autoware-d1..autoware-dN` を同時に起動
- `output/<run_id>/dN/autoware.log` に各ドメインのログを出力
  - `run_id` は timestamp（`YYYYMMDD-HHMMSS`）を自動採番して使用する

## 現行の固定仕様

- `run_id` はスクリプト内部で timestamp から自動生成する
- ログ出力先は `LOG_DIR` で指定（`/output/<run_id>/dN`）
- `/output/latest` は固定参照ディレクトリとして使う（参照先の更新は Autoware/評価側処理に依存）
- compose 呼び出しは `.env` の `COMPOSE_FILE` に従う
- `wait-admin-ready` / `wait-admin-finish` は `run_parallel_submissions.bash` では行わない
  - `down` 実行まで、起動中状態を手動で管理する運用。

## 前提

- `docker compose` が使えること（Compose v2）
- 提出物 tar.gz はリポジトリ配下にあること（Docker build context 制約）
- `docker-compose.yml` に `autoware-d1..4` が定義済みであること

## 実行フロー（高レベル）

1. `--submit` をパースし、提出物 1〜4 件を受け取る
2. `output/<run_id>/d1..dN` を作成
3. submit ごとに eval イメージをビルド
   - `docker build --target eval --build-arg SUBMIT_TAR=<repo相対path> -t autoware-dN`
4. `autoware-command` サービスで `simulator.launch.xml` を起動
   - Domain 0 で AWSIM を起動し、同時に `awsim_state_manager` を起動
5. `autoware-d1..autoware-dN` を起動
   - `LOG_DIR=/output/<run_id>/d1|d2` を渡してログ出力先を分離
   - 起動後は即時復帰（`run_parallel_submissions.bash` は admin 状態待機や自動停止を行わない）

## サービス対応

- `autoware-dN` -> Domain ID N（`docker-compose.yml` 側で `ROS_DOMAIN_ID=N` を付与）
- `autoware-command` -> `simulator.launch.xml` 起動

## コマンド

### 1) 実行（複数提出物）

```bash
./run_parallel_submissions.bash \
  --submit \
    submit/aichallenge_submit_A.tar.gz \
    submit/aichallenge_submit_B.tar.gz
```

制約:

- `--submit` は 1〜4 件
- Domain ID は `dN = N` の固定対応

### 2) 停止

```bash
./run_parallel_submissions.bash down
```

- 現行実装は `docker compose down` を実行

## 出力構成

```text
output/<run_id>/
  run_parallel_submissions.log
  awsim.log
  d1/
    autoware.log
    d1-result*.json
  d2/
    autoware.log
    d2-result*.json
  ...
```

`run_id` は timestamp（`YYYYMMDD-HHMMSS`）で自動生成する。

## まず見るログ（最短導線）

1. `output/<run_id>/run_parallel_submissions.log`（ホスト側制御フロー）
2. `output/<run_id>/awsim.log`（simulator 側）
3. `output/<run_id>/dN/autoware.log`（各 Domain）

## 既知の注意点

- submit tar.gz は Docker build context 制約によりリポジトリ配下必須
- build された image tag は `autoware-dN`（再実行で同名tagを上書き）
- `run_parallel_submissions.bash` 自体は admin ready/finish 待機を行わない
