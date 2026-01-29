# Makefile ターゲット命名ガイドライン（サービス-コマンド）

このリポジトリの `Makefile` ターゲットは **`<service>-<command>`** 形式を基本とし、一覧性・検索性・拡張性を優先します。

## 1. 基本ルール

- **形式**: `<service>-<command>[-<variant>]`
- **文字種**: `a-z` / `0-9` / `-` のみ（小文字）。`_` は使わない
- **語順**: 先頭は必ず *service*（`build-*` / `run-*` のような動詞始まりは避ける）
- **意味の粒度**: 1ターゲット = 1責務（複数サービスを束ねる場合は `system-*` / `workflow-*` に寄せる）
- **オプションは変数で渡す**: `make eval-run DOMAIN_IDS=1,2,3,4` のように、原則は環境変数で分岐する

## 2. docker compose との対応

`Makefile` の *service* は、原則として `docker-compose.yml` の `services:` 名に合わせます。

`docker-compose.yml` の現行サービス名（抜粋）:

- `autoware`
- `autoware-build`（overlay build）
- `simulator`
- `autoware-command`（単発コマンド実行用）
- `rosbag`
- `driver`
- `zenoh`
- `rviz2`

例（compose に合わせたターゲット名）:

- `autoware-up` / `autoware-up-vehicle`（`RUN_MODE=...` を内部で切替）
- `autoware-build`（compose service: `autoware-build`）
- `simulator-up` / `simulator-reset`
- `rviz2-up`

GPU の扱い:

- `docker-compose.yml` は CPU 前提のベース、`docker-compose.gpu.yml` で GPU 設定を上書きする
- Makefile 側で `DEVICE=auto|gpu|cpu` に応じて compose file を選択する（`-f docker-compose.yml -f docker-compose.gpu.yml`）
- 例: `make simulator-up DEVICE=gpu` → `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d simulator`

## 3. service の命名

service は「操作対象のまとまり」を表します（docker compose のサービス名と 1:1 に揃えるのが基本）。

推奨の service 例:

- `autoware` : Autoware 起動・操作
- `autoware-build` : Autoware overlay ビルド
- `simulator` : AWSIM 起動・操作
- `autoware-command` : 単発コマンド実行用
- `eval` : 評価オーケストレーション（複数サービスを束ねる）
- `rviz2` : 可視化（RViz2）
- `rosbag` : 記録
- `driver` : racing_kart_interface
- `zenoh` : Zenoh bridge
- `compose` : docker compose の直操作（`compose-ps` / `compose-down` など）
- `system` : 実車/フル構成など「一括起動」（`system-up-*` など）

## 4. command（動詞）の語彙

以下の語彙を優先し、同義語を増やさない（例: `start` と `up` を混在させない）。

- `up` : 起動（基本は `docker compose up -d`）
- `stop` : 停止（コンテナは残す）
- `down` : 停止 + リソース削除（`docker compose down`）
- `restart` : 再起動
- `build` : ビルド
- `run` : 1回実行（評価など）
- `ps` : 状態表示
- `logs` : ログ表示
- `exec` / `shell` : コンテナ内でコマンド/シェル

## 5. variant（末尾サフィックス）

`-<variant>` は「変数で表すのが難しい、固定の差分」だけに使います。

例:
- `autoware-up-vehicle` / `autoware-up-sim`（mode 固定のショートカット）
- `eval-run-1-4`（ただし可能なら `DOMAIN_IDS=1,2,3,4` を推奨）

避けたい例:
- `eval-run-fast`（意味が曖昧。具体的に `RESULT_WAIT_SECONDS=...` で表現する）

## 6. 例（Good / Bad）

Good:
- `autoware-up` / `autoware-up-vehicle`
- `autoware-build`
- `simulator-up` / `simulator-reset`
- `eval-run`（`DOMAIN_ID` / `DOMAIN_IDS` などは変数で）
- `compose-ps` / `compose-down`

Bad（語順が逆/曖昧）:
- `build-autoware`（→ `autoware-build`）
- `simulator-eval`（→ `eval-run` など）
- `start` / `init` / `reset`（→ `simulator-start` / `simulator-init` / `simulator-reset` のように service を明示）

## 7. 変更時の互換性（推奨）

既存ターゲット名を変更する場合:
- 旧ターゲットは **alias として残す**（1〜2リリース程度）
- 旧ターゲット実行時に **DEPRECATED メッセージ**を出し、新ターゲットに委譲する
