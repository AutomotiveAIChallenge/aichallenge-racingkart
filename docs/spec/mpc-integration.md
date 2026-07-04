# multi_purpose_mpc_ros インテグレーション設計

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

作成日: 2026-02-10

## 概要

`multi_purpose_mpc_ros` は `aichallenge_submit` に統合済み。`reference.launch.xml` の `control_method` 引数で `mpc` / `pure_pursuit` / `tiny_lidar_net` / `pilot_net` / `joycon` を切り替えられる。デフォルトは `mpc`。

## 現在のアーキテクチャ

### ノード構成（Planning + Control）

```
reference.launch.xml (aichallenge_submit_launch)

  [Planning]
  ┌─────────────────────────────────┐
  │ simple_trajectory_generator     │
  │   CSV → Trajectory を 1Hz で Pub│
  │   出力: /planning/scenario_     │
  │         planning/trajectory     │
  └───────────┬─────────────────────┘
              │ Trajectory
              ▼
  [Control] (control_method == "pure_pursuit" の場合)
  ┌─────────────────────────────────┐
  │ simple_pure_pursuit (100Hz)     │
  │   入力:                         │
  │     /localization/kinematic_    │
  │       state (Odometry)          │
  │     /planning/scenario_         │
  │       planning/trajectory       │
  │   出力:                         │
  │     /control/command/control_cmd│
  │     (AckermannControlCommand)   │
  └─────────────────────────────────┘
```

### simple_pure_pursuit のパラメータ

- `wheel_base`: 2.14m
- `lookahead_gain`: 0.5
- `lookahead_min_distance`: 3.5m
- `speed_proportional_gain`: 1.0
- `steering_tire_angle_gain`: 1.50（sim）/ 1.639（実機）
- `use_external_target_vel`: false

### simple_trajectory_generator の役割

- CSV ファイルからウェイポイント（x, y, z, orientation, velocity）を読み込み
- `Trajectory` メッセージとして 1Hz で Publish
- ネームスペース `planning/scenario_planning` 配下で起動

## MPC コントローラの特徴

### トピック構成

**入力:**
| トピック名 | 型 | 備考 |
|-----------|-----|------|
| `/localization/kinematic_state` | Odometry | **既存と同一** |
| `planning/scenario_planning/trajectory` | Trajectory | 相対パス。`update_by_topic: true` 時のみ使用 |
| `control/control_mode_request_topic` | Bool | 制御有効/無効 |
| `/control/mpc/stop_request` | Empty | 停止要求 |

**出力:**
| トピック名 | 型 | 備考 |
|-----------|-----|------|
| `/control/command/control_cmd` | AckermannControlCommand | **既存と同一** |
| `/control/command/control_cmd_raw` | AckermannControlCommand | ゲイン適用前 |
| `/mpc/prediction` | MarkerArray | 予測軌跡（可視化） |
| `/mpc/ref_path` | MarkerArray | 参照パス（可視化） |

### 経路参照の方式

MPC コントローラは参照パスの取得方法を2種類持つ。

1. **CSV ファイルから直接読み込み**（`reference_path.update_by_topic: false`、デフォルト）
   - `config.yaml` の `reference_path.csv_path` で指定
   - 独自の occupancy grid map と最適化済み経路ファイルを使用
   - `simple_trajectory_generator` は不要

2. **Trajectory トピック経由**（`reference_path.update_by_topic: true`）
   - `simple_trajectory_generator` と同じ経路を動的に受け取り、内部で参照パスを再構成する

## 統合方針

### アーキテクチャ: control_method による切り替え

`control_method` launch 引数で `mpc` / `pure_pursuit` / `tiny_lidar_net` / `pilot_net` / `joycon` を選択できる。デフォルトは `mpc`。各コントローラは `<group if="...">` 内の `<include ... control/<name>.launch.xml>` で起動する（インライン `<node>` ではない）。

