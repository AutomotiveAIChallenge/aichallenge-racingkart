# aichallenge ディレクトリ Readme（設計メモ）

このディレクトリ（`aichallenge/`）は、評価・ビルド・起動を行うための「コンテナ内エントリポイント群」をまとめた場所です。
スクリプト同士の責務分離と、失敗時に原因を追いやすいログ/終了コードを重視しています。

## 設計方針（読みやすさ優先）

- 1コマンド1責務: `run_evaluation.bash` はオーケストレーション、`utils/*` の補助スクリプトは「単発のROS操作」に寄せる
- Ctrl+C で確実に止まる: `EXIT` の cleanup と `SIGINT/SIGTERM` のハンドラを分けて扱う
- Domain ID の副作用を局所化: できるだけ `env ROS_DOMAIN_ID=... <cmd>` で「そのコマンドだけ」切り替える
- ビルドはコンテナ内で完結: ホスト（src環境）でのビルドは前提にしない
- cleanup はプロセス停止まで含める: `nohup` で起動したプロセスは PID/SID/PGID を使って停止し、残骸（ros2 launch 配下のサブプロセスなど）も可能な範囲で回収する

## `aichallenge/` 配下のディレクトリ（設計思想）

- `aichallenge/workspace/`: ROS 2 overlay の colcon ワークスペース（`src/` をビルドして `install/` を生成）
- `aichallenge/simulator/`: AWSIM バイナリ/データ。`run_simulator.bash` はここを参照して起動する
- `aichallenge/ml_workspace/`: 学習/データ収集用（この配下は独立性を高く保ち、別READMEで説明）
- `aichallenge/utils/`: 補助スクリプト群（`topic_check.sh`、`run_rviz.bash`、`record_rosbag.bash` など）

## `aichallenge/` 配下の主要ファイル（設計思想）

- `aichallenge/run_evaluation.bash`: 評価オーケストレータ。起動→待機→初期化→収集→後処理までを1本で管理
- `aichallenge/build_autoware.bash`: overlay(`aichallenge/workspace/`) のビルド。必要なら `clean` で `build/install/log` を削除
- `aichallenge/run_simulator.bash`: AWSIM の起動。`SIM_MODE` をファイル名として `simulator_scripts/<mode>.sh` に委譲（起動引数の正本は各スクリプト側）
- `aichallenge/run_autoware.bash`: Autoware の起動。`awsim/vehicle/rosbag` などモード別に launch 引数を整理
- `aichallenge/utils/run_rviz.bash`: RViz の起動補助（`awsim`/`vehicle`/`remote` モード）。可視化は本質ではないので簡易スクリプトで十分
- `aichallenge/utils/topic_check.sh`: 走行前のトピック存在/HZチェック。ログは `output/latest/` に残す運用を想定
- `aichallenge/utils/record_rosbag.bash`: rosbag 手動記録用スクリプト

## 評価フロー（現状）

評価の詳細（オーケストレーション）は、`autostart_orchestrator_py` 側のドキュメントに集約しました。

- `aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md`
