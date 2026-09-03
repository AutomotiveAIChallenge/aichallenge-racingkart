# 評価・システムインターフェース契約（守るべき約束）

> これは「変更するとここに依存する側が壊れる」安定面（契約）です。守るべき約束を冒頭の **「守るべき約束（一覧）」** にまとめ、各約束の背景となる参照情報を §2–§5 の各セクションに記載しています。変更時は互換性影響を必ず確認してください。

この文書の読者は **評価基盤・システムパッケージ（`aichallenge_system`）の保守担当**です。AWSIM シミュレータ・オーケストレータ FSM（`autostart_orchestrator` / `awsim_state_manager`）・Web/評価パイプライン（`aichallenge-aws`）が共通して依存する**システム側の契約**を 1 本に集約します。参加者が実装するノードの ROS トピック入出力（センサ入力 / 制御出力など）は姉妹文書 [participant-interface.md](./participant-interface.md) を参照してください。

---

## 1. 概要（この契約が何を保証するか）

この契約は以下の 6 つの面を固定します。

1. **ROS 2 ドメイン規約** — AWSIM が Domain 0、車両 Autoware が Domain 1..N に固定
2. **admin/awsim トピック** — スタート・リセット・状態遷移を駆動するトピックの名前・型・所有者
3. **ノード責務分離** — `awsim_state_manager_node` と `autostart_orchestrator_node` の境界
4. **成果物・ログレイアウト** — `/output` 配下のディレクトリ構造とシンボリックリンク
5. **result JSON スキーマ** — `result-summary.json`（schema v2）と `dN-result-details.json`（schema v3）のキー
6. **評価環境の入口（entrypoint）** — 評価パイプラインが起動するスクリプトの名前と、それが自己完結で満たす前提

いずれかを変更すると、評価パイプライン・複数車両ランの分離・スコアリング Lambda のいずれかが機能しなくなります。

---

## 守るべき約束（一覧）

1. **Domain 0 は AWSIM とその状態管理ノード専用とする。`awsim_state_manager_node` は必ず Domain 0 で起動する。** Domain 0 以外で起動すると AWSIM とオーケストレータの間でスタート/状態ハンドシェイクが成立しなくなる。

2. **車両 1 台 = 1 ドメイン（Domain 1..N）。`make dev2` / `dev3` / `dev4` は `docker compose -p N` で各 Domain に Autoware を立てる。Domain 0 に車両 Autoware を同居させてはならない。** 守らないと複数車両の評価分離が崩れ、DDS メッセージが混線する。

3. **クロスドメイン通信は `aichallenge_system/v2x_msgs`（`V2XVehiclePositionArray`、トピック `/v2x/vehicle_positions`）のみを使う。`domain_bridge` は purge 済みであり復活させない。車両間トピックを追加する場合は v2x の publisher/subscriber を使い、生のクロスドメイン DDS 購読は設けない。** `domain_bridge` の復活や生クロスドメイン購読を足すと多車両評価の分離が壊れる。

4. **`/admin/awsim/start`、`/admin/awsim/reset`、`/admin/awsim/state` の名前・型・値体系は変更しない。`awsim_state_manager` がトリガ条件を `waitstart,ready` で受け取り、一度だけ start を publish する挙動（`admin_start_once: true`）も固定。** 守らないとスタート/リセット信号が届かず評価を開始できなくなる。`make awsim-request-start` / `make awsim-request-reset` も機能しなくなる。

5. **`/awsim/state` の名前・型・状態文字列（`spawned, grounded, ready, start, finish`）は変更しない。`/awsim/control_mode_request_topic` の名前・型も変更しない。** 守らないと録画/キャプチャ開始トリガが届かず成果物が空になる。`make autoware-request-control` も機能しなくなる。

6. **`autostart_orchestrator_node` は `/admin/awsim/start` に一切触れない。`awsim_state_manager_node` は `/awsim/state`（車両 FSM）を消費しない。** この境界を越えると、多車両ランで各 Domain の orchestrator が `/admin/awsim/start` を横取りまたは重複送出し、AWSIM の sync モード開始シーケンスが崩れる。単一車両でも AWSIM kill タイミングと録画停止タイミングが連鎖してデッドロックする。

7. **`awsim_state_manager_node` は必ず Domain 0 で動作する。Domain 0 以外での起動を検知した場合ログ警告が出る（ソース: `awsim_state_manager_node.py` L98–102）。** Domain 0 以外で起動すると AWSIM プロセス監視と `/admin/awsim/start` の owner が Domain 0 の DDS グラフ外に出て、AWSIM がスタート信号を受け取れなくなる。