MPC コントローラは独自の参照パスと occupancy grid map を持ち、これが経路幅制限・速度プロファイルの制約計算に不可欠なため、MPC モードでは `simple_trajectory_generator` の軌跡入力を使用しない（`update_by_topic: false`）。

```
統合後:

  [Planning]
  ┌──────────────────────────────────┐
  │ simple_trajectory_generator      │ ← そのまま残す
  │   出力: /planning/scenario_      │    （pure_pursuit モードで使用）
  │         planning/trajectory      │
  └──────────────────────────────────┘

  [Control] (control_method == "mpc" の場合) ← デフォルト
  ┌──────────────────────────────────┐
  │ <include control/mpc.launch.xml> │
  │   mpc_controller (40Hz)          │
  │   独自CSV参照パス + occupancy map│
  │   入力:                          │
  │     /localization/kinematic_     │
  │       state (Odometry)           │
  │   出力:                          │
  │     /control/command/control_cmd │
  │     /mpc/prediction (可視化)     │
  │     /mpc/ref_path   (可視化)     │
  └──────────────────────────────────┘

  [Control] (control_method == "pure_pursuit" の場合)
  ┌──────────────────────────────────┐
  │ simple_pure_pursuit (100Hz)      │ ← 従来どおり残す
  │   入力:                          │
  │     /localization/kinematic_state│
  │     /planning/scenario_planning/ │
  │       trajectory                 │
  │   出力:                          │
  │     /control/command/control_cmd │
  └──────────────────────────────────┘

  [Control] (control_method == "tiny_lidar_net" / "pilot_net" の場合)
  ┌──────────────────────────────────┐
  │ tiny_lidar_net_controller /      │ ← LiDAR / カメラから直接制御
  │ pilot_net_controller             │
  └──────────────────────────────────┘
```

### control_method 一覧

| 値 | コントローラ | 経路ソース | 用途 |
|----|------------|-----------|------|
| `mpc` (デフォルト) | `mpc_controller` | MPC 独自 CSV | 本番走行・タイムアタック |
| `pure_pursuit` | `simple_pure_pursuit` | `simple_trajectory_generator` | デバッグ・比較検証 |
| `tiny_lidar_net` | `tiny_lidar_net_controller` | LiDAR 直接 | E2E 学習ベース走行 |
| `pilot_net` | `pilot_net_controller` | — | パイロットネット走行 |
| `joycon` | joystick teleop | — | 手動操作 |

### 各ノードの扱い

| ノード | 変更 |
|--------|------|
| `simple_pure_pursuit` | **残す**（`control_method=pure_pursuit` で使用） |
| `simple_trajectory_generator` | **残す**（`pure_pursuit` モードで使用） |
| `mpc_controller` | **新規追加**（`control_method=mpc` で使用、デフォルト） |

## 実装済み状態の確認

### パッケージ配置

`multi_purpose_mpc_ros` と `multi_purpose_mpc_ros_msgs` はすでに `aichallenge_submit/` 配下に存在する。手動でのクローンや移動は不要。

```
aichallenge/workspace/src/aichallenge_submit/
├── aichallenge_submit_launch/
├── simple_pure_pursuit/
├── simple_trajectory_generator/
├── tiny_lidar_net_controller/
├── multi_purpose_mpc_ros/          # ← 統合済み
├── multi_purpose_mpc_ros_msgs/     # ← 統合済み
└── ...（その他既存パッケージ）
```

### reference.launch.xml の現状

`aichallenge/workspace/src/aichallenge_submit/aichallenge_submit_launch/launch/reference.launch.xml`

- `control_method` arg（L20）: デフォルト `mpc`
- MPC コントローラは `<include file="$(find-pkg-share aichallenge_submit_launch)/launch/control/mpc.launch.xml">` で起動（インライン `<node>` ではなく専用 launch ファイル経由）
- `pure_pursuit` / `tiny_lidar_net` / `pilot_net` / `joycon` も同様に各 `control/<name>.launch.xml` を include する構造

### ビルド

```bash
# Docker コンテナ内でビルド
make autoware-build
```

