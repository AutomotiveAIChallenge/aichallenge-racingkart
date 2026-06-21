# アーキテクチャ（テキスト図）

> 仕様ドキュメント（現仕様の正）。文書運用方針は [docs/README.md](../README.md) を参照。図版は画像を使わず ASCII / 表で表現する。

関連ドキュメント:

- インターフェース契約（評価 FSM / result JSON / ドメイン規約）: [../interface/evaluation-interface.md](../interface/evaluation-interface.md)
- インターフェース契約（提出物フォーマット / トピック I/O）: [../interface/participant-interface.md](../interface/participant-interface.md)
- Compose オーバーレイ設計: [compose-overlays.md](compose-overlays.md)
- ログ設計（`/output` 詳細）: [log-design.md](log-design.md)

---

## リポジトリ構成

```
aichallenge-racingkart/              # リポジトリルート（Docker ビルドコンテキスト）
├── aichallenge/                     # コンテナ内に bind-mount される主要ツリー
│   ├── workspace/                   # colcon ワークスペース（Autoware アンダーレイのオーバーレイ）
│   │   └── src/
│   │       ├── aichallenge_submit/  # 参加者パッケージ群（提出物）← eval 時に tar.gz で差し替え
│   │       ├── aichallenge_system/  # 評価インフラパッケージ群（運営管理）
│   │       └── aichallenge_tools/  # 補助ツールパッケージ群（bag_manager_py, teleop_manager）
│   ├── simulator/                   # AWSIM バイナリ + データ（eval イメージに bake）
│   ├── simulator_scripts/           # AWSIM 起動シナリオスクリプト群
│   ├── utils/                       # ROS one-shot スクリプト（run_rviz.bash, record_rosbag.bash など）
│   ├── run_autoware.bash            # Autoware 起動スクリプト（autoware サービスのエントリ）
│   ├── run_evaluation.bash          # 評価実行スクリプト（eval サービスのエントリ）
│   ├── run_simulator.bash           # AWSIM 起動スクリプト（simulator サービスのエントリ）
│   └── build_autoware.bash         # colcon build ラッパ
├── vehicle/                         # 車両設定ファイル（cyclonedds.xml, zenoh.json5 など）
├── remote/                          # リモートビジュアライゼーション用スクリプト群
├── docs/                            # ドキュメント（spec/ interface/ の 2 種）
├── submit/                          # 提出物配置先（評価 tar.gz を置く。git 管理外）
├── output/                          # 評価成果物出力先（git 管理外）
├── Dockerfile                       # dev / eval 2 ステージ定義（common ステージ共有）
├── docker-compose.yml               # ベース Compose 定義（全サービス、eval 含む）
├── docker-compose.gpu.yml           # GPU オーバーレイ（NVIDIA ランタイム付与）
├── docker-compose.sound.yml         # サウンドオーバーレイ（PulseAudio / PipeWire）
├── Makefile                         # make ターゲット群（HOST_UID/GID, COMPOSE_FILE を一元管理）
├── setup.bash                       # 初回環境セットアップ（.env 生成・DDS ホストチューニング）
└── create_submit_file.bash          # 提出 tar.gz 生成（aichallenge/workspace/src/aichallenge_submit/ をパック）
```

---

## Docker / Compose トポロジ

### イメージターゲット

`Dockerfile` は `common` ステージを共有する 2 つのターゲットを定義する。

| ターゲット | イメージ名 | 用途 | `/aichallenge` の扱い |
|---|---|---|---|
| `dev` | `aichallenge-2025-dev` | 対話開発・ビルド・シミュレーション実行 | ホストから bind-mount（ソース編集が即反映） |
| `eval` | `aichallenge-2025-eval` | 評価（提出物を封入した sealed イメージ） | イメージに bake 済み（ホストマウントなし） |

`eval` ビルド時の処理:

1. アップストリームをクローンしてクリーンな `/aichallenge` ツリーを取得
2. `/aichallenge/simulator` と `/aichallenge/workspace/src/aichallenge_submit` を削除
3. 提出 tar.gz を `/aichallenge/workspace/src/` に展開（→ `/aichallenge/workspace/src/aichallenge_submit/`）
4. ローカルの `aichallenge/simulator/`（AWSIM バイナリ + データ）をイメージに戻す
5. `rosdep install` + `colcon build` を実行してビルド済み状態にする

