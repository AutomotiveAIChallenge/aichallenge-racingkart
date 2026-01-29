# aichallenge-2025

本リポジトリでは、2025年度に実施される自動運転AIチャレンジでご利用いただく開発環境を提供します。参加者の皆様には、Autoware Universe をベースとした自動運転ソフトウェアを開発し、予選大会では End to End シミュレーション空間を走行するレーシングカートにインテグレートしていただきます。開発した自動運転ソフトウェアで、安全に走行しながらタイムアタックに勝利することが目標です。また、決勝大会では本物のレーシングカートへのインテグレーションを行っていただきます。

This repository provides a development environment use in the Automotive AI Challenge which will be held in 2025. For the preliminaries, participants will develop autonomous driving software based on Autoware Universe and integrate it into a racing kart that drives in the End to End simulation space. The goal is to win in time attack while driving safely with the developed autonomous driving software. Also, for the finals, qualifiers will integrate it into a real racing kart.

## ドキュメント / Documentation

下記ページにて、本大会に関する情報 (ルールの詳細や環境構築方法) を提供する予定です。ご確認の上、奮って大会へご参加ください。

Toward the competition, we will update the following pages to provide information such as rules and how to set up your dev environment. Please follow them. We are looking forward your participation!

- [日本語ページ](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/)
- [English Page](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/en/)
- [スクリプト設計メモ（評価/ビルド/起動）](aichallenge/Readme.md)

## Docker Compose（推奨）

- `cp .env.example .env`（必要に応じて編集）
- ビルド: `./docker_build.sh dev`
- 評価用イメージのビルド（提出物を差し替えたい場合）: `./docker_build.sh eval --submit submit/aichallenge_submit.tar.gz`
- ビルド + 評価（提出物 tar.gz を eval イメージとしてビルドして実行）: `./run_parallel_submissions.bash --submit submit/aichallenge_submit.tar.gz --device gpu`
  - 複数 `--submit` 時は順に domain id を 1..4 に固定割当します
- Autoware(overlay) ビルド: `make autoware-build`
- 評価（`aichallenge/run_evaluation.bash` 相当）: `make simulator-eval`
  - オプション例: `make simulator-eval DEVICE=gpu DOMAIN_ID=1 ROSBAG=true CAPTURE=false RESULT_WAIT_SECONDS=10`
  - 複数domainを連続実行: `make simulator-eval DOMAIN_IDS=1,2,3,4`
  - 省略形: `make simulator-eval-1-4`
  - 出力: `output/<timestamp>/d<domain_id>/`（`output/latest` は可能ならシンボリックリンク）
- 個別起動: `make simulator` / `make autoware-simulator` / `make autoware-vehicle`
- GPU: Autoware/Simulator は自動検出（強制: `DEVICE=gpu`、CPU強制: `DEVICE=cpu`）。`autoware-build` / `rviz2` は常に CPU サービスを使用
- ウィンドウ移動の調整: `MOVE_WINDOW_DEBUG=1 MOVE_WINDOW_QUIET=0 make simulator-eval`（必要なら `AWSIM_*_REGEX` / `RVIZ_*_REGEX` を指定）

## 複数提出物の並列起動（run_parallel_submissions）

- 複数提出物を **別々の eval イメージとしてビルド**し、`autoware-d1..dN` を **並列起動**: `./run_parallel_submissions.bash --submit ... --submit ...`
- 詳細（入出力/生成される compose override/ディレクトリ構成）: `design_docs/run_parallel_submissions.md`
