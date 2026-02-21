# awsim_state_manager_node

## 目的

AWSIM が起動したことを監視し、必要なら `/admin/awsim/start` を publish して Sync モードの開始を補助します。  
加えて、AWSIM の終了（`/admin/awsim/state` 遷移やプロセス消失）を検知したら終了処理を実行します。

重要:
- `/admin/awsim/start` (`std_msgs/Bool`) の publish/subscribe は本ノードが唯一の責務です。
- `autostart_orchestrator_node.py` は本トピックを扱いません。

## 対応ノード

- ノード名: `awsim_state_manager`
- 実行ファイル: `autostart_orchestrator_py/awsim_state_manager_node.py`

## 購読 / publish

### 購読

- `admin_state_topic`（default: `/admin/awsim/state`）
  - 型: `std_msgs/String`
  - 正規化された観測値: `selectmode`, `playstart`, `ready`, `waitstart`, `start`, `lapcomplete`, `finish`, `finishall`, `terminate`

### publish

- `admin_start_topic`（default: `/admin/awsim/start`）
  - 型: `std_msgs/Bool`
  - `admin_start_trigger_state` に一致したら `True` を一度（既定）publish

## 終了トリガ

- `/admin/awsim/state` が `finish`, `finishall`, `finishedall`, `terminate`, `terminated` のいずれか
- 監視対象の AWSIM プロセスが消失
- ノード終了要求（`Ctrl-C`, destroy）時

## プロセス監視

- `awsim_kill_patterns` で列挙した文字列を `pgrep -f` で検索
- 停止時シーケンス:
  - `SIGINT`
  - `SIGTERM`
  - 最後に `SIGKILL`
- `shutdown_grace_sec`（SIGINT と SIGTERM の間隔）と `kill_wait_sec`（各シグナル後の待機）を使用

## ワークフロー状態

`_DEBUG_AWSIM_STATES`

1. `BOOT`
2. `WAIT_AWSIM`
3. `RUNNING`
4. `SHUTTING_DOWN`
5. `FINISHED`
6. `ERROR`

`enable_debug_visualization=true` 時は Qt パネルで上記状態を表示します（現状のウィンドウタイトル: `AWSIM State Monitor`）。

## launch / config

- `aichallenge_system_launch/launch/mode/awsim_state_manager.launch.xml`
  - `ROS_DOMAIN_ID=0` をセットして起動
  - `awsim_state_manager.param.yaml` を読む
- 既定パラメータ（`config/awsim_state_manager.param.yaml`）:
  - `awsim_kill_patterns`
  - `shutdown_grace_sec`
  - `kill_wait_sec`
  - `exit_on_finish`
  - `shutdown_on_exit`
  - `admin_state_topic`
  - `admin_start_topic`
  - `admin_start_trigger_state`
  - `admin_start_enabled`
  - `admin_start_once`
  - `enable_debug_visualization`

## 責務境界

- 本ノードは「AWSIM実行の監視・終了処理」を担当
- `/admin/awsim/start` の送信や AWSIM プロセス監視は本ノードに集約
- `autostart_orchestrator_node.py` は車両状態 (`/<vehicle_ns>/awsim/state`) と記録制御を担当