8. **`/output/<run_id>/d<N>/autoware.log`、`capture/`、`ros/log/`、`rosbag2_autoware/` のディレクトリ構造は変更しない。`out_dir` は `run_evaluation.bash` が `"/output/${ts}/d${domain_id}"` で生成する。** 守らないと `autostart_orchestrator` のリンク生成（`_refresh_latest_artifact_links`）が壊れる。AWS 評価側では、make フェーズの `ensure_result_summary`（`aichallenge-aws/makefile/main.bash`）が `upload_dir` / `run_dir` を探索して `result-summary.json` を回収する前提が崩れる（`result_update` Lambda 自体はパス探索せず、固定キー `result-summary.json` の S3 イベントで発火する）。

9. **`output/latest/` は実ディレクトリ（`mkdir -p`）であり、内部エントリがシンボリックリンクである。`latest/` 自身をシンボリックリンクに変えない。`latest/d<N>/` 配下の 6 リンク（`result-details.json`、`result-summary.json`、`capture.mp4`、`rosbag2_autoware.mcap`、`motion_analytics.html`、`autoware.log`）は `autostart_orchestrator` の `_refresh_latest_artifact_links` が作成する。`latest/` 直下の `docker_build.log` / `docker_run.log` は別作成者（`docker_build.sh` / `docker_run.sh`）が張る。これらのリンク名・配置は変更しない。** 守らないと AWS Lambda や外部ツールが `latest/` から最新成果物を特定できなくなる。

10. **`HOST_UID` / `HOST_GID` を Makefile が export し、`output/` 配下の成果物がホストユーザ所有になる。コンテナを直接 `docker compose` で起動する場合も `HOST_UID` / `HOST_GID` を渡すこと。** 守らないと `output/` が root 所有になり、ホストで成果物を参照/削除できなくなる。

11. **`dN-result-details.json` のファイル名接頭辞 `d{vehicle_number}-` と `schema_version: "v3"`、`result-summary.json` のファイル名と `schema_version: "v2"`（レースモード。セーフティゲートモードは `"safety-gate-v1"`、§5 参照）、`vehicles[].vehicle_number`、`vehicles[].final_position` は変更しない。** 守らないと AWS `result_update` Lambda がファイル検索に失敗するか、スコアリングが壊れる。`result-summary.json` はファイル名が Lambda の S3 イベント発火条件であり固定。

12. **`dN-result-details.json` の配置は AWSIM の CWD に従う（固定の `d0/` 前提でパスを決め打ちしない）。`result-summary.json` は `autostart_orchestrator` が `output_dir / "result-summary.json"` と `run_dir / "result-summary.json"` の両方を探索する（ソース: `_refresh_latest_artifact_links`）。この探索ロジックを変更する場合は配置パターンと整合させること。** 守らないと `latest/` へのシンボリックリンクが張れず、最新成果物の特定手段が失われる。


13. **評価パイプライン（`aichallenge-aws` の `main.bash`）が起動する入口は §6 の 3 本（`run_evaluation.bash` / `run_safety_gate.bash` / `run_parallel.bash`）だけで、いずれも引数なし・自己完結（ROS overlay と `SIM_MODE` を自分で決める）。名前と自己完結性を変更しない。** 守らないと評価イメージ（entrypoint 無し、提出物は `/aichallenge/d1` overlay）で `ros2: command not found` や提出パッケージ未検出で全件 failed になる。
---

## 2. ドメイン規約の約束

ソース確認先: `Makefile`、`aichallenge_system_launch/launch/evaluation.launch.xml`、`simulator.launch.xml`

| Domain | 役割 | 設定元 |
|---|---|---|
| `0` | AWSIM シミュレータ本体 + `awsim_state_manager_node`（管理・スタート信号の所有者） | `simulator` サービス。`evaluation.launch.xml` の `<set_env name="ROS_DOMAIN_ID" value="0"/>` にハードコード |
| `1..N` | 車両ごとの Autoware インスタンス（planning / control / localization） | Makefile `ROS_DOMAIN_ID := 1`（既定）。多車両は `docker compose -p N` で Domain 1..N に分離 |

---

## 3. admin/awsim トピック契約

ソース確認先: `awsim_state_manager_node.py`、`autostart_orchestrator.param.yaml`、`awsim_state_manager.param.yaml`、`Makefile`

