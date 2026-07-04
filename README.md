# aichallenge-racingkart

本リポジトリでは、自動運転AIチャレンジでご利用いただく開発環境を提供します。参加者の皆様には、Autoware Universe をベースとした自動運転ソフトウェアを開発し、予選大会では End to End シミュレーション空間を走行するレーシングカートにインテグレートしていただきます。開発した自動運転ソフトウェアで、安全に走行しながらタイムアタックに勝利することが目標です。また、決勝大会では本物のレーシングカートへのインテグレーションを行っていただきます。

This repository provides a development environment use in the Automotive AI Challenge. For the preliminaries, participants will develop autonomous driving software based on Autoware Universe and integrate it into a racing kart that drives in the End to End simulation space. The goal is to win in time attack while driving safely with the developed autonomous driving software. Also, for the finals, qualifiers will integrate it into a real racing kart.

## ドキュメント / Documentation

ルールの詳細や環境構築方法など、大会に関する情報を以下のページで提供します。

Toward the competition, we will update the following pages to provide information such as rules and how to set up your dev environment. Please follow them. We are looking forward your participation!

- [日本語ページ](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/)
- [English Page](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/en/)
- [スクリプト設計メモ（評価/ビルド/起動）](aichallenge/README.md)

## リポジトリ構成（トップレベル）

- `aichallenge/`: シミュレータ/Autoware/評価の起動・操作スクリプト群
- `vehicle/`: 実車環境向け（セットアップ確認、Zenoh、rosbag など）
- `remote/`: 実車/遠隔接続の補助（SSH/Zenoh/RViz/joy）
- `docs/`: ガイド・スライドは [`docs/guide/`](docs/guide/)、仕様は [`docs/spec/`](docs/spec/)、インターフェイス契約は [`docs/interface/`](docs/interface/)
- `submit/`: 提出物（tar.gz）置き場
- `output/`: 実行結果・ログ出力先（生成物）

## Docker Compose（推奨）

### 全体像（開発: Makefile / 個別起動）

```text
Host (you)
  ├─ make autoware-build / make dev / make eval / make simulator ...
  └─ docker compose（.env の COMPOSE_FILE でオーバーレイを選択）
        ├─ docker-compose.yml       (ベース: 全サービス定義)
        │     ├─ autoware           (Autoware)
        │     ├─ autoware-build     (colcon build 専用)
        │     ├─ simulator          (AWSIM)
        │     ├─ autoware-command   (ros2 service/topic の単発操作)
        │     ├─ zenoh              (Zenoh ブリッジ)
        │     ├─ driver             (実車ドライバ)
        │     └─ rviz2              (可視化)
        ├─ docker-compose.gpu.yml   (NVIDIA GPU オーバーレイ、オプション)
        └─ docker-compose.sound.yml (PulseAudio / simulator のみ、オプション)
        → output/ にログ・結果を出力（最新結果は output/latest/d<domain>/）
```

オーバーレイの選択は `.env` の `COMPOSE_FILE` 行で行う（`docker compose` が自動読み込み）。
`./setup.bash env` を実行すると GPU の有無を自動検出して `.env` を生成する。

### はじめてのセットアップ

```bash
# 1. 環境セットアップ（Docker インストール〜.env 生成〜イメージ取得〜AWSIM DL）
./setup.bash bootstrap

# または個別に実施する場合:
./setup.bash env            # .env 生成（GPU/CPU 自動判定）
./setup.bash network tune   # DDS ホストチューニング（rmem_max + loマルチキャスト）
./setup.bash download awsim # AWSIM バイナリのダウンロード
./setup.bash pull image     # Autoware ベースイメージの取得

# 2. 開発イメージのビルド
./docker_build.sh dev

# 3. ROS 2 ワークスペースのビルド（コンテナ内 colcon）
make autoware-build

# 4. 開発ループ（AWSIM + Autoware 起動）
make dev
make autoware-request-initialpose
make autoware-request-control
make down
```

コンテナはホストの UID/GID（`HOST_UID`/`HOST_GID`）で動作するため、`output/` 配下の生成物はホストユーザー所有になり root 権限は不要です。

## まずは読んでほしいもの

- [初学者向けセットアップ資料](./docs/spec/how-to-setup.md)
- [初学者向け説明資料](./docs/spec/introduction.md)
- [初学者向けリポジトリ入門スライド (Marp)](./docs/guide/beginner-deck.marp.md)

## OSS 貢献にあたって

PR を出す前に `pre-commit run -a` を通してください。

```.sh
check for merge conflicts................................................Passed
check xml................................................................Passed
check yaml...............................................................Passed
detect private key.......................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
shellcheck...............................................................Passed
shfmt....................................................................Passed
```