### Compose サービス一覧

`docker-compose.yml` が定義する全サービス。
全サービスは `network_mode: host`（DDS = CycloneDDS、ホストネットワーク直接利用）。

| サービス名 | イメージ | 用途 | エントリ |
|---|---|---|---|
| `autoware` | `aichallenge-2025-dev` | Autoware スタック実行（ROS_DOMAIN_ID=1..N） | `run_autoware.bash` |
| `autoware-build` | `aichallenge-2025-dev` | colcon build 実行 | `build_autoware.bash` |
| `simulator` | `aichallenge-2025-dev` | AWSIM 起動（ROS_DOMAIN_ID=0） | `run_simulator.bash` |
| `autoware-command` | `aichallenge-2025-dev` | ROS one-shot コマンド（initial pose / control 要求など） | `$CMD` 変数で指定 |
| `zenoh` | `aichallenge-2025-dev` | Zenoh ブリッジ（実車連携用。実車環境でのみ使用） | `zenoh-bridge-ros2dds` |
| `driver` | `ghcr.io/tier4/racing_kart_interface:latest-experiment` | 実車ドライバスタック（racing_kart_interface） | `vehicle` モード |
| `rviz2` | `aichallenge-2025-dev` | RViz2 リモートビジュアライゼーション | `run_rviz.bash remote` |
| `autoware-simulator-evaluation` | `aichallenge-2025-eval` | 封入評価実行 | `run_evaluation.bash` |

### COMPOSE_FILE オーバーレイモデル

GPU / サウンド の切り替えは `.env` の `COMPOSE_FILE` 変数で制御する。
`./setup.bash env` が `/dev/nvidia0` の存在を自動検出して正しい行を書き込む。

| 構成 | `COMPOSE_FILE` の値 |
|---|---|
| CPU + サウンドなし（ヘッドレス） | `docker-compose.yml` |
| CPU + サウンドあり | `docker-compose.yml:docker-compose.sound.yml` |
| GPU + サウンドなし | `docker-compose.yml:docker-compose.gpu.yml` |
| GPU + サウンドあり | `docker-compose.yml:docker-compose.gpu.yml:docker-compose.sound.yml` |

`autoware-simulator-evaluation` サービスはベースの `docker-compose.yml` に含まれる。
GPU / サウンド オーバーレイはそれぞれ対応サービスにのみ差分を適用するため単独では動作しない。

詳細は [compose-overlays.md](compose-overlays.md) を参照。

---

## ROS 2 ドメインと Launch 階層

### ドメイン規約

クロスドメイン通信は `v2x_msgs`（トピック `/v2x/vehicle_positions`）のみ使用する。
`domain_bridge` は廃止済みであり復活させない。

| Domain | 役割 | 設定元 |
|---|---|---|
| `0` | AWSIM シミュレータ本体 + `awsim_state_manager_node`（管理・スタート信号の所有者） | `evaluation.launch.xml` にハードコード。`simulator` サービスで起動 |
| `1..N` | 車両ごとの Autoware インスタンス（planning / control / localization） | Makefile `ROS_DOMAIN_ID := 1`（既定）。多車両は `docker compose -p N` で分離 |

### Launch ツリー

```
evaluation.launch.xml              ← run_evaluation.bash のエントリ（単一車両 eval 用）
├── ROS_DOMAIN_ID=0 グループ
│   ├── AWSIM.x86_64 バイナリ
│   └── mode/awsim_state_manager.launch.xml
│       └── awsim_state_manager_node.py    ← /admin/awsim/start の所有者
└── ROS_DOMAIN_ID=<domain_id> グループ
    └── aichallenge_system.launch.xml
        ├── aichallenge_submit_launch/aichallenge_submit.launch.xml  ← 参加者エントリ
        │   └── reference.launch.xml
        │       ├── sensing（imu_corrector, racing_kart_gnss_poser, gyro_odometer）
        │       ├── localization（imu_gnss_poser, ekf_localizer, twist2accel）
        │       ├── planning（simple_trajectory_generator）
        │       ├── control（control_method で選択: mpc / pure_pursuit / tiny_lidar_net / pilot_net / joycon）
        │       └── map（lanelet2_map_loader）
        ├── rviz2（オプション）
        ├── mode/awsim.launch.xml（simulation:=true のとき）
        │   ├── aichallenge_awsim_adapter   ← 現状全行コメントアウト（無効）
        │   └── autostart_orchestrator_node.py  ← 車両ごとの FSM・録画管理
        ├── mode/real.launch.xml（simulation:=false のとき）
        └── mode/v2x.launch.xml（domain_id != 0 のとき）

simulator.launch.xml               ← 多車両開発（make devN）のシミュレータ専用エントリ
└── ROS_DOMAIN_ID=0: AWSIM + awsim_state_manager_node のみ（per-vehicle ノードなし）
```

