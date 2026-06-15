# Makefile ターゲット命名ガイドライン（サービス-コマンド）

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

このリポジトリの `Makefile` ターゲットは **`<service>-<command>`** 形式を基本とし、一覧性・検索性・拡張性を重視します。

## 1. 基本ルール

- **形式**: `<service>-<command>[-<variant>]`
- **文字種**: `a-z` / `0-9` / `-` のみ（小文字）。`_` は使わない
- **語順**: 先頭は必ず *service*（`build-*` / `run-*` のような動詞始まりは避ける）
- **意味の粒度**: 1ターゲット = 1責務（複数サービスをまとめる場合は `system-*` / `workflow-*` を使う）
- **オプションは変数で渡す**: `DOMAIN_IDS=1,2,3,4 make eval` のように環境変数で分岐する

## 2. docker compose との対応

`Makefile` の *service* は、原則として `docker-compose.yml` の `services:` 名に合わせます。

`docker-compose.yml` の現行サービス名（抜粋）:

- `autoware`
- `autoware-build`（overlay build）
- `simulator`
- `autoware-command`（単発コマンド実行用）
- `driver`
- `zenoh`
- `rviz2`

ターゲット名の例（compose サービス名に対応）:

- `autoware-simulator` / `autoware-vehicle`（`RUN_MODE=...` を内部で切替）
- `autoware-build`（compose service: `autoware-build`）
- `simulator` / `awsim-request-start`
- `rviz2`

GPU / 音声の構成は `.env` の `COMPOSE_FILE` で指定します:

| 構成 | `COMPOSE_FILE` の値 |
|------|---------------------|
| CPU + 音声（デフォルト） | `docker-compose.yml:docker-compose.sound.yml` |
| GPU + 音声 | `docker-compose.yml:docker-compose.gpu.yml:docker-compose.sound.yml` |
| ヘッドレス（音声なし） | `docker-compose.yml` |

`docker-compose.yml` は CPU 前提のベース。eval サービスも含みます。

## 3. service の命名

service は「操作対象のまとまり」を表します（docker compose のサービス名と 1:1 に揃えるのが基本）。

推奨の service 例:

- `autoware` : Autoware 起動・操作
- `autoware-build` : Autoware overlay ビルド
- `simulator` : AWSIM 起動・操作
- `autoware-command` : 単発コマンド実行用
- `eval` : 評価オーケストレーション（複数サービスをまとめる）
- `dev` : 開発用（AWSIM + Autoware 起動のみ）
- `rviz2` : 可視化（RViz2）
- `driver` : racing_kart_interface
- `zenoh` : Zenoh bridge
- `compose` : docker compose の直操作（`compose-ps` / `compose-down` など）
- `system` : 実車/フル構成など一括起動（`system-up-*` など）

## 4. command（動詞）の語彙

同義語を増やさず、以下の語彙に統一します（例: `start` と `up` を混在させない）。

- `up` : 起動（基本は `docker compose up -d`。このリポジトリでは **ターゲット名に `-up` を付けず**、service 名そのものを「起動」として扱う）
- `stop` : 停止（コンテナは残す）
- `down` : 停止 + リソース削除（`docker compose down`）
- `restart` : 再起動
- `build` : ビルド
- `run` : 1回実行（評価など）
- `ps` : 状態表示
- `logs` : ログ表示
- `exec` / `shell` : コンテナ内でコマンド/シェル

## 5. variant（末尾サフィックス）

`-<variant>` は「変数で表しにくい固定の差分」だけに使います。

例:
- `autoware-vehicle` / `autoware-simulator`（mode 固定のショートカット）
- `simulator-<mode>` は `aichallenge/simulator_scripts/*.sh` のベース名（`dev`/`eval`/`gate`/`multiplay-client`/`multiplay-host`/`multiplay-server`/`parallel`/`sample-scenario`/`simulator`）から自動生成され、さらに `dev2`/`dev3`/`dev4`/`gate1`/`gate2`/`gate3` が追加される
- 評価の複数 domain 実行は `DOMAIN_IDS=1,2,3,4` など **変数で表現**する（`*-1-4` のような固定サフィックスは作らない）

避けたい例:
- `eval-run-fast`（意味が曖昧。`RESULT_WAIT_SECONDS=...` のような変数で表現する）

## 6. 例（Good / Bad）

Good:
- `autoware-simulator` / `autoware-vehicle`
- `autoware-build`
- `simulator` / `awsim-request-start`
- `eval`（`DOMAIN_ID` / `DOMAIN_IDS` などは変数で）
- `dev` / `dev2` / `dev3` / `dev4`
- `compose-ps` / `compose-down`

Bad（語順が逆・曖昧・存在しない）:
- `build-autoware`（→ `autoware-build`）
- `simulator-eval`（旧名。現在は `eval` を使用）
- `start` / `init` / `reset`（→ `awsim-request-start` / `awsim-request-reset` のように service を明示）
- `run-sim-eval` / `run-sim-eval-1-4`（存在しない。`make eval` を使う）
- `dev-ready` / `dev-multi`（存在しない）

## 7. 変更時の互換性

互換 alias を残さずにターゲット名を整理する場合があります。
`README.md` / `docs/spec/` と `docker-compose.yml` の整合を優先してください。

### 削除・変更されたターゲット

- `down2` / `down3` / `down4`（旧: `down` への互換 alias）は削除しました。`make down` が
  プロジェクト 1〜4 を含め全コンテナを停止するため、`make down` に一本化してください。
