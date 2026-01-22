# aichallenge ディレクトリ Readme（設計メモ）

このディレクトリ（`aichallenge/`）は、評価・ビルド・起動を行うための「コンテナ内エントリポイント群」をまとめた場所です。
スクリプト同士の責務分離と、失敗時に原因を追いやすいログ/終了コードを重視しています。

## 設計方針（読みやすさ優先）

- 1コマンド1責務: `run_evaluation.bash` はオーケストレーション、`publish.bash` は「単発のROS操作」に寄せる
- 失敗は終了コードで返す: 呼び出し側は `$?` か `run_or_exit` で一貫して判定できる
- 無限待ちを避ける: `timeout` を基本にして、待ち/サービス呼び出しのハングを防ぐ
- Ctrl+C で確実に止まる: `EXIT` の cleanup と `SIGINT/SIGTERM` のハンドラを分けて扱う
- Domain ID の副作用を局所化: できるだけ `env ROS_DOMAIN_ID=... <cmd>` で「そのコマンドだけ」切り替える
- ビルドはコンテナ内で完結: ホスト（src環境）でのビルドは前提にしない
- cleanup はプロセス停止まで含める: `nohup` で起動したプロセスは PID/SID/PGID を使って停止し、残骸（例: `domain_bridge`）も可能な範囲で回収する

## リポジトリ構成（トップレベル）

- `aichallenge/`: シミュレータ/Autoware/評価の起動・操作スクリプト（本ドキュメントの対象）
- `vehicle/`: 実車環境向け（セットアップ確認、Zenoh、rosbag等）。詳細は `vehicle/Readme.md`
- `remote/`: 実車/遠隔接続の補助（SSH/Zenoh/RViz/joy）
- `output/`: 実行結果・ログの出力先（タイムスタンプ + `latest`）。ソースではない
- `submit/`: 提出物（tar.gz）置き場
- `Dockerfile`: dev/eval 向けイメージ定義
- `docker_build.sh` / `docker_run.sh` / `docker_exec.sh`: Docker/rocker のラッパ（ログを `output/latest/` に残す）
- `create_submit_file.bash`: `aichallenge/workspace/src/aichallenge_submit` を tar 化して提出物を作成
- `make_gui.py`: `remote/` 配下の操作をGUI化（最終的な実体はシェルスクリプトに寄せる）
- `requirements.txt`: Python ツール類の依存
- `packages.txt`: 環境構築で必要な apt パッケージ一覧（用途はリポジトリ運用側に寄せる）
- `.pre-commit-config.yaml`: コード整形/静的解析の自動化（任意、開発体験の改善）
- `.gitignore`: ビルド成果物や出力の混入防止
- `LICENSE`: ライセンス

## `aichallenge/` 配下のディレクトリ（設計思想）

- `aichallenge/workspace/`: ROS 2 overlay の colcon ワークスペース（`src/` をビルドして `install/` を生成）
- `aichallenge/simulator/`: AWSIM バイナリ/データ。`run_simulator.bash` はここを参照して起動する
- `aichallenge/ml_workspace/`: 学習/データ収集用（この配下は独立性を高く保ち、別READMEで説明）
- `aichallenge/capture/`: 記録、画面キャプチャ関連の置き場（現状は予約領域）

## `aichallenge/` 配下の主要ファイル（設計思想）

- `aichallenge/run_evaluation.bash`: 評価オーケストレータ。起動→待機→初期化→収集→後処理までを1本で管理
- `aichallenge/publish.bash`: 単発のROS操作CLI（サービス呼び出し/トピック待ち）。終了コードをそのまま返す
- `aichallenge/build_autoware.bash`: overlay(`aichallenge/workspace/`) のビルド。必要なら `clean` で `build/install/log` を削除
- `aichallenge/run_simulator.bash`: AWSIM の起動。GPU有無で headless を切り替え、SIM側 Domain を固定（`ROS_DOMAIN_ID=0`）
- `aichallenge/run_autoware.bash`: Autoware の起動。`awsim/vehicle/rosbag` などモード別に launch 引数を整理
- `aichallenge/run_rviz.bash`: RViz の起動補助（ローカル/実車/remote 用）。可視化は本質ではないので簡易スクリプトで十分
- `aichallenge/record_rosbag.bash`: rosbag 記録。`SIGINT/SIGTERM/EXIT` で `ros2 bag record` を止める
- `aichallenge/pkill_all.bash`: デバッグ/緊急停止用。関連プロセスを強制終了して環境を戻す
- `aichallenge/topic_check.sh`: 走行前のトピック存在/HZチェック。ログは `output/latest/` に残す運用を想定
- `aichallenge/docker-compose.yml`: 役割別コンテナ（build/eval/autoware/simulator/zenoh等）を定義し、作業者の操作手順を簡略化
- `aichallenge/Makefile`: `docker compose` の操作を短いターゲットに集約（ログ表示や引数組み立ての隠蔽）
- `aichallenge/.env.example`: `docker-compose.yml` 用の環境変数テンプレ
- `aichallenge/autoware.log`: Autoware起動ログの一時出力（デバッグ用途）
- `aichallenge/result-details.json`: 結果JSONのサンプル/一時出力（採点用途）
- `aichallenge/d*-result-details.json`: Domainごとの結果JSONサンプル（採点用途）

## `run_evaluation.bash` の評価フロー（現状）

`aichallenge/run_evaluation.bash` は以下の流れで評価をオーケストレーションします。

1. 出力ディレクトリ作成（`/output/<timestamp>`、`latest` シンボリックリンク）
2. ROS/Autoware/overlay 環境の `source` と `ROS_DOMAIN_ID` の設定
3. ネットワーク設定（`sudo -n ...` を best-effort 実行）
4. AWSIM 起動（`run_simulator.bash eval` を `nohup` 起動）
5. AWSIM 準備待ち（`publish.bash check-awsim`。`/clock` を1回受け取るまで待つ）
6. Autoware 起動（`run_autoware.bash awsim <domain>` を `nohup` 起動）
7. （可能なら）ウィンドウ移動（`wmctrl` がある場合のみ、タイムアウト付き）
8. 初期姿勢/制御要求（`publish.bash request-initialpose` → `request-control`）
9. 任意で画面キャプチャ・rosbag 開始（フラグ指定時）
10. AWSIM 終了待ち → 結果変換（`result-details.json` を最大待ち）→ 終了
11. 終了時 `trap` により後処理（キャプチャ停止/rosbag停止/権限調整）

### 引数/オプション（抜粋）

- `rosbag` / `--rosbag`: rosbag 記録を有効化
- `capture` / `--capture`: 画面キャプチャを有効化
- `--uid N` / `--gid N`（互換: 末尾に `<uid> <gid>`）: 生成物の `chown` 用
- `--domain-id N`: Autoware 側の `ROS_DOMAIN_ID` として使用
- `--output-root PATH`: 出力先（デフォルト `/output`）
- `--result-wait-seconds N`: `result-details.json` の待ち秒数（デフォルト 10）

## 重要な実装上の注意点

### `/clock` 待ちの実装

`ros2 topic echo /clock --once` は「型推論ができない状態」だと `rc=1` で即失敗しやすいため、
`publish.bash check-awsim` ではメッセージ型（`rosgraph_msgs/msg/Clock`）を明示して待機します。

### Domain ID の扱い

AWSIM の `/clock` 待ちは、`env ROS_DOMAIN_ID=<sim>` を付けて「そのコマンドだけ」Domain を切り替えて実行します。
そのため `--domain-id` で指定した Autoware 側の Domain を上書きしません。