### 3-1. 管理面（Domain 0 専用、`awsim_state_manager_node` が所有）

| トピック | 型 | 方向 | 値 / 意味 |
|---|---|---|---|
| `/admin/awsim/start` | `std_msgs/Bool` | 双方向（pub + sub） | `data=true` でレース開始。管理状態が `waitstart` / `ready` に達したとき `awsim_state_manager` が自動 publish。`make awsim-request-start` は Domain 0 で手動一発送出 |
| `/admin/awsim/reset` | `std_msgs/Empty` | pub | シミュレーションのリセット。`make awsim-request-reset` が Domain 0 で送出 |
| `/admin/awsim/state` | `std_msgs/String` | sub（AWSIM が publish） | 管理/レース状態 FSM 文字列。既知の値: `selectmode, playstart, ready, waitstart, start, lapcomplete, finish, finishall, terminate`。終了判定集合: `finish / finishall / finishedall / terminate / terminated` |

### 3-2. 車両面（Domain N、`autostart_orchestrator_node` が消費）

| トピック | 型 | 方向 | 値 / 意味 |
|---|---|---|---|
| `/awsim/state` | `std_msgs/String` | sub（AWSIM が publish） | 車両ごとの FSM 状態。既知の値: `spawned, grounded, ready, start, finish`。オーケストレータは `Grounded,Ready,Start` で録画/キャプチャを開始し、`Finish` で停止する（param: `vehicle_state_topic: /awsim/state`） |
| `/awsim/control_mode_request_topic` | `std_msgs/Bool` | pub（複数 publisher → AWSIM） | `data=true` = AUTONOMOUS engage。publisher は `autostart_orchestrator`（自動）/ `make autoware-request-control`（手動一発）/ `teleop_manager`（ジョイ）/ rviz プラグイン。なお `control_mode_adapter.py` は Autoware の**サービス** `/control/control_mode_request`（`autoware_auto_vehicle_msgs/srv/ControlModeCommand`）を受け、AUTONOMOUS→`Bool(true)` / MANUAL→`Bool(false)` に変換してこのトピックへ中継する |

---

## 4. ノード責務分離の約束

ソース確認先: `awsim_state_manager_node.py`、`autostart_orchestrator_node.py`、`autostart_orchestrator_py/README.md`

`awsim_state_manager_node` と `autostart_orchestrator_node` は意図的に Domain をまたいで分離されている。**この境界は越えないこと。**

### 責務テーブル

| 項目 | `awsim_state_manager_node`（Domain 0） | `autostart_orchestrator_node`（Domain N） |
|---|---|---|
| 起動場所 | `mode/awsim_state_manager.launch.xml` | `mode/awsim.launch.xml` |
| 所有トピック（pub） | `/admin/awsim/start` | `/awsim/control_mode_request_topic` |
| 所有トピック（sub） | `/admin/awsim/state` | `/awsim/state` |
| 固有責務 | AWSIM プロセス監視・kill・ライフサイクル管理 | 車両ごとの rosbag/キャプチャ開始/停止 FSM |
| 禁止事項 | `/awsim/state` の消費 | `/admin/awsim/start` への pub/sub |

---

## 5. 成果物・ログ契約

ソース確認先: `aichallenge/run_evaluation.bash`、`autostart_orchestrator_node.py`（`_refresh_latest_artifact_links`）、`aichallenge/run_simulator.bash`、`docs/spec/log-design.md`

### 5-1. `/output` レイアウト

```
output/
├── <YYYYMMDD-HHMMSS>/              # run_id（= run_dir）
│   ├── awsim.log                   # AWSIM stdout/stderr（run_simulator.bash が ${LOG_DIR}/awsim.log に tee）
│   ├── result-summary.json         # AWSIM が自身の CWD に書き出す（配置は下記注。dev/並列では run_dir 直下）
│   ├── dN-result-details.json      # 車両数分。AWSIM が自身の CWD に書き出す
│   └── d<N>/                       # 車両 Domain N の Autoware 側（run_autoware.bash / run_evaluation.bash の out_dir）
│       ├── autoware.log            # Autoware stdout/stderr（tee）
│       ├── capture/                # スクリーンキャプチャ（cap-*.mp4）
│       ├── ros/log/                # ROS_LOG_DIR
│       └── rosbag2_autoware/       # rosbag（.mcap）
├── latest/                         # 実ディレクトリ（シンボリックリンクを格納）
│   ├── docker_build.log            -> ../docker/<ts>-docker_build-<pid>.log  (docker_build.sh が作成)
│   ├── docker_run.log              -> ../docker/<ts>-docker_run-<pid>.log    (docker_run.sh が作成)
│   └── d<N>/                        (autostart_orchestrator が作成)
│       ├── result-details.json     -> ../<run_id>/d<N>/d<N>-result-details.json
│       ├── result-summary.json     -> ../<run_id>/result-summary.json（または d<N>/。AWSIM の CWD 次第）
│       ├── capture.mp4             -> ../<run_id>/d<N>/capture/cap-*.mp4
│       ├── rosbag2_autoware.mcap   -> ../<run_id>/d<N>/rosbag2_autoware/...
│       ├── motion_analytics.html   -> ../<run_id>/d<N>/motion_analytics-*.html
│       └── autoware.log            -> ../<run_id>/d<N>/autoware.log
└── docker/                         # docker build / run ログ
    └── <ts>-docker_{build,run}-<pid>.log
```

