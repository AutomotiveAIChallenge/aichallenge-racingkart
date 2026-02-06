# run_parallel_submissions.bash 設計メモ

> Note:
> 本ドキュメントは **ホスト側** の複数台起動オーケストレーション（`run_parallel_submissions.bash`）の説明です。
> 車両状態（`/dN/awsim/state`）に基づく initial pose / control mode / capture / rosbag / finish 後処理の詳細は、
> `aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md` を参照してください。

## 概要

`run_parallel_submissions.bash` は、複数の提出物（`aichallenge_submit.tar.gz`）をそれぞれ eval イメージとしてビルドし、
`docker-compose.yml` に固定定義された `autoware-domain1..autoware-domain4` を使って並列起動します。

- submit の並び順に Domain ID を `1..N` で割り当て（最大 4）
- simulator は 1 台だけ起動
- `output/<run_id>/dN/autoware.log` に各ドメインのログを集約
- `output/latest -> <run_id>` を更新

旧実装で使っていた compose override 生成（`compose.autoware_multi.yml`）は廃止済みです。

## 前提

- `docker compose` が使えること（Compose v2）
- 提出物 tar.gz はリポジトリ配下にあること（Docker build context 制約）
- `docker-compose.yml` に `autoware-domain1..4` が定義済みであること

## 実行フロー（高レベル）

1. `--submit` をパースし、台数 `N`（`1..4`）を決定
2. `output/<run_id>/d1..dN` を作成し、`output/latest` を更新
3. submit ごとに eval イメージをビルド
4. compose project 名を決定し、`output/<run_id>/compose.project` に保存
5. simulator を 1 台起動
   - `SIM_MODE` は台数に応じて `eval` / `2p` / `3p` / `4p`
6. `autoware-domain1..autoware-domainN` を順に起動
   - `AUTOWARE_DOMAIN{N}_IMAGE` でサービスごとの image を差し替え
   - `OUTPUT_RUN_DIR=/output/<run_id>/dN` を渡してログ出力先を分離
7. `autoware-command` で `wait-admin-state` を実行し readiness を待機
8. 停止時（`down`）に simulator 終了後の `dN-result*.json` を `output/<run_id>/dN/` へ回収

## サービス対応

- `autoware-domain1` -> Domain ID 1
- `autoware-domain2` -> Domain ID 2
- `autoware-domain3` -> Domain ID 3
- `autoware-domain4` -> Domain ID 4

各サービスの image は次の環境変数で差し替えます。

- `AUTOWARE_DOMAIN1_IMAGE`
- `AUTOWARE_DOMAIN2_IMAGE`
- `AUTOWARE_DOMAIN3_IMAGE`
- `AUTOWARE_DOMAIN4_IMAGE`

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

- `output/latest`（または `--log-dir`）から compose project 名を解決して `docker compose down --remove-orphans` を実行
- 停止後に `dN-result*.json` を各 `dN/` に回収

### 3) 結果回収のみ

```bash
./run_parallel_submissions.bash collect --vehicles 2
```

- `dN-result*.json` を `output/<run_id>/dN/` に整理

## 出力構成

```text
output/<run_id>/
  run_parallel_submissions.log
  compose.project
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

- `run_parallel_submissions.bash` は起動まで担当し、終了確定は `down` に依存
- `AIC_CAPTURE` / `AIC_ROSBAG` は orchestrator 起動が前提
- readiness は `ROS_DOMAIN_ID=0` の admin state 依存
- 最大 4 台（`autoware-domain1..4` 固定）
