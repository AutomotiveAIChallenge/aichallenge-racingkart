# aichallenge-racingkart

本リポジトリでは、自動運転AIチャレンジでご利用いただく開発環境を提供します。参加者の皆様には、Autoware Universe をベースとした自動運転ソフトウェアを開発し、予選大会では End to End シミュレーション空間を走行するレーシングカートにインテグレートしていただきます。開発した自動運転ソフトウェアで、安全に走行しながらタイムアタックに勝利することが目標です。また、決勝大会では本物のレーシングカートへのインテグレーションを行っていただきます。

This repository provides a development environment use in the Automotive AI Challenge. For the preliminaries, participants will develop autonomous driving software based on Autoware Universe and integrate it into a racing kart that drives in the End to End simulation space. The goal is to win in time attack while driving safely with the developed autonomous driving software. Also, for the finals, qualifiers will integrate it into a real racing kart.

## ドキュメント / Documentation

下記ページにて、本大会に関する情報 (ルールの詳細や環境構築方法) を提供する予定です。ご確認の上、奮って大会へご参加ください。

Toward the competition, we will update the following pages to provide information such as rules and how to set up your dev environment. Please follow them. We are looking forward your participation!

- [日本語ページ](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/)
- [English Page](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/en/)
- [スクリプト設計メモ（評価/ビルド/起動）](aichallenge/README.md)

## Docker Compose（推奨）

### 全体像（開発: Makefile / 個別起動）

```text
Host (you)
  ├─ make autoware-build / ./run_evaluation.bash / make dev / make simulator ...
  └─ docker compose
        ├─ simulator        (AWSIM)
        ├─ autoware         (Autoware)
        ├─ autoware-command (ros2 service/topic の単発操作)
        └─ output/ にログ・結果を出力（output/latest は最新runへのsymlink）
```

## まずは読んでほしいもの

[初学者向けセットアップ資料](./design_docs/HowToSetup.md)

[初学者向け説明資料](./design_docs/Introduction.md)

## OSS貢献にあたって

`pre-commit run -a`を必ず通すこと

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
