Autostart Orchestrator (aichallenge_system)
==========================================

このパッケージは、評価走行を自動化する2つのノードをまとめています。

- `autostart_orchestrator_node.py`
- `awsim_state_manager_node.py`

## ノード別ドキュメント

- `autostart_orchestrator_node.md`
  - Autostart側の状態遷移と、車両状態・記録の開始停止（記録開始/停止）フロー
- `awsim_state_manager.md`
  - AWSIM 実行状態の監視、`/admin/awsim/start` のトリガ送信、AWSIM終了時の kill フロー

## 責務分離ルール（重要）

- `autostart_orchestrator_node.py` は `/admin/awsim/start` の送受信を行いません。
- `/admin/awsim/start` の publish/subscribe は `awsim_state_manager_node.py` の責務です。
- `autostart_orchestrator_node.py` は `vehicle_state_topic`（default: `/awsim/state`）と記録/初期化フローに集中します。

## 起動先（launch）

- AWSIM評価側（autostart込み）: `aichallenge_system_launch/launch/mode/awsim.launch.xml`
- AWSIM state-manager単体: `aichallenge_system_launch/launch/mode/awsim_state_manager.launch.xml`

## 設定ファイル

- `autostart_orchestrator.param.yaml`
- `awsim_state_manager.param.yaml`

## 補足

- Auto startノードは Autoware ドメイン側のワークフローを担当します。
- state managerノードは AWSIM 側（通常 `ROS_DOMAIN_ID=0`）の管理状態監視と終了処理を担当します。
- `autostart_orchestrator_node.py` の debug 可視化では `vehicle`（`ROS_DOMAIN_ID` 由来）と `vehicle_topic` を表示します。
- Auto startノードは終了時に `/output/latest` ディレクトリ配下の `d<id>/` に固定名シンボリックリンクを更新します。
- `ros2 bag record -o <name>` の標準命名により、最初のmcapは `<name>_0.mcap` になります（例: `rosbag2_autoware_0.mcap`）。