> 注: AWSIM はレース結果（`result-summary.json` / `dN-result-details.json`）を **自身の CWD** に書き出す。配置はフローで異なる:
> - **dev / 並列（`run_simulator.bash`）**: AWSIM の CWD = run_dir 直下。`awsim.log` も `${LOG_DIR}/awsim.log`（run_dir 直下）。実サンプル `output/20260607-235456/` では `awsim.log`・`d1..d4-result-details.json`・`result-summary.json` が全て run_dir 直下にあり、`d0/` は存在しない。
> - **eval / safety gate（`run_evaluation.bash`、`run_safety_gate.bash`）**: `cd "${out_dir}"`（= `d<N>/`）してから launch するため AWSIM の CWD = `d<N>/`。`evaluation.launch.xml` が `LOG_DIR=log_dir`（= `d<N>/`）を渡すので `awsim.log` も `d<N>/` に入る。
>
> `d0/` というディレクトリは生成されない。固定前提でパスを決め打ちせず、`autostart_orchestrator` の探索（約束 12）に従うこと。

### 5-2. result JSON スキーマ

result JSON の**生産者は AWSIM（Unity）シミュレータ本体**。`script/result-converter.py` は旧互換用の dead スクリプトであり、評価パイプラインには使われていない。信じるべきは AWSIM の実出力（実サンプル: `output/20260613-083405/` 等）。時間フィールドはすべて**秒（float）**。

#### `dN-result-details.json`（schema `"v3"`、車両 1 台につき 1 ファイル）

ファイル名は常に `d{vehicle_number}-result-details.json`（例: `d1-result-details.json`）。

| キー | 型 | 意味 |
|---|---|---|
| `schema_version` | string | 固定 `"v3"` |
| `vehicle_name` | string | `"GoKart{n}"` |
| `vehicle_number` | int | 1..N |
| `finished` | bool | 完走したか |
| `lap_count` | int | 完了ラップ数 |
| `required_laps` | int | 必要ラップ数 |
| `session_timeout` | float (秒) | 設定タイムアウト |
| `min_lap_time` | float (秒) | ラップなしのとき `0.0` |
| `avg_lap_time` | float (秒) | ラップなしのとき `0.0` |
| `total_lap_time` | float (秒) | 完了ラップの合計 |
| `laps` | float[] (秒) | ラップごとのタイム（なしは `[]`） |
| `penalty_count` | int | ペナルティ発動回数 |
| `penalty_total_seconds` | float (秒) | union（重複除去）された有効ペナルティ時間 |
| `penalty_events` | object[] | 個別ペナルティイベント（後述） |
| `penalty_by_kind` | object | 種別ごと集計（後述） |

`penalty_events[]` の各要素: `kind`（`"crash"` / `"wall"` / `"over"`）、`lap`（1 始まり int）、`race_time`（float 秒）、`duration`（float 秒）。`penalty_by_kind` は常に `crash` / `wall` / `over` の 3 キー、各々 `{ "count": int, "total_seconds": float }`。

不変条件: `len(penalty_events) == penalty_count`。`penalty_by_kind.*.count` の合計はその種別のイベント数に一致。`penalty_by_kind.*.total_seconds` の合計は単純合算であり `penalty_total_seconds`（union）を上回りうる（バグではない）。

#### `result-summary.json`（schema `"v2"`、レースにつき 1 ファイル）

