---
marp: true
theme: gaia
paginate: true
size: 16:9
title: AI Challenge 2026 リポジトリ入門
description: 初学者向けに、このリポジトリの構造と基本操作を短時間で把握するためのスライド
style: |
  section {
    font-size: 25px;
  }
  h1, h2 {
    color: #0f3557;
  }
  code {
    font-size: 0.85em;
  }
---

# AI Challenge 2026  
## リポジトリ入門 (初学者向け)

- 対象: このリポジトリを初めて触る人
- 目的: 「どこに何があり、何から動かすか」を10分で把握する
- ゴール: 開発・評価・提出までの最短ルートを理解する

---

## このリポジトリでできること

- AWSIM + Autoware の実行環境を起動できる
- 開発実行 (`make dev`) と評価実行 (`make eval`) を使い分けられる
- 実行ログを `output/` に整理し、提出物を `submit/` に作れる
- シミュレータ運用から実車補助 (`vehicle/`, `remote/`) まで周辺ツールが揃っている

---

## まず覚える5コマンド

```bash
./docker_build.sh dev       # 開発用イメージをビルド（最初に1回）
make autoware-build         # Autoware/ROS 2 overlay をビルド
make dev                    # AWSIM + Autoware を開発モードで起動（1台）
make eval                   # 評価フローを一括実行
make down                   # コンテナ停止
```

- 迷ったらこの5つから始める
- セットアップは `./setup.bash bootstrap` で一括実行

---

## 全体像 (ホストとコンテナ)

1. ホストで `make` / `bash` コマンドを実行
2. `docker compose` が各サービスを起動
3. `simulator` (AWSIM) と `autoware` が連携
4. 結果は `output/` に保存、提出物は `submit/` に出力

---

## トップレベル構造

- `aichallenge/`: ビルド・起動・評価の中核
- `aichallenge/workspace/src/`: ROS 2 overlay のソース
- `aichallenge/simulator/`: AWSIM 実行データ
- `aichallenge/utils/`: publish/reset/rosbag などの補助スクリプト
- `aichallenge/simulator_scripts/`: シナリオ別 AWSIM 起動スクリプト
- `vehicle/`: 実車向け補助スクリプト
- `remote/`: SSH/GUI など遠隔運用補助
- `docs/design/specs/`: 手順書・運用設計資料
- `output/`, `submit/`: 実行結果と提出アーカイブ

---

## `aichallenge/` の中で重要なもの

- `build_autoware.bash`: コンテナ内ビルド
- `run_simulator.bash`: AWSIM 起動（`simulator_scripts/<mode>.sh` に委譲）
- `run_autoware.bash`: Autoware 起動
- `run_evaluation.bash`: 評価スクリプト（コンテナ内から呼ばれる。直接実行しない）
- `simulator_scripts/`: シナリオ別スクリプト（dev, eval, gate, multiplay など）

補足:
- ホストからの評価実行は `make eval`（= `docker compose up -d autoware-simulator-evaluation` + `awsim-request-start`）を使う
- 複数 Domain の並列起動は `make dev2` / `make dev3` / `make dev4` を使う

---

## 開発フロー (日常の反復)

1. `aichallenge/workspace/src/` などを変更する
2. `make autoware-build` でビルド
3. `make dev` で動作確認
4. 問題があればログを確認 (`/output/latest/d1`)
5. `make down` で停止

ポイント:
- `make dev` は常駐プロセス。手動停止を忘れずに

---

## 評価フロー (提出前の確認)

1. `./docker_build.sh eval --submit submit/aichallenge_submit.tar.gz` で評価用イメージを作成
2. `make eval` を実行（バックグラウンドで起動し、評価が自動進行する）
3. `output/<timestamp>/` に結果が保存される
4. `/output/latest/d1` で最新結果を確認
5. 終了後は **`make down` で停止**（自動で片付かない）

シナリオを変えたいとき:
```bash
SIM_MODE=gate1 make eval    # 安全ゲートシナリオ など
# 詳細は aichallenge/simulator_scripts/README.md 参照
```

---

## GPU / CPU の切り替え

`.env` の `COMPOSE_FILE` で選択します（`docker-compose.eval.yml` は必須）。

```bash
# CPU + サウンド（デフォルト）
COMPOSE_FILE=docker-compose.yml:docker-compose.eval.yml:docker-compose.sound.yml

# GPU（NVIDIA）+ サウンド
COMPOSE_FILE=docker-compose.yml:docker-compose.eval.yml:docker-compose.gpu.yml:docker-compose.sound.yml

# WSL2（WSLg）
COMPOSE_FILE=docker-compose.yml:docker-compose.eval.yml:docker-compose.wsl.yml
```

`./setup.bash env` を使うと GPU の有無を自動検出して `.env` を作成します。

---

## ログと提出物の見方

- `/output/latest/`:
  - 最新ランを格納する固定ディレクトリ
  - `d1`/`d2`... 配下の固定名シンボリックリンクで成果物を参照する
- `submit/aichallenge_submit.tar.gz`:
  - `./create_submit_file.bash` で生成する提出用アーカイブ

---

## よくある詰まりどころ

- `install/setup.bash` がない:
  - `make autoware-build` を先に実行する
- 起動が不安定/止まらない:
  - `make down` で停止してから再実行
- Domain の設定が混乱している:
  - `ROS_DOMAIN_ID` を `.env` で設定する（デフォルト `1`）
- 複数台で動かしたい:
  - `make dev2` / `make dev3` / `make dev4` を使う

---

## どの資料から読むべきか

1. `docs/design/specs/how-to-setup.md`
2. `docs/design/specs/introduction.md`
3. このスライド (`docs/design/specs/beginner-deck.marp.md`)

- まずセットアップと基本実行の2本を押さえる

---

## まとめ

- 最初は「構造理解」より「実行して結果を見る」を優先
- 基本コマンドは `build -> dev -> eval -> down`
- ログは `/output/latest/d1`、提出物は `submit/`
- 慣れたら `vehicle/` と `remote/` に進む