ビルドで行われること:
1. `multi_purpose_mpc_ros_msgs` のメッセージ型生成（`AckermannControlBoostCommand.msg`, `PathConstraints.msg`, `BorderCells.msg`）
2. `multi_purpose_mpc_ros` のビルド:
   - C++ ライブラリ/ノード（`boost_commander`）のビルド
   - Python venv の作成（`/usr/bin/python3 -m venv`）
   - `requirements.txt` からの pip install（`numpy`, `pandas`, `matplotlib`, `osqp`, `scikit-image`, `PyYAML`）
   - スクリプトとデータの install

**ビルド依存関係の順序:**
```
autoware_auto_control_msgs（Autoware underlay に存在）
  → multi_purpose_mpc_ros_msgs
    → multi_purpose_mpc_ros
```

colcon が自動解決するため、特別な指定は不要。

### config.yaml の確認・調整

MPC の config ファイル: `multi_purpose_mpc_ros/config/config.yaml`

| 設定項目 | 現在の値 | 確認事項 |
|---------|---------|---------|
| `map.yaml_path` | `env/final_ver3/occupancy_grid_map.yaml` | 占有格子地図が存在するか |
| `reference_path.csv_path` | `env/final_ver3/traj_mincurv.csv` | 最適化済み経路が存在するか |
| `reference_path.update_by_topic` | `false` | CSV 直接読み込みモード（推奨） |
| `mpc.steering_tire_angle_gain_var` | `1.639` | 実機値。sim では `1.50` が必要かも |
| `mpc.v_max` | `20.0` | 速度プリセット（中速）。環境に合わせて調整 |
| `obstacles.csv_path` | `""` | 空 = トピック購読モード（障害物回避が off なので影響なし） |

**コースが変更された場合**（例: 新しい lanelet2_map.osm が配布された場合）は、「事前準備」セクションの手順に従って OGM と経路を再生成する。

### 動作確認

#### MPC モードでの起動（デフォルト）

```bash
make dev
```

起動後の確認:

```bash
# MPC ノードが起動しているか
ros2 node list | grep mpc

# 制御指令が出力されているか
ros2 topic echo /control/command/control_cmd --once

# 予測軌跡が可視化できるか
ros2 topic echo /mpc/prediction --once

# 参照パスが可視化できるか
ros2 topic echo /mpc/ref_path --once
```

#### Pure Pursuit モードでの起動確認

```bash
# reference.launch.xml を呼んでいる箇所で control_method を変更するか、
# 直接 launch コマンドで
ros2 launch aichallenge_submit_launch reference.launch.xml control_method:=pure_pursuit simulation:=true use_sim_time:=true
```

Pure Pursuit が従来通り動作することを確認。

#### 走行品質の確認

| チェック項目 | 確認方法 |
|------------|---------|
| 参照パスに追従しているか | RViz で `/mpc/ref_path` と実際の走行軌跡を比較 |
| 40Hz の制御レートで安定しているか | `ros2 topic hz /control/command/control_cmd` |
| 制御指令値が妥当か | `ros2 topic echo /control/command/control_cmd` でステア角・加速度を確認 |
| occupancy grid map が正しく読めているか | ノード起動ログでエラーがないか |

### 提出ファイルの確認

```bash
bash create_submit_file.bash
tar tf submit/aichallenge_submit.tar.gz | grep multi_purpose_mpc
```

以下のエントリが含まれていれば OK:
```
aichallenge_submit/multi_purpose_mpc_ros/
aichallenge_submit/multi_purpose_mpc_ros_msgs/
```

### 変更ファイルまとめ

| ファイル | 内容 |
|---------|------|
| `aichallenge_submit/multi_purpose_mpc_ros/` | 統合済み（in-tree） |
| `aichallenge_submit/multi_purpose_mpc_ros_msgs/` | 統合済み（in-tree） |
| `reference.launch.xml` | `control_method` 引数（デフォルト `mpc`）、各コントローラを `<include control/<name>.launch.xml>` で起動 |

