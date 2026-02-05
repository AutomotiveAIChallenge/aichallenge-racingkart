# aichallenge ディレクトリ Readme（設計メモ）

このディレクトリ（`aichallenge/`）は、評価・ビルド・起動を行うための「コンテナ内エントリポイント群」をまとめた場所です。
スクリプト同士の責務分離と、失敗時に原因を追いやすいログ/終了コードを重視しています。

## 設計方針（読みやすさ優先）

- 1コマンド1責務: `run_evaluation.bash` はオーケストレーション、`utils/publish.bash` は「単発のROS操作」に寄せる
- Ctrl+C で確実に止まる: `EXIT` の cleanup と `SIGINT/SIGTERM` のハンドラを分けて扱う
- Domain ID の副作用を局所化: できるだけ `env ROS_DOMAIN_ID=... <cmd>` で「そのコマンドだけ」切り替える
- ビルドはコンテナ内で完結: ホスト（src環境）でのビルドは前提にしない
- cleanup はプロセス停止まで含める: `nohup` で起動したプロセスは PID/SID/PGID を使って停止し、残骸（例: `domain_bridge`）も可能な範囲で回収する

## リポジトリ構成（トップレベル）

- `aichallenge/`: シミュレータ/Autoware/評価の起動・操作スクリプト（本ドキュメントの対象）
- `vehicle/`: 実車環境向け（セットアップ確認、Zenoh、rosbag等）。詳細は `vehicle/README.md`
- `remote/`: 実車/遠隔接続の補助（SSH/Zenoh/RViz/joy）
- `output/`: 実行結果・ログの出力先（タイムスタンプ + `latest`）。ソースではない
- `submit/`: 提出物（tar.gz）置き場
- `Dockerfile`: dev/eval 向けイメージ定義
- `docker_build.sh` / `docker_run.sh` / `docker_exec.sh`: Docker/rocker のラッパ（ホスト側ログを `output/_host/latest/` に残す）
- `run_parallel_submissions.bash`: 複数提出物を別 eval イメージとしてビルドし、`autoware-d1..dN` を並列起動する（設計メモ: `../design_docs/run_parallel_submissions.md`）
- `create_submit_file.bash`: `aichallenge/workspace/src/aichallenge_submit` を tar 化して提出物を作成
- `remote/gui_tools.py`: `remote/` 配下の操作をGUI化（最終的な実体はシェルスクリプトに寄せる）
- `design_docs/`: 開発/運用メモ（例: `design_docs/run_parallel_submissions.md`）
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
- `aichallenge/utils/`: 補助スクリプト群（`publish.bash` / `record_rosbag.bash` / `topic_check.sh` など）

## `aichallenge/` 配下の主要ファイル（設計思想）

- `aichallenge/run_evaluation.bash`: 評価オーケストレータ。起動→待機→初期化→収集→後処理までを1本で管理
- `aichallenge/utils/publish.bash`: 単発のROS操作CLI（トピック待ち/リセット等）。終了コードをそのまま返す
- `aichallenge/utils/move_window.bash`: （可能なら）AWSIM/RViz のウィンドウ位置調整。`wmctrl` がない場合は何もしない
- `aichallenge/build_autoware.bash`: overlay(`aichallenge/workspace/`) のビルド。必要なら `clean` で `build/install/log` を削除
- `aichallenge/run_simulator.bash`: AWSIM の起動。GPUデバイスの有無で headless を切り替え、SIM側 Domain を固定（`ROS_DOMAIN_ID=0`）
- `aichallenge/run_autoware.bash`: Autoware の起動。`awsim/vehicle/rosbag` などモード別に launch 引数を整理
- `aichallenge/utils/run_rviz.bash`: RViz の起動補助（ローカル/実車/remote 用）。可視化は本質ではないので簡易スクリプトで十分
- `aichallenge/utils/record_rosbag.bash`: rosbag 記録。`SIGINT/SIGTERM/EXIT` で `ros2 bag record` を止める
- `aichallenge/utils/pkill_all.bash`: デバッグ/緊急停止用。関連プロセスを強制終了して環境を戻す
- `aichallenge/utils/topic_check.sh`: 走行前のトピック存在/HZチェック。ログは `output/latest/` に残す運用を想定

## 評価フロー（現状）

評価の詳細（オーケストレーション）は、`autostart_orchestrator_py` 側のドキュメントに集約しました。

- `aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md`
