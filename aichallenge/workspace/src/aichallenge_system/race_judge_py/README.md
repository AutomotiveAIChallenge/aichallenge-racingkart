# race_judge_py

## 概要

AWSIMシミュレーター内部で行っていた順位判定・ラップ計測・車両間衝突判定・コース壁接触判定を、実機レーシングカートのROS 2環境（RTK GNSS自己位置 + V2X他車位置 + IMU）で再現するパッケージ。AWSIM互換トピックを出力することで既存の autostart_orchestrator を無改修で再利用する。

## アーキテクチャ

```
[各カート車両ドメイン]
  RTK GNSS (localization/kinematic_state)
  V2X 他車位置 (v2x/vehicle_positions)
  IMU (sensing/imu/imu_data)
        │
        ▼
  vehicle_judge_node
  ├─ ラップ計測（センターライン弧長進捗）
  ├─ 壁/コース逸脱判定（lanelet2 footprint）
  └─ 衝突判定（V2X 距離 + IMU 確証）
        │
        ├─ /awsim/state, /awsim/status   (autostart_orchestrator 互換)
        ├─ /judge/lap, /judge/penalty_*
        └─ /vehicle/emergency/is_route_deviation

[地上局（1箇所）]
  V2X 全車位置 (v2x/vehicle_positions)
  管理者スタート信号 (admin/awsim/start)
        │
        ▼
  race_director_node
  ├─ 全車totalProgress から順位計算（1秒 persistence）
  ├─ レースフェーズ制御（selectmode→waitstart→ready→start→finish→finishall）
  └─ result-summary.json 出力
        │
        ├─ /admin/awsim/state   (全車両へ配信)
        └─ /judge/ranking       (JSON)
```

## ROS 2 インターフェース

### vehicle_judge_node

#### Subscribe

| トピック | 型 | 説明 |
|---|---|---|
| `/localization/kinematic_state` | `nav_msgs/Odometry` | RTK GNSS 自己位置・姿勢 |
| `/v2x/vehicle_positions` | `v2x_msgs/V2XVehiclePositionArray` | V2X 他車位置 |
| `/sensing/imu/imu_data` | `sensor_msgs/Imu` | IMU 加速度（衝突確証用） |
| `/admin/awsim/state` | `std_msgs/String` | レースフェーズ（director から） |

#### Publish

| トピック | 型 | 説明 |
|---|---|---|
| `/awsim/state` | `std_msgs/String` | AWSIM 互換フェーズ（orchestrator 向け） |
| `/awsim/status` | `std_msgs/Float32MultiArray` | `[sessionTime, lapCount, lapTime, section, timeScale, boost, boostActive]` |
| `/judge/lap` | `std_msgs/Float32MultiArray` | `[lap_count, progress01, last_lap_time, total_progress]` |
| `/judge/penalty_events` | `std_msgs/String` | ペナルティイベント JSON 配列 |
| `/judge/penalty_active` | `std_msgs/Bool` | 現在ペナルティ中フラグ |
| `/vehicle/emergency/is_route_deviation` | `std_msgs/Bool` | コース逸脱フラグ |

### race_director_node

#### Subscribe

| トピック | 型 | 説明 |
|---|---|---|
| `/v2x/vehicle_positions` | `v2x_msgs/V2XVehiclePositionArray` | 全車 V2X 位置・進捗 |
| `/admin/awsim/start` | `std_msgs/Bool` | 管理者スタート信号 |

#### Publish

| トピック | 型 | 説明 |
|---|---|---|
| `/admin/awsim/state` | `std_msgs/String` | レースフェーズ（全車へ配信） |
| `/judge/ranking` | `std_msgs/String` | 順位 JSON（totalProgress 降順） |

## 判定仕様

### ラップ計測

センターライン弧長進捗（0.0〜1.0）の wrap 検出により計測。ゴール通過判定は 0.70/0.30 ヒステリシスを設けてノイズを抑制し、実際の通過時刻を線形補間で求める。後退横断はデットにより相殺され、意図的な周回のみカウントされる。

### 順位判定

`totalProgress = lap数 + progress01` の降順、同値時は車番昇順でタイブレーク。1秒間の persistence フィルタにより GNSS ノイズによる順位フリッカを抑止。race_director_node が V2X データから独立して計算する。

### 壁/コース逸脱

車体 footprint（ yaw 連動、アンテナオフセット補正）の4角点が lanelet2 ポリゴン外に出た時点で逸脱と判定。2秒クールダウンにより連続検出を抑制。`route_safety_monitor` の実機発展形。

### 衝突判定

