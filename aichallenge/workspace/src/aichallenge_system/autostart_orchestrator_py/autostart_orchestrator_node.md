# autostart_orchestrator_node

## 目的

Autostart オーケストレータは、車両状態を起点にデータ収集の開始・停止を制御し、必要な初期化処理を実行します。

想定ユースケース:
- evaluation 用の記録開始/停止を固定フローで自動化する
- `awsim_state_manager_node.py` と組み合わせて、AWSIM開始指示と整合した走行を実施する

重要:
- `/admin/awsim/start` (`std_msgs/Bool`) のやり取りは行いません。
- `/admin/awsim/start` の publish/subscribe は責務分離により `awsim_state_manager_node.py` のみが担当します。

## 対応ノード

- ノード名: `autostart_orchestrator`
- 実行ファイル: `autostart_orchestrator_py/autostart_orchestrator_node.py`

## 購読

- `vehicle_state_topic`（default: `/awsim/state`）`std_msgs/String`
  - 値: `Spawned`, `Grounded`, `Ready`, `Start`, `Finish`

## サービス呼び出し

- `initial_pose_service`（default: `/set_initial_pose`）: `std_srvs/srv/Trigger`
- `capture_service`（default: `/debug/service/capture_screen`）: `std_srvs/srv/Trigger`

## publish

- `control_mode_request_topic`（default: `/awsim/control_mode_request_topic`）へ `std_msgs/Bool(True)`
  - `request_control_mode=true` のときに送信

## ローカル処理

- rosbag 録画子プロセス起動/停止（`subprocess`）
- capture service 開始/停止
- 初期姿勢要求（`/set_initial_pose`）
- ログ監視・デバッグ可視化（`enable_debug_visualization`）
  - Qtパネル表示: `state`, `detail`, `vehicle`, `vehicle_state`, `vehicle_topic`

## ワークフロー状態

`_WORKFLOW_STATES`

1. `BOOT`
2. `WAIT_INITIAL_POSE`
3. `REQUEST_CONTROL_MODE`
4. `IDLE`
5. `WAIT_START`
6. `RECORDING`
7. `WAIT_STOP`
8. `AUTO_STOP_DISABLED`
9. `STOPPING`
10. `FINISHED`
11. `ERROR`

## 制御フロー（要約）

1. `start_on_vehicle_state` で開始待機し、到達時に初期化 (`/set_initial_pose` と `control_mode_request_topic`) を実行
2. 記録開始 (`enable_capture`, `enable_rosbag`)
3. `RECORDING` 開始
4. `stop_on_vehicle_state` 到達で停止処理実行
5. `exit_on_finish=true` の場合は終了処理を実行

## パラメータ

- `vehicle_state_topic`（required）
- `start_on_vehicle_state`（required）
- `stop_on_vehicle_state`（required）
- `exit_on_finish`（required）
- `enable_capture`（required）
- `enable_rosbag`（required）
- `rosbag_topics`
- `rosbag_output`
- `rosbag_storage_id`
- `rosbag_compression_format`
- `rosbag_compression_mode`
- `call_initial_pose`
- `request_control_mode`
- `initial_pose_service`
- `control_mode_request_topic`
- `capture_service`
- `enable_debug_visualization`（任意: default false）

※ `autostart_orchestrator.param.yaml` を参照し、`evaluation.launch.xml` の `start_on_vehicle_state` などを上書きできます。

## launch

- `aichallenge_system_launch/launch/mode/awsim.launch.xml`
  - autoware 起動とセットでノードを立てる想定
  - `capture` / `rosbag` / `start_on_vehicle_state` / `stop_on_vehicle_state` / `exit_on_finish` を受ける
  - 既定 `start_on_vehicle_state` は `Ready,Start`

## 責務境界

- `autostart_orchestrator_node.py` は `/admin/awsim/start` の送受信を行わない
- `/admin/awsim/start` による開始トリガは `awsim_state_manager_node.py` 側の責務