### パッケージ一覧

#### `aichallenge_submit/`（参加者が提出するパッケージ群、15 パッケージ）

eval イメージビルド時にこのディレクトリ全体が提出 tar.gz の内容で差し替えられる。

| パッケージ | 役割 |
|---|---|
| `aichallenge_submit_launch` | 評価エントリ launch ファイル（`aichallenge_submit.launch.xml`）を提供 |
| `gyro_odometer` | Autoware アンダーレイを上書きするカスタム実装 |
| `imu_corrector` | IMU 補正 |
| `imu_gnss_poser` | IMU + GNSS 融合による自己位置初期化 |
| `laserscan_generator` | LiDAR スキャン生成 |
| `multi_purpose_mpc_ros` | MPC 制御器（Python） |
| `multi_purpose_mpc_ros_msgs` | MPC 制御器用カスタムメッセージ |
| `path_to_trajectory` | パスを Trajectory に変換 |
| `pilot_net_controller` | カメラ画像入力の学習ベース制御器（Python） |
| `racing_kart_description` | カート URDF / パラメータ |
| `racing_kart_gnss_poser` | レーシングカート向け GNSS 自己位置推定 |
| `racing_kart_sensor_kit_description` | センサキット構成記述 |
| `simple_pure_pursuit` | Pure Pursuit 制御器（C++） |
| `simple_trajectory_generator` | 参照軌跡生成（プランニング） |
| `tiny_lidar_net_controller` | LiDAR スキャン入力の学習ベース制御器（Python） |

#### `aichallenge_system/`（評価インフラパッケージ群、7 パッケージ + 補助ディレクトリ）

参加者は提出しない。eval イメージではアップストリームのものが使われる。

| パッケージ | 役割 |
|---|---|
| `aichallenge_system_launch` | 評価・システム launch のオーケストレーション |
| `aichallenge_awsim_adapter` | AWSIM ⇄ Autoware トピック変換（現状無効、全行コメントアウト） |
| `autostart_orchestrator_py` | 車両ごとの評価 FSM（rosbag / キャプチャ開始停止）+ `awsim_state_manager_node.py` |
| `aichallenge_screen_recorder` | 画面キャプチャ録画（`cap-*.mp4` 生成） |
| `v2x_msgs` | クロスドメイン間 V2X 通信用カスタムメッセージ |
| `aichallenge_control_rviz_plugin` | RViz2 制御モードプラグイン |
| `autoware_overlay_rviz_plugin` | RViz2 Autoware オーバーレイプラグイン |
| `script`（※非 colcon パッケージ） | 補助スクリプト群（`package.xml` を持たない補助ディレクトリ） |

---

## 評価フロー（状態遷移）

詳細な契約（トピック名・型・禁止事項）は [../interface/evaluation-interface.md](../interface/evaluation-interface.md) を参照。

### 管理 FSM（`/admin/awsim/state`、Domain 0、AWSIM が publish）

`awsim_state_manager_node` が購読し、`waitstart` または `ready` 到達時に `/admin/awsim/start`（Bool=true）を一度だけ publish する。

```
             AWSIM 起動
                 |
                 v
          [selectmode]
                 |
                 v
          [playstart]
                 |
                 v
            [ready] <---+
                 |      |  （参加者車両の準備待ち）
                 v      |
          [waitstart] --+
                 |
                 |  /admin/awsim/start = true を受信
                 v
            [start]
                 |
                 v
         [lapcomplete]     （1 ラップ完了ごとに経由）
                 |
                 v
            [finish]       （当該車両のフィニッシュ）
                 |
                 v
          [finishall]      （全車両フィニッシュ）
                 |
                 v
          [terminate]      （セッション終了）
```