V2X 他車位置との中心間距離 < `collision_distance_m`（既定 1.5 m）かつ相手が自車前方半平面（追突側）に居る場合に crash ペナルティを記録。`use_imu_confirmation: true` の場合は IMU 衝撃スパイク（±0.2 s、`|a| > imu_impact_threshold`）を確証として要求する。

## パラメータ（config/race_judge.param.yaml）

| パラメータ | 既定値 | 説明 |
|---|---|---|
| `vehicle_id` | `d1` | 車両 ID（d1〜d4） |
| `output_dir` | `/output/latest` | 結果ファイル出力先 |
| `map_path` | `""` | lanelet2 地図ファイルパス |
| `total_laps` | `3` | レース周回数 |
| `lap_hysteresis_high` | `0.70` | ゴール通過検出上限閾値 |
| `lap_hysteresis_low` | `0.30` | ゴール通過検出下限閾値 |
| `wall_cooldown_sec` | `2.0` | 壁接触判定クールダウン（AWSIM 由来定数） |
| `position_clamp_speed_mps` | `5.0` | GNSS 位置跳び抑制クランプ速度（AWSIM 由来定数） |
| `collision_distance_m` | `1.5` | 衝突判定距離閾値（**実車キャリブレーション対象**） |
| `use_imu_confirmation` | `true` | 衝突の IMU 確証要否 |
| `imu_impact_threshold` | `1.5` | 衝突 IMU 閾値 [g]（**実車キャリブレーション対象**） |
| `imu_confirmation_window_sec` | `0.2` | IMU 確証探索ウィンドウ [s] |
| `pose_covariance_gate` | `0.5` | GNSS 品質ゲート（covariance trace 上限） |
| `ranking_persistence_sec` | `1.0` | 順位変動 persistence [s]（AWSIM 由来定数） |
| `publish_awsim_compat` | `true` | AWSIM 互換トピック出力の有無 |

AWSIM 由来定数（`wall_cooldown_sec`, `position_clamp_speed_mps`, `ranking_persistence_sec`）は AWSIM 側の実装に合わせた固定値。実車キャリブレーション対象（`collision_distance_m`, `imu_impact_threshold`, 地図境界ズレ）は実走行データで調整すること。

## 結果ファイル

| ファイル | 出力ノード | スキーマ |
|---|---|---|
| `d{N}-result-details.json` | `vehicle_judge_node`（各車） | v3：ラップ時刻・ペナルティ履歴 |
| `result-summary.json` | `race_director_node` | v2：全車最終順位・総周回 |

いずれも AWSIM 出力とスキーマ完全互換。既存の評価集計パイプライン（Lambda `result_update`）をそのまま利用できる。

## 運用要件

- **車両間時刻同期**: chrony / NTP 推奨。衝突の同時性判定および同着タイムの信頼性が依存する。
- **RTK FIX 前提**: FLOAT 劣化時は壁際マルチパスによる誤検出リスクがある。`pose_covariance_gate` パラメータで品質ゲートを設定すること。
- **V2X 供給**: `/v2x/vehicle_positions` は本パッケージのスコープ外。実機レースでの V2X インフラが前提。

## ペナルティ執行方針

安全上の観点から、自動速度クランプは実装しない（記録のみ）。`/judge/penalty_active` を参加者側の制御ノードが subscribe し、自主減速する設計とする。ペナルティ内容は `/judge/penalty_events` に JSON 形式で配信される。

## シミュレーションでのシャドー検証

`publish_awsim_compat:=false` でパラメータを設定し、AWSIM シミュレーション環境と本パッケージを並走させる。`/judge/*` 出力を AWSIM 内部判定結果と突き合わせて較正する手順:

```bash
# AWSIM と並走（compat 出力を無効化してトピック衝突を避ける）
ros2 launch race_judge_py race_judge.launch.xml \
  vehicle_id:=d1 \
  publish_awsim_compat:=false

# /judge/lap, /judge/ranking を AWSIM 側の /awsim/status と比較
ros2 topic echo /judge/lap
ros2 topic echo /judge/ranking
```

## 起動方法

```bash
# 実機モード（通常運用）
# aichallenge_system.launch.xml の simulation:=false 経由で
# real.launch.xml → race_judge.launch.xml が自動起動する
ros2 launch aichallenge_system_launch aichallenge_system.launch.xml simulation:=false

# 地上局（集計・順位配信）
ros2 launch race_judge_py race_director.launch.xml output_dir:=/output/latest

# 車両ノード単体起動
ros2 launch race_judge_py race_judge.launch.xml vehicle_id:=d1

# 車両 ID を変えて起動（並列評価 d1〜d4）
ros2 launch race_judge_py race_judge.launch.xml vehicle_id:=d2 output_dir:=/output/20260613-120000
```
