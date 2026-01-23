# aichallenge_control_rviz_plugin

RViz2 panel plugin for the AI Challenge system.

## 日本語

AI Challenge システム向けの RViz2 パネルプラグインです。

### パネル: `aichallenge_control_rviz_plugin/ControlModePanel`

- `Auto Mode Start` / `Auto Mode Stop`: 自動運転モード開始/停止リクエストとして `std_msgs/Bool` を publish します
- `Initial Pose Set`: GNSS の位置と trajectory の進行方向から `/initialpose` を publish します
- 上記 `Initial Pose Set` は **ボタン操作**に加えて、下記 **サービス呼び出し**でも実行できます

### 初期姿勢セット（サービス）

- サービス名: `/set_initial_pose`
- 型: `std_srvs/srv/Trigger`
- 呼び出し例:
  - `ros2 service call /aichallenge/control_mode_panel/set_initial_pose std_srvs/srv/Trigger {}`

### 初期姿勢セット仕様（ボタン/サービス共通）

#### 入力

- GNSS pose（位置 + covariance）
  - Topic: `/sensing/gnss/pose_with_covariance`
  - Type: `geometry_msgs/msg/PoseWithCovarianceStamped`
- Trajectory
  - Topic: `/planning/scenario_planning/trajectory`
  - Type: `autoware_auto_planning_msgs/msg/Trajectory`

#### 出力

- Initial pose
  - Topic: `/initialpose`
  - Type: `geometry_msgs/msg/PoseWithCovarianceStamped`
  - 補足: `/initialpose` は `aichallenge_system_launch` により `/localization/initial_pose3d` に中継されます

#### 動作

1. 最新の GNSS pose をベースにします
2. GNSS 位置（x-y の2D距離）に最も近い trajectory point を探します
3. **隣接点**から yaw を計算します
   - 基本は前方向の `p[i] -> p[i+1]`
   - 前方向のセグメントが退化（長さ0）している/終端点の場合は `p[i-1] -> p[i]`
   - 必要なら前後にスキャンして長さ0でないセグメントを探します
4. 計算した yaw から quaternion を作り、`pose.pose.orientation` を上書きします
5. GNSS covariance は維持しつつ、yaw の covariance のみ上書きします
   - `covariance[35] = 0.5`（6x6 covariance の `rot_z` 分散）
6. `/initialpose` に publish します

#### frame の扱い

- GNSS と trajectory で `header.frame_id` が異なる場合でも **TF変換は行わず**に処理を続行します

#### 失敗時

- GNSS/trajectory を未受信、trajectory の有効点/セグメントが不足、yaw が計算できない場合は `/initialpose` を publish しません
  - パネルのステータス表示と WARN ログを出します

## Panels

### `aichallenge_control_rviz_plugin/ControlModePanel`

Provides buttons for:

- `Auto Mode Start` / `Auto Mode Stop`: publish `std_msgs/Bool` to request autonomous control mode.
- `Initial Pose Set`: publish `/initialpose` using GNSS position and trajectory heading.
  - Same behavior is also available via service: `/set_initial_pose` (`std_srvs/srv/Trigger`).

## Initial Pose Set specification

### Inputs

- GNSS pose (position + covariance)
  - Topic: `/sensing/gnss/pose_with_covariance`
  - Type: `geometry_msgs/msg/PoseWithCovarianceStamped`
- Trajectory
  - Topic: `/planning/scenario_planning/trajectory`
  - Type: `autoware_auto_planning_msgs/msg/Trajectory`

### Output

- Initial pose
  - Topic: `/initialpose`
  - Type: `geometry_msgs/msg/PoseWithCovarianceStamped`
  - Note: `/initialpose` is relayed to `/localization/initial_pose3d` by `aichallenge_system_launch`.

### Behavior (button click / service call)

1. Use the latest received GNSS pose as the base.
2. Find the closest trajectory point to the GNSS position (2D distance on x-y).
3. Compute yaw from **adjacent** trajectory points:
   - Prefer the forward segment `p[i] -> p[i+1]`.
   - If the forward segment is degenerate (zero length) or `i` is the last point, use the backward segment `p[i-1] -> p[i]`.
   - If necessary, scan forward/backward until a non-zero segment is found.
4. Create an orientation quaternion from the computed yaw and overwrite `pose.pose.orientation`.
5. Keep GNSS covariance as-is, but overwrite yaw covariance:
   - `covariance[35] = 0.5` (i.e., `rot_z` variance in the 6x6 covariance matrix).
6. Publish to `/initialpose`.

### Frame handling

- If `header.frame_id` differs between GNSS and trajectory, the panel **still proceeds** (no TF transform is performed).

### Failure handling

- If GNSS or trajectory has not been received, or trajectory has fewer than 2 valid points/segments:
  - Do not publish `/initialpose`.
  - Show a status message in the panel and log a warning.