| キー | 型 | 意味 |
|---|---|---|
| `schema_version` | string | 固定 `"v2"` |
| `session` | object | `required_laps` (int), `timeout` (float 秒), `total_vehicles` (int) |
| `vehicles` | object[] | 車両エントリ。`final_position` 昇順で並ぶ |
| `laps` | float[] | 後方互換: GoKart1 の `laps` のコピー |
| `min_time` | float (秒) | 後方互換: GoKart1 の `min_lap_time` |
| `total_lap_time` | float (秒) | 後方互換: GoKart1 の合計 |
| `num_laps` | int | 後方互換: GoKart1 の `lap_count` |

`vehicles[]` 要素: `vehicle_number` (int)、`vehicle_name` (string)、`final_position` (int, 1 始まり)、`finished` (bool)、`lap_count` (int)、`laps` (float[] 秒)、`min_lap_time` / `max_lap_time` / `avg_lap_time` / `total_lap_time` (float 秒)。`final_position` は MCR（"1334" 形式）: 同着はグループ最小順位を共有するため値が飛びうる（順位付けアルゴリズム自体は AWSIM 側にあり本リポジトリのソースには無く、実サンプル出力から確認）。

AWS `result_update` Lambda は `vehicles[].vehicle_number` と `vehicles[].final_position` のみを消費してスコア（Elo レーティング）を算出する。

##### セーフティゲートモードの `result-summary.json`（2026-09 追加）

セーフティゲート実行（`SIM_MODE=gate*`、`evaluation_mode: s2r_2_practice_solo`）では、AWSIM は
レースを走らせないため通常の summary は生成せず、代わりに **gate 版 summary** を CWD に書き出す。
完了契約（「summary を最後にアップロード → `result_update` 発火」）は全モード共通:

```json
{
  "schema_version": "safety-gate-v1",
  "safety_gate": {
    "all_passed": true,
    "gate_arg": "all",
    "scenario_folder": "SafetyGate",
    "tests": [{ "test_name": "test1", "passed": true, "fail_reason": "" }]
  },
  "vehicles": []
}
```

- `safety_gate` キーの有無がモード判別子。**レース用フィールドの偽装は禁止**（`vehicles` は空配列。
  `final_position` / `laps` のダミー値を入れない）。
- `schema_version` はレース用 `"v2"` と区別するため `"safety-gate-v1"` 固定。gate 版 summary は
  `safety_gate` + `vehicles` しか持たないので、v2（session + legacy projection）契約でパースしてはならない
  （AWS `result_update` は `schema_version` を読まず `vehicles[].final_position` のみ参照するため影響なし）。
- `vehicles: []` のため `result_update` の順位パースは安全に None を返し、`executions.status=2` と
  raw JSON の `result` 列保存のみが行われる（レーティングは `evaluation_mode` ガードで不変）。
- 人間/デバッグ用の `safety-gate-result.json` も従来どおり CWD に併存する（S3 へはアップロードしない）。

---

## 6. 評価環境の入口（entrypoint）契約

ソース確認先: `aichallenge/run_safety_gate.bash`、`aichallenge/run_parallel.bash`、`aichallenge-aws/makefile/main.bash`、`aichallenge-aws/makefile/Dockerfile`

評価環境（AWS Batch 上の評価イメージ）とローカル開発環境は、同じ `aichallenge/` を使いながら前提が異なる。

| 前提 | ローカル（`docker compose` / `make eval*`） | 評価環境（`aichallenge-aws`） |
|---|---|---|
| ROS overlay の source | `docker-entrypoint.sh`（ENTRYPOINT / `.bashrc`）が行う | ENTRYPOINT 無し。`main.bash` は `bash <entrypoint>` を呼ぶだけ |
| 提出物の配置 | `/aichallenge/workspace/src/aichallenge_submit`（1 つの workspace） | `/aichallenge/d1/workspace`（挑戦者）、`d2` / `d3`（対戦相手 / NPC）を base の上に overlay |
| AWSIM 起動引数 | `simulator_scripts/<SIM_MODE>.sh` | 同じ（`run_simulator.bash` 経由） |
| 完了シグナル | 人が `make down` | `/admin/awsim/state` の `terminate`（gate）/ `finishall`（parallel）を `main.bash` が監視し、grace 後にアップロード |

この差を吸収する層が **評価環境の入口スクリプト** である。`run_evaluation.bash` はローカルの `make eval` と評価環境の両方で使う唯一のスクリプトで、そのために ROS overlay を自分で source する。

### 6-1. 入口スクリプト

