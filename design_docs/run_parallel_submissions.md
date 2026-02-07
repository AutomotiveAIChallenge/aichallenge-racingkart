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

- submit の並び順に Domain ID を `1..N` で割り当て（最大4）
- simulator は 1 台だけ起動
- `output/<run_id>/dN/autoware.log` に各ドメインのログを出力
- `output/latest -> <run_id>` を更新

## 前提

- `docker compose` が使えること（Compose v2）
- 提出物 tar.gz はリポジトリ配下にあること（Docker build context 制約）
- `docker-compose.yml` に `autoware-d1..4` が定義済みであること

## 実行フロー（高レベル）

1. `--submit` をパースし、台数 `N`（`1..4`）を決定
2. `output/<run_id>/d1..dN` を作成し、`output/latest` を更新
3. submit ごとに eval イメージをビルド
   - `docker build --target eval --build-arg SUBMIT_TAR=<repo相対path> -t autoware-dN`
4. simulator を 1 台起動
   - `SIM_MODE` は台数に応じて `eval` / `2p` / `3p` / `4p`
5. `autoware-command` を `ROS_DOMAIN_ID=0` で実行し、`wait-admin-ready` を待機
6. `autoware-d1..autoware-dN` を順に起動
   - `OUTPUT_RUN_DIR=/output/<run_id>/dN` を渡してログ出力先を分離
7. `autoware-command` を `ROS_DOMAIN_ID=0` で実行し、`wait-admin-finish` を待機

## サービス対応

- `autoware-d1` -> Domain ID 1
- `autoware-d2` -> Domain ID 2
- `autoware-d3` -> Domain ID 3
- `autoware-d4` -> Domain ID 4

## コマンド

### 1) 実行（複数提出物）

```bash
./run_parallel_submissions.bash \
  --submit \
    submit/aichallenge_submit_A.tar.gz \
    submit/aichallenge_submit_B.tar.gz
```

制約:

- `--submit` は 1 つ以上、最大 4 つ
- Domain ID は submit の順で `1..N`
- `DEVICE=auto|gpu|cpu` で GPU 使用を制御

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
output/latest -> <run_id>
```

## まず見るログ（最短導線）

1. `output/<run_id>/run_parallel_submissions.log`（ホスト側制御フロー）
2. `output/<run_id>/awsim.log`（simulator 側）
3. `output/<run_id>/dN/autoware.log`（各 Domain）

## 既知の注意点

- submit tar.gz は Docker build context 制約によりリポジトリ配下必須
- build された image tag は `autoware-dN`（再実行で同名tagを上書き）
- readiness は `ROS_DOMAIN_ID=0` の admin state 依存
- 最大 4 台（`autoware-d1..4` 固定）
