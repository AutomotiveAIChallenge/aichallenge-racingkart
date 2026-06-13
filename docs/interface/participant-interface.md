# 参加者インターフェース契約

> 参加者（提出者）が**変えてはいけない約束**を列挙します。各項目は「**約束**: …／**破ると**: …」の形式で示します。評価システムが依存する安定面（契約）の集約であり、手順書ではありません。

関連ドキュメント:

- 評価システム側契約（FSM・結果 JSON など）: [evaluation-interface.md](evaluation-interface.md)
- Compose オーバーレイ設計: [../design/specs/compose-overlays.md](../design/specs/compose-overlays.md)
- MPC 統合仕様: [../design/specs/mpc-integration.md](../design/specs/mpc-integration.md)
- リポジトリルート README: [../../README.md](../../README.md)
- ドキュメント命名・分類規約: [../README.md](../README.md)

---

## 1. 概要

評価環境は `docker_build.sh eval --submit <tar>` で封入イメージを生成し、そのイメージ内で `evaluation.launch.xml` を起動します。提出する tar.gz はイメージのビルドとランタイムに直接影響するため、以下の契約を守ることが評価の前提条件です。

契約は 4 つの軸に分かれます。

1. **提出パッケージの形式**（tar.gz レイアウト）
2. **制御方式の選択**（`control_method` 引数）
3. **ROS 2 トピック I/O**（subscribe/publish すべきトピック）
4. **ビルド・実行環境の前提**（eval イメージで固定・差し替えられる事項）

---

## 2. 提出パッケージの約束

### 約束 1: tar.gz の最上位ディレクトリは `aichallenge_submit/`

**約束**: `create_submit_file.bash` は次のコマンドで tar を生成します。

```bash
tar zcvf submit/aichallenge_submit.tar.gz -C ./aichallenge/workspace/src aichallenge_submit
```

tar 内の最上位エントリは `aichallenge_submit/` のみです。独自に tar を生成する場合も、内側の最上位ディレクトリ名は **必ず `aichallenge_submit/`** にしてください。

**破ると**: eval イメージの `Dockerfile` eval ステージは `/aichallenge/workspace/src/aichallenge_submit` を `rm -rf` したうえで `tar zxf /tmp/s.tgz -C /aichallenge/workspace/src` で展開します。ディレクトリ名が異なると展開後に `aichallenge_submit/` が空のままになり、参加者パッケージが一切ビルドされません。

### 約束 2: tar.gz はリポジトリ直下（Docker ビルドコンテキスト内）に置く

**約束**: `Dockerfile` eval ステージは `COPY ${SUBMIT_TAR} /tmp/s.tgz` でビルドコンテキスト（リポジトリルート）を基準にパスを解決します。既定パスは `submit/aichallenge_submit.tar.gz`（`ARG SUBMIT_TAR=submit/aichallenge_submit.tar.gz`）。別パスを指定する場合は `--submit <リポジトリ直下の相対パス>` を使います。

**破ると**: リポジトリルート外の tar を指定すると `docker build` の `COPY` が解決できず、eval イメージのビルドが失敗します。

### 約束 3: エントリ launch ファイルは `aichallenge_submit_launch` パッケージが提供する

**約束**: `aichallenge_system.launch.xml` は `aichallenge_submit_launch` パッケージの `aichallenge_submit.launch.xml` を固定で `<include>` します。この launch ファイルは `reference.launch.xml` に委譲し、センシング・自己位置・プランニング・制御の全スタックを起動します。

```
aichallenge_submit.launch.xml
└── reference.launch.xml    ← 参加者が変更してよいエントリ
    ├── sensing（imu_corrector, racing_kart_gnss_poser, gyro_odometer）
    ├── localization（imu_gnss_poser, ekf_localizer, twist2accel）
    ├── planning（simple_trajectory_generator）
    ├── control（control_method で選択）
    └── map（lanelet2_map_loader）
```

**破ると**: `aichallenge_submit_launch` パッケージを提出物に含めない、または `aichallenge_submit.launch.xml` を削除すると、評価の launch ツリーが起動できません。

---

## 3. 制御方式の約束

### 約束 4: `control_method` は 5 値、既定は `mpc`

**約束**: `reference.launch.xml` は以下の引数を持ちます。

```xml
<arg name="control_method" default="mpc"
     description="Select control: mpc, pure_pursuit, tiny_lidar_net, pilot_net, joycon"/>
```

各値が起動するノードと消費するセンサ:

