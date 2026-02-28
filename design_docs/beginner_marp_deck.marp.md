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
- 目的: 「どこに何があり、何から実行するか」を10分で把握する
- ゴール: 開発・評価・提出までの最短ルートを理解する

---

## このリポジトリでできること

- AWSIM + Autoware の実行環境を起動できる
- 開発実行 (`make dev`) と評価実行 (`./run_evaluation.bash`) を切り替えられる
- 実行ログを `output/` に整理し、提出物を `submit/` に作れる
- シミュレータ運用から実車補助 (`vehicle/`, `remote/`) まで周辺ツールがそろっている

---

## まず覚える5コマンド

```bash
make autoware-build         # Autoware/ROS 2 overlay をビルド
make dev                    # AWSIM + Autoware を開発モードで起動
./run_evaluation.bash       # 評価フローを一括実行
./run_evaluation.bash test  # 短時間のスモークテスト
make down                   # コンテナ停止
```

- 迷ったらこの5つから始める

---

## 全体像 (ホストとコンテナ)

1. ホストで `make` / `bash` コマンドを実行
2. `docker compose` が各サービスを起動
3. `simulator` (AWSIM) と `autoware` が連携
4. 結果は `output/` に保存、提出は `submit/` に出力

---

## トップレベル構造

- `aichallenge/`: ビルド・起動・評価の中核
- `aichallenge/workspace/src/`: ROS 2 overlay のソース
- `aichallenge/simulator/`: AWSIM 実行データ
- `aichallenge/utils/`: publish/reset/rosbag などの補助
- `vehicle/`: 実車向け補助スクリプト
- `remote/`: SSH/GUI など遠隔運用補助
- `design_docs/`: 手順書や運用設計資料
- `output/`, `submit/`: 実行結果と提出アーカイブ

---

## `aichallenge/` の中で重要なもの

- `build_autoware.bash`: コンテナ内ビルド
- `run_simulator.bash`: AWSIM 起動
- `run_autoware.bash`: Autoware 起動
- `run_evaluation.bash`: 評価一括実行
- `run_parallel_submissions.bash`: 複数 Domain の並列評価

補足:
- `run_evaluation.bash` は単一 `DOMAIN_ID` 前提
- 複数 Domain は `run_parallel_submissions.bash` を使う

---

## 開発フロー (日常の反復)

1. 変更を加える (`aichallenge/workspace/src/` など)
2. `make autoware-build`
3. `make dev` で動作確認
4. 問題があればログ確認 (`/output/latest/d1`)
5. 停止は `make down`

ポイント:
- `make dev` は常駐系。手動停止を忘れない

---

## 評価フロー (提出前の確認)

1. `./run_evaluation.bash` を実行
2. 自動で準備・実行・収集・片付けが進む
3. `output/<timestamp>/` に結果が残る
4. `/output/latest/d1` で最新結果へアクセス

テストだけしたいとき:
```bash
./run_evaluation.bash test
```

---

## ログと提出物の見方

- `/output/latest/`:
  - 最新ランを格納する固定ディレクトリ
  - `d1`/`d2`... 配下の固定名シンボリックリンクで成果物を見る
- `submit/aichallenge_submit.tar.gz`:
  - `./create_submit_file.bash` で生成
  - 提出用アーカイブ

---

## よくある詰まりどころ

- `install/setup.bash` がない:
  - `make autoware-build` を先に実行
- 起動が不安定/止まらない:
  - `make down` で停止して再実行
- Domain の設定混乱:
  - `DOMAIN_ID` を明示して実行
- 「並列評価したい」:
  - `run_evaluation.bash` ではなく `run_parallel_submissions.bash`

---

## どの資料から読むべきか

1. `design_docs/how_to_setup.md`
2. `design_docs/introduction.md`
3. `design_docs/run_parallel_submissions.md`
4. このスライド (`design_docs/beginner_marp_deck.marp.md`)

- まずはセットアップと基本実行の2本を押さえる

---

## まとめ

- 最初は「構造理解」より「実行して結果を見る」を優先
- 基本コマンドは `build -> dev -> evaluation -> down`
- ログは `/output/latest/d1`、提出は `submit/`
- 慣れたら `vehicle/` と `remote/` に進む