### 将来の改善項目（今回はスコープ外）

- sim/実機での `steering_tire_angle_gain_var` 切り替え（config 分離 or launch param override）
- 速度プリセットの launch arg 化
- 障害物回避の有効化（`use_obstacle_avoidance=true`）
- `boost_commander` ノードの統合（`use_boost_acceleration=true` 時）
- `path_constraints_provider` ノードの統合（高度な障害物回避）

## 事前準備: MPC 用地図・経路データの生成

MPC コントローラはノード起動時にファイルを読み込むだけで、実行時に計算は行わない。**コースが変わった場合はこの手順で再生成が必要**。

現在は `env/final_ver3/` に計算済みのファイルが格納されており、同じコースであればそのまま使える。

### データ生成フロー

```
lanelet2_map.osm（コース地図）
    │
    ▼  Step 1: lanelet2_to_ogm
occupancy_grid_map.pgm + .yaml（占有格子地図）
    │
    ▼  Step 2: global_racetrajectory_optimization
traj_mincurv.csv（最適化済み経路）
```

### Step 1: Occupancy Grid Map の生成

lanelet2 形式の地図（`.osm`）から占有格子地図を生成する。

**ツール**: https://github.com/Roborovsky-Racers/lanelet2_to_ogm

**参考**: https://roborovsky-racers.github.io/RoborovskyNote/AutomotiveAIChallenge/2024/lanelet2_to_ogm.html

```bash
git clone https://github.com/Roborovsky-Racers/lanelet2_to_ogm.git
cd lanelet2_to_ogm

# lanelet2_map.osm を lanelet2/map/ に配置（デフォルトで AIC2024 マップが同梱）
make
```

**出力:**
- `occupancy_grid_map.pgm` — コースの壁・境界を表現した画像ファイル
- `occupancy_grid_map.yaml` — 解像度・原点座標の定義

### Step 2: 最適化済み経路の生成

occupancy grid map 上で最適走行ラインを計算する。

**ツール**: https://github.com/TUMFTM/global_racetrajectory_optimization

最適化基準を選択して経路を生成する。`env/preliminary/` に3種類の結果が残っている：

| ファイル | 最適化基準 |
|---------|-----------|
| `optimized_traj_mincurv.csv` | 最小曲率（カーブが緩やかなライン） |
| `optimized_traj_shortest.csv` | 最短距離 |
| `optimized_traj_mintime.csv` | 最小時間（最速ライン） |

**出力フォーマット** (`traj_mincurv.csv`):
```
s_m, x_m, y_m, psi_rad, kappa_radpm, vx_mps, ax_mps2
（距離, x座標, y座標, ヨー角, 曲率, 速度, 加速度）
```

### Step 3: env/ ディレクトリへの配置

生成したファイルを `multi_purpose_mpc_ros/env/<バージョン名>/` に配置し、`config.yaml` のパスを更新する。

```yaml
# config.yaml
map:
  yaml_path: "env/<バージョン名>/occupancy_grid_map.yaml"

reference_path:
  csv_path: "env/<バージョン名>/traj_mincurv.csv"
```

### 現在の env/ ディレクトリ構成

```
env/
├── preliminary/     # 初期版（3種類の最適化軌跡あり）
├── final/           # 決勝版 v1
├── final_ver2/      # 決勝版 v2
├── final_ver3/      # 決勝版 v3 ← 現在 config.yaml で参照中
├── final_ver4/      # 決勝版 v4
├── official/        # 公式版（軌跡なし、地図のみ）
└── others/          # 補助データ（ウェイポイント、障害物 CSV 等）
```

### ウェイポイント作成補助ツール

`env/create_waypoints.py` を使うと、occupancy grid map を GUI で表示しマウスクリックでウェイポイントを打てる。軌跡最適化ツールの入力用。

```bash
cd multi_purpose_mpc_ros/env/<バージョン名>   # 例: final_ver3（occupancy_grid_map.yaml を含む版を選ぶ）
python3 ../create_waypoints.py               # 要: matplotlib, pyyaml
```