| `control_method` | 起動するノード（パッケージ） | 主な入力トピック |
|---|---|---|
| `mpc`（既定） | `multi_purpose_mpc_ros`（Python） | `/localization/kinematic_state`、`/planning/scenario_planning/trajectory` |
| `pure_pursuit` | `simple_pure_pursuit`（C++） | `/localization/kinematic_state`、`/planning/scenario_planning/trajectory` |
| `tiny_lidar_net` | `tiny_lidar_net_controller`（Python） | `/scan`（`sensor_msgs/LaserScan`） |
| `pilot_net` | `pilot_net_controller`（Python） | `/image_raw`（`sensor_msgs/Image`） |
| `joycon` | `teleop_manager`（`teleop_manager` パッケージ） | （手動制御） |

各値は `control/<name>.launch.xml` を `<include>` する `<group if=...>` で実装されており、いずれも `/control/command/control_cmd`（`autoware_auto_control_msgs/AckermannControlCommand`）を publish します。

**破ると**: 上記 5 値以外を渡すと、どの `<group if=...>` にも一致せず制御ノードが起動せず車両が動きません。既定値 `mpc` を変更すると、`control_method` を明示しない既存の起動経路の挙動が変わります。

---

## 4. トピック I/O 契約

評価可能な提出物が守るべき ROS 2 トピック契約です。型・方向はソースで確認済みです。AWSIM 側の publisher 一覧は本リポジトリ外（Unity プロジェクト）のため（要確認）と明記します。

> **アーキテクチャ補足**: `aichallenge_awsim_adapter` の launch（`aichallenge_awsim_adapter.launch.xml`）は現状**全行がコメントアウト**されており、`actuation_cmd_converter` ノードは起動しません。AWSIM は Autoware 標準のトピック名を直接 publish/subscribe します。

### (A) AWSIM → Autoware（参加者が subscribe してよい入力）

AWSIM が publish し参加者ノードが subscribe するトピックです（Autoware 側の購読実装から確認済み。AWSIM 側の正確な publisher 名は要確認）。

| トピック | 型 | 確認元 |
|---|---|---|
| `/sensing/imu/imu_raw` | `sensor_msgs/Imu` | `reference.launch.xml`（imu_corrector 入力） |
| `/sensing/gnss/nav_sat_fix` | `sensor_msgs/NavSatFix` | `reference.launch.xml`（racing_kart_gnss_poser 入力） |
| `/vehicle/status/velocity_status` | `autoware_auto_vehicle_msgs/VelocityReport` | `reference.launch.xml`（vehicle_velocity_converter 入力） |
| `/vehicle/status/steering_status` | `autoware_auto_vehicle_msgs/SteeringReport` | `reference.launch.xml`（raw_vehicle_cmd_converter 入力、実車経路のみ） |
| `/clock` | `rosgraph_msgs/Clock` | シミュレーション時間（`use_sim_time=true`） |

`tiny_lidar_net` 使用時の追加入力（要確認: AWSIM 側の `/scan` publisher 名は本リポジトリ外）:

| トピック | 型 | 確認元 |
|---|---|---|
| `/scan` | `sensor_msgs/LaserScan` | `tiny_lidar_net_controller_node.py` の `create_subscription` |

`pilot_net` 使用時の追加入力（要確認: AWSIM 側のカメラトピック名は本リポジトリ外）:

| トピック | 型 | 確認元 |
|---|---|---|
| `/image_raw` | `sensor_msgs/Image` | `pilot_net_controller_node.py` の `create_subscription` |

### (B) Autoware → AWSIM（参加者が publish すべき出力）

評価のために参加者パッケージが最終的に publish しなければならないトピックです。

| トピック | 型 | 確認元 |
|---|---|---|
| `/control/command/control_cmd` | `autoware_auto_control_msgs/AckermannControlCommand` | `pure_pursuit.launch.xml`（remap）、`mpc_controller.py`、`boost_commander.cpp`、`tiny_lidar_net_controller_node.py`、`pilot_net_controller_node.py` |

AWSIM はこのトピックを受けてカートを動かします。全制御方式（mpc / pure_pursuit / tiny_lidar_net / pilot_net）がこのトピックに収束します。

実車経路（`simulation=false`）のみ使用する追加出力:

| トピック | 型 | 用途 |
|---|---|---|
| `/control/command/actuation_cmd` | `tier4_vehicle_msgs/ActuationCommandStamped` | `raw_vehicle_cmd_converter` 経由で実車アクチュエータへ（シミュレーションでは未使用） |

### (C) 評価起動ハンドシェイク（参加者が提供すべきサービス）