`aichallenge-aws` の `start_build` Lambda が `evaluation_mode` を入口名とタイムアウトに変換し、Step Functions が Batch コンテナの env（`EVAL_ENTRYPOINT`, `EVAL_TIMEOUT_SEC`）として渡す。`main.bash` は許可リストで名前を検証してから起動する。

| evaluation_mode | EVAL_ENTRYPOINT | SIM_MODE（AWSIM 引数） | EVAL_TIMEOUT_SEC |
|---|---|---|---|
| `s2r_1_practice_solo` | `run_evaluation.bash` | `eval`（`eval.sh`） | 800 |
| `s2r_2_practice_solo` | `run_safety_gate.bash` → `run_evaluation.bash` | `safety-gate`（`safety-gate.sh`） | 400 |
| `s2r_1_rated_trios` | `run_parallel.bash` → `parallel.launch.xml` | `parallel`（`parallel.sh`） | 800 |

ソース確認先: 各入口スクリプト本体、`aichallenge-aws/cloudformation_templates/lambda_functions/_shared/evaluation_mode.py`（`EVAL_RUNTIME_PROFILES`）、`aichallenge-aws/makefile/main.bash`（`ALLOWED_ENTRYPOINTS`）。

### 6-2. 入口スクリプトが守ること

- **引数なしで起動できる。** 評価環境から渡されるのは環境変数のみ（`EVAL_TIMEOUT_SEC` は `main.bash` の `timeout` に使われ、入口には渡らない）。
- **ROS 環境を自分で整える。** 単体系は `run_evaluation.bash` が `d1` overlay（あれば。base workspace を chain する）か base workspace を source し、`run_safety_gate.bash` はモード指定だけの薄い wrapper。parallel は `run_parallel.bash` が base を、`parallel.launch.xml` が各車両の `dN` を source する。`LOG_DIR` は launch ファイルが `log_dir` から設定する。
- **`SIM_MODE` を自分で決める。** 入口 = モードなので、外から渡された `SIM_MODE` は上書きする。AWSIM の引数は `simulator_scripts/<mode>.sh` に置き、入口や launch に直接書かない（`eval.sh` / `safety-gate.sh` / `parallel.sh`）。
- **rviz は評価環境側が切る（`RUN_RVIZ=false` を `main.bash` が export）。** 録画は standalone の `aichallenge_screen_recorder`（X root window 全体 = 1280x720）で行う。rviz が居ると Autoware 標準の AutowareScreenCapturePanel が同じ `/debug/service/capture_screen` を提供し、rviz ウィンドウ領域（1850x1136、オフセット 70,27）しか録れない。`evaluation.launch.xml` は `run_rviz=false` かつ `capture=true` のときだけ recorder を起動する。debug 可視化 / motion analytics は `debug` 引数で独立に on。
- **出力レイアウトは §5 に従う。** 単体系は `/output/<ts>/d1/`（`result-summary.json` も同じ場所）、parallel は `/output/<ts>/`（`result-summary.json` は run_dir 直下、車両ログは `d1..d3/`）。
- **完了は `/admin/awsim/state` で通知する。** gate は AWSIM の自己終了時 `terminate`、レース系は `finishall`。入口が自分で結果をアップロードしたり、`/output` 以外に書いたりしない。

### 6-3. 変更時の手順

入口の名前・数・自己完結性を変えるときは、(1) 本節と約束 13 を更新、(2) `aichallenge-aws` の `start_build`（mode → 入口の表）と `makefile/main.bash` の許可リストを合わせる、(3) base image（`aichallenge-aws/base_image`）を stg から再ビルドして push、(4) `makefile/deploy.sh` で `main.bash` を反映、の順で行う。base image は `aichallenge/` を丸ごと取り込むため、入口スクリプトは base image 再ビルドまで評価環境に届かない。

---

## 7. 関連ドキュメント

- ログ設計（`/output` 詳細・ローテーション方針・rosbag 安全停止）: [`../spec/log-design.md`](../spec/log-design.md)
- Compose オーバーレイ選択（`COMPOSE_FILE` の GPU/CPU/headless）: [`../spec/compose-overlays.md`](../spec/compose-overlays.md)
- Makefile ターゲット命名規約: [`../spec/makefile-target-naming.md`](../spec/makefile-target-naming.md)
- 参加者インターフェース契約（センサ入力 / 制御出力 / 提出物フォーマット）: [`participant-interface.md`](./participant-interface.md)
- ドキュメント運用方針: [`../README.md`](../README.md)
- リポジトリ全体概要: [`../../README.md`](../../README.md)