終了判定集合: `finish / finishall / finishedall / terminate / terminated`

### 車両 FSM（`/awsim/state`、Domain N、AWSIM が publish）

`autostart_orchestrator_node` が購読し、録画・キャプチャのトリガとして使用する。

```
           車両スポーン
                 |
                 v
           [spawned]
                 |
                 v
           [grounded]     ← 録画・キャプチャ準備開始
                 |
                 v
            [ready]       ← /set_initial_pose サービス呼び出し
                 |        ← /awsim/control_mode_request_topic = true 送信
                 |        ← rosbag record 開始
                 v
            [start]       ← レース開始
                 |
                 v
            [finish]      ← rosbag record 停止、キャプチャ終了
```

### オーケストレータのキャプチャパイプライン

`autostart_orchestrator_node` が `/awsim/state` に応じて以下を順次実行する。

```
[grounded] 受信
     |
     v
ログディレクトリ準備
（output/<run_id>/d<N>/ を作成）
     |
     v
スクリーンキャプチャ開始
（aichallenge_screen_recorder → capture/cap-*.mp4）
     |
     v
/set_initial_pose サービス呼び出し
（imu_gnss_poser に初期自己位置設定を要求）
     |
     v
[ready] 受信
     |
     v
/awsim/control_mode_request_topic = true 送信
（AUTONOMOUS モード engage）
     |
     v
rosbag record 開始
（output/<run_id>/d<N>/rosbag2_autoware/ → .mcap）
     |
     v
[finish] 受信
     |
     v
rosbag record 停止
     |
     v
スクリーンキャプチャ停止
     |
     v
output/latest/d<N>/ へのシンボリックリンク更新
（result-details.json, result-summary.json, capture.mp4,
  rosbag2_autoware.mcap, motion_analytics.html, autoware.log）
```

### 成果物レイアウト（`output/`）

```
output/
├── <YYYYMMDD-HHMMSS>/              # run_id（タイムスタンプ）
│   ├── awsim.log                   # AWSIM stdout/stderr
│   ├── result-summary.json         # レース結果サマリ（schema v2）
│   ├── dN-result-details.json      # 車両別詳細（schema v3）
│   └── d<N>/                       # 車両 Domain N の Autoware 側成果物
│       ├── autoware.log            # Autoware stdout/stderr
│       ├── capture/                # スクリーンキャプチャ（cap-*.mp4）
│       ├── ros/log/                # ROS_LOG_DIR
│       └── rosbag2_autoware/       # rosbag（.mcap）
├── latest/                         # 実ディレクトリ（内部エントリがシンボリックリンク）
│   ├── docker_build.log            -> ../docker/<ts>-docker_build-<pid>.log
│   ├── docker_run.log              -> ../docker/<ts>-docker_run-<pid>.log
│   └── d<N>/
│       ├── result-details.json     -> ../<run_id>/d<N>/d<N>-result-details.json
│       ├── result-summary.json     -> ../<run_id>/result-summary.json
│       ├── capture.mp4             -> ../<run_id>/d<N>/capture/cap-*.mp4
│       ├── rosbag2_autoware.mcap   -> ../<run_id>/d<N>/rosbag2_autoware/...
│       ├── motion_analytics.html   -> ../<run_id>/d<N>/motion_analytics-*.html
│       └── autoware.log            -> ../<run_id>/d<N>/autoware.log
└── docker/                         # docker build / run ログ
    └── <ts>-docker_{build,run}-<pid>.log
```

> 注: `result-summary.json` / `dN-result-details.json` は AWSIM の CWD に書き出される。
> - `run_simulator.bash`（dev / 並列）: AWSIM の CWD = run_dir 直下。`d0/` ディレクトリは生成されない。
> - `run_evaluation.bash`（eval）: `cd <out_dir>` 後に launch するため CWD = `d<N>/`。
>
> パスを固定前提で決め打ちせず、`autostart_orchestrator` の探索ロジック（`output_dir / "result-summary.json"` と `run_dir / "result-summary.json"` の両方を探索）に従うこと。