| エンドポイント | 型 | 確認元 |
|---|---|---|
| `/set_initial_pose` | `std_srvs/srv/Trigger` | `imu_gnss_poser_node.cpp`（`initial_pose_service` パラメータで名前設定） |

オーケストレータ（`autostart_orchestrator_node`）は起動時にこのサービスを呼び出して初期自己位置を設定します。未提供の場合は `initial_pose_service_timeout_sec` 経過後にスキップされます。

### 約束 5: 評価可能な提出物が満たす最小インターフェース

**約束**: 提出パッケージは以下をすべて満たすこと。

1. AWSIM センサ入力を subscribe: `/sensing/imu/imu_raw`（Imu）、`/sensing/gnss/nav_sat_fix`（NavSatFix）、`/vehicle/status/velocity_status`（VelocityReport）
2. `/localization/kinematic_state`（`nav_msgs/Odometry`）を produce する（`ekf_localizer` が publish）
3. `/planning/scenario_planning/trajectory`（`autoware_auto_planning_msgs/Trajectory`）を produce する
4. `/control/command/control_cmd`（`autoware_auto_control_msgs/AckermannControlCommand`）を publish する
5. `/set_initial_pose`（`std_srvs/srv/Trigger`）を advertise する（`imu_gnss_poser` が実装）

**破ると**: いずれかのトピック名・型を変更すると、対応する localization / planning / control の連結が切れ、車両の起動・走行・評価ができなくなります。

---

## 5. ビルド・実行環境の前提

### eval イメージが行うこと（固定事項）

`Dockerfile` eval ステージは以下を実行します。この部分は参加者が変更できません。

1. アップストリームをクローンしてクリーンな `/aichallenge` ツリーを得る
2. `/aichallenge/simulator` と `/aichallenge/workspace/src/aichallenge_submit` を削除
3. 提出 tar.gz を `/aichallenge/workspace/src` に展開（→ `/aichallenge/workspace/src/aichallenge_submit/`）
4. ローカルの `aichallenge/simulator/`（AWSIM バイナリ + データ）をイメージに戻す
5. `rosdep install` + `colcon build --symlink-install --allow-overriding gyro_odometer --cmake-args -DCMAKE_BUILD_TYPE=Release` を実行

この結果、**`aichallenge_system/` 以下（`autostart_orchestrator_py`、`aichallenge_awsim_adapter` 等）はアップストリームのものが使われます**。参加者提出物はステップ 3 の `aichallenge_submit/` 展開のみです。

### eval イメージで変更できないもの

| 項目 | 固定値 | 理由 |
|---|---|---|
| RMW 実装 | `rmw_cyclonedds_cpp` | イメージに bake 済み |
| `CYCLONEDDS_URI` | `file:///opt/autoware/cyclonedds.xml` | イメージに bake 済み |
| アップストリーム `aichallenge_system/` | クローン時の HEAD | eval ステージでクローン |
| AWSIM バイナリ | ローカルの `aichallenge/simulator/` | ステップ 4 でコピー |
| `colcon build` オプション | `--allow-overriding gyro_odometer` 固定 | `Dockerfile` に記述 |

### .env・make は評価環境側の設定

**約束**: `.env`（`COMPOSE_FILE`、`HOST_UID`、`ROS_DOMAIN_ID` 等）および `Makefile` のターゲットは評価環境（運営）側が管理します。参加者は `.env` や `Makefile` を直接変更して評価を制御することはできません。

**破ると**: 提出 tar.gz 内に `.env` や `Makefile` を含めても、eval イメージはホストから切り離して動作するため効果がありません。評価環境の挙動を変えたい場合は、`aichallenge_submit/` パッケージの launch 引数・パラメータ YAML の変更で対応してください。

---

## 6. 関連ドキュメント

| ドキュメント | 内容 |
|---|---|
| [evaluation-interface.md](evaluation-interface.md) | 評価 FSM（`autostart_orchestrator` / `awsim_state_manager`）、結果 JSON スキーマ、AWS 評価パイプライン |
| [../design/specs/compose-overlays.md](../design/specs/compose-overlays.md) | `COMPOSE_FILE` の GPU/CPU/headless/WSL2 選択肢と `docker-compose.eval.yml` 必須の根拠 |
| [../design/specs/mpc-integration.md](../design/specs/mpc-integration.md) | MPC 制御器（`multi_purpose_mpc_ros`）の統合仕様 |
| [../design/specs/makefile-target-naming.md](../design/specs/makefile-target-naming.md) | make ターゲット命名規約 |
| [../README.md](../README.md) | ドキュメント命名・分類規約 |
| [../../README.md](../../README.md) | リポジトリルート README |