## 注意事項

### トピック互換性

| 観点 | 互換性 | 備考 |
|------|--------|------|
| 入力: Odometry | **完全一致** | `/localization/kinematic_state` |
| 出力: 制御指令 | **完全一致** | `/control/command/control_cmd` |
| 出力: 制御指令（raw）| **完全一致** | `/control/command/control_cmd_raw` |
| Planning → Control | **不要** | MPC は独自 CSV を使う（`update_by_topic: false`） |

トピックインタフェースの互換性は高く、**リマップは不要**。

### 制御周期の差異

- simple_pure_pursuit: **100Hz**（`create_wall_timer(10ms)`）
- mpc_controller: **40Hz**（`config.yaml` の `control_rate: 40.0`）

MPC は計算負荷が高いため 40Hz は妥当。問題があれば `control_rate` を調整できる。

### Python venv

MPC パッケージは CMakeLists.txt 内で Python 仮想環境を作成する（`execute_process` で `/usr/bin/python3 -m venv` + `pip install`）。Docker ビルド内で完結するため追加設定は不要だが、ビルド時間が増加する点に注意。

### 障害物回避

MPC コントローラは障害物回避機能を内蔵しているが、**今回の統合ではデフォルトで無効**（`use_obstacle_avoidance=false`）。将来的に必要になったら有効化できる。

**障害物情報の取得方法は2つ:**

1. **CSV ファイルから静的障害物を読み込み**
   ```yaml
   # config.yaml
   obstacles:
     csv_path: "maps/occupancy_grid_map_obstacles.csv"  # 空でなければCSVモード
     radius: 1.25  # 障害物の半径 [m]
   ```
   - `create_waypoints.py --obs` で GUI 上から障害物座標を手動で作成
   - `ObstacleManager` が occupancy grid map に障害物を追加し、MPC の制約計算で回避

2. **トピック経由で動的障害物を受け取り**
   ```yaml
   # config.yaml
   obstacles:
     csv_path: ""  # 空にするとトピック購読モード
     radius: 1.25
   ```
   - `/aichallenge/objects`（`Float64MultiArray`）から障害物座標を受け取る
   - データフォーマット: `[x, y, ?, ?, x, y, ?, ?, ...]`（4要素ずつ、x/y を使用）
   - 更新のたびに occupancy grid map をリセット → 障害物再配置 → 経路制約を再計算

**さらに高度な回避（オプション）:**

`path_constraints_provider` ノードを別途起動すると、障害物を考慮した経路の上下限制約（`PathConstraints`, `BorderCells`）を MPC に提供できる。

```yaml
# config.yaml
reference_path:
  use_path_constraints_topic: true   # PathConstraints トピックを購読
  use_border_cells_topic: true       # BorderCells トピックを購読
```

**有効化する場合の launch 変更:**
```xml
<param name="use_obstacle_avoidance" value="true"/>
```

### 提出ファイルへの影響

`create_submit_file.bash` で `aichallenge_submit` 以下を tar.gz にまとめるため、`multi_purpose_mpc_ros` と `multi_purpose_mpc_ros_msgs` が `aichallenge_submit/` 配下にある必要がある。

## まとめ

| 項目 | 内容 |
|------|------|
| 統合方式 | `control_method` launch 引数で切り替え |
| デフォルト | `mpc`（MPC コントローラ） |
| 切り替え | `pure_pursuit` / `tiny_lidar_net` / `pilot_net` / `joycon` |
| トピック互換 | 入出力ともに一致、リマップ不要 |
| 経路参照 | MPC: 独自 CSV / Pure Pursuit: `simple_trajectory_generator` |
| MPC 起動方式 | `<include control/mpc.launch.xml>` 経由（インライン node ではない） |
| パッケージ配置 | `aichallenge_submit/` 配下に統合済み（追加作業不要） |
| ビルド注意 | Python venv 作成（pip install）によるビルド時間増加 |
