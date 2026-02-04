# Autostart Orchestrator / 評価フロー 設計（オーケストレーション）

`autostart_orchestrator_py` は **オーケストレータ**（orchestrator）として動作し、車両ごとの AWSIM 状態（`/<vehicle_ns>/awsim/state`）を監視して、競技走行に必要な一連の準備〜実行〜後処理を自動化します。

> Note:
> `autostart_orchestrator_py` は submit 側 launch ではなく、
> `aichallenge_system_launch/launch/mode/awsim.launch.xml` から起動される想定です。

## 目的
- AWSIM の state 変化に追従して、必要な補助処理を **順序・タイミング通り**に実行する
- 実行開始と終了時に、収集物（rosbag / 画面キャプチャ）を **確実に開始・停止**する
- 終了後に、Autoware の停止と結果変換などの **後処理を自動化**する

## 非目的
- 走行ロジック（プランニングや制御）の実装
- Autoware/AWSIM の内部状態推定の代替（あくまで state に従う）

## 評価フロー（現状）
以下の流れで評価をオーケストレーションします（コンテナ内の `aichallenge/run_evaluation.bash` を前提）。

1. 出力ディレクトリ作成（`/output/<timestamp>/d<domain_id>`、`/output/latest` シンボリックリンク）
2. overlay 環境の `source`（`/aichallenge/workspace/install/setup.bash`）と `ROS_DOMAIN_ID` の決定
3. AWSIM 起動（`/aichallenge/run_simulator.bash eval` をバックグラウンド起動）
4. AWSIM 準備待ち（`env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash wait-admin-state`）
5. Autoware 起動（`env OUTPUT_RUN_DIR=<out_dir> /aichallenge/run_autoware.bash awsim <domain_id>`）
6. 計測開始/終了に合わせた補助処理（`autostart_orchestrator_py`。後述）
7. AWSIM 終了待ち（AWSIM プロセス終了で評価終了）
8. 後処理（`/aichallenge/utils/fix_ownership.bash` による ownership 調整は best-effort）

### `run_evaluation.bash` の引数
`run_evaluation.bash` は引数で記録の on/off を切り替えます。

- `capture`: 画面キャプチャ有効（launch arg `capture:=true` が渡る）
- `rosbag`: rosbag 記録有効（launch arg `rosbag:=true` が渡る）
- `online`: `capture` と `rosbag` を同時に有効化
- `<UID> <GID>`: 終了時の ownership 調整に使用（`fix_ownership.bash` に委譲）

## 入出力（I/F）
### Subscribe
- `/admin/awsim/state`（AWSIM の状態通知）
  - 値の形式は環境依存のため、**実際の型・値の一覧は要確認**

### Subscribe（車両ごとの状態）
- `/<vehicle_ns>/awsim/state`（例: `/d1/awsim/state`）
  - 型: `std_msgs/msg/String`
  - 説明: 各車両の状態を文字列で配信します（ブリッジにより通常ドメイン側で購読可能）
  - 状態値:
    1. `Spawned`（車両の判定/配信が初期化された）
    2. `Running`（車両がアクティブで、`Time.timeScale > 0` になった）
    3. `TimingStart`（`lapCount.IsStarted()` が true になった＝計測スタート）
    4. `Finish`（`lapCount.IsFinished()` が true になった＝規定ラップ数に達した）

## ROS_DOMAIN_ID（ドメイン）分離の注意
AWSIM の `/admin/awsim/state` は **`ROS_DOMAIN_ID=0` 側**で流れていることが多く、Autoware 側（通常ドメイン）とは **別ドメイン**になりがちです。

- ROS 2 の topic/service/action は **ドメインを跨いで直接は通信できない**
- そのため、「AWSIM state 監視（domain0）」と「Autoware操作（通常ドメイン）」を同一ノード/同一プロセスに統合すると破綻しやすい

推奨アーキテクチャ:
- **domain0**: `utils/publish.bash wait-admin-state` で `/admin/awsim/state` を待つ（`ROS_DOMAIN_ID=0`）
- **通常ドメイン**: `autostart_orchestrator_py` を起動し、`/<vehicle_ns>/awsim/state` に基づき開始/停止を制御する

## 処理フロー（要件）
### 1) 開始トリガ（推奨: 車両ごとの `TimingStart`）
開始タイミングは **車両ごとの `/<vehicle_ns>/awsim/state`** を主に使うことを推奨します。

- 推奨: `TimingStart` を “計測開始” とみなし、記録（screen capture / rosbag）を開始する
- 代替: `Running` を開始トリガにする（計測開始より早く録画したい場合）

### 2) 走行開始前処理（initial pose / control）
次の順序で実行する（**順序が重要**）:
1. initial pose set（service: `/set_initial_pose` / `std_srvs/srv/Trigger`）
2. request control mode（topic: `/awsim/control_mode_request_topic` / `std_msgs/msg/Bool`）
3. （必要なら）screen capture start（service: `/debug/service/capture_screen` / `std_srvs/srv/Trigger`）
4. （必要なら）rosbag record start（`ros2 bag record` をサブプロセス起動。シェルは使わない）

> 実装（現状）:
> - initial pose / control mode はノード起動直後に best-effort 実行
> - 記録開始は `start_on_vehicle_state` 到達時（default: 空 = 即開始）

### 3) 停止トリガ（推奨: 車両ごとの `Finish`）
次の順序で実行する:
1. `/<vehicle_ns>/awsim/state == Finish` を検知したら stop を開始する（推奨）
2. screen capture stop
3. rosbag record stop
4. Autoware shutdown
5. result converter 実行
6. 後処理（成果物整理）

## 冪等性
`/<vehicle_ns>/awsim/state` は同じ値が複数回流れる可能性があるため、各操作は以下を満たすこと:
- 既に開始済みの記録開始を再実行しても二重起動しない（pid管理 or フラグ管理）
- 停止操作は「未起動でも成功扱い」にできる設計（停止対象が無い場合はWARNで継続）

## 失敗時の方針
- どの段階で失敗しても、可能な範囲で **記録系を停止**してから `ERROR` に遷移する
- 開始/停止トリガ待ちのタイムアウトは `fail_on_timeout` で扱いを切り替える（default: true）
  - true: ERROR 扱い（必要なら記録停止して非0終了）
  - false: WARN 扱い（可能な範囲で継続）

## パラメータ
### `aichallenge_system_launch/launch/mode/awsim.launch.xml` の引数（AWSIM時のみ有効）
- `capture`（true/false: 画面キャプチャを開始/停止する）
- `rosbag`（true/false: rosbag を開始/停止する）
- `start_on_vehicle_state`（default: 空 = 即開始）
- `stop_on_vehicle_state`（default推奨: `Finish`。空にすると自動停止しない）
- `exit_on_finish`（default: false。true だと stop 後にノードが終了する）
- `fail_on_timeout`（default: true。start/stop トリガ待ちがタイムアウトしたら ERROR 扱いにする）

### `autostart_orchestrator_py` のノードパラメータ（必要ならlaunch側で上書き）
- `vehicle_ns` / `vehicle_state_topic`（車両状態topic。デフォルトは `/<vehicle_ns>/awsim/state`）
- `wait_service_timeout_sec` / `call_timeout_sec`（サービス待ち/呼び出しタイムアウト）
- `finish_wait_timeout_sec`（開始/停止トリガ待ちのタイムアウト）
- `exit_on_finish`（stop 完了後にノード終了するか）
- `fail_on_timeout`（start/stop トリガ待ちタイムアウトを ERROR 扱いにするか）
- `output_dir` / `rosbag_log_file`（rosbag 実行ログ、出力先）
- `rosbag_topics` / `rosbag_output`（記録対象topic、出力bag名）
- `rosbag_storage_id` / `rosbag_compression_format` / `rosbag_compression_mode`（保存形式・圧縮設定）
- `rosbag_extra_args`（`ros2 bag record` への追加引数）
- `rosbag_argv_override`（`subprocess` に渡すargvを完全上書き。テスト/特殊用途向け）
- `rosbag_cmd`（deprecated: `shlex` でargv化して実行。シェルは使わない）
- `initial_pose_service` / `capture_service`（サービス名）
- `control_mode`（`1`=AUTONOMOUS, `0`=MANUAL）
- `control_mode_request_topic`（default: `/awsim/control_mode_request_topic`）

## 成果物（Artifacts）
`output_dir` 配下に、タイムスタンプ付きで保存する想定:
- `rosbag2/`（bag一式）
- `screen_capture/`（動画/画像）
- `logs/`（オーケストレータのログ、実行コマンドのstdout/stderr）
- `results/`（result converter の出力）
- `ros/log/`（ROS 2 launch/node のログ。`ROS_LOG_DIR` を `<output_dir>/ros/log` に設定して保存）

## 改善候補
- トリガ整理: initial pose/control を `Spawned` 到達後に実行するオプション（現状はノード起動直後）
- 記録開始の安全性: `start_on_vehicle_state` が来ない場合の扱い（タイムアウト/フォールバック方針）
- 停止保険: `/<vehicle_ns>/awsim/state` が来なくなった場合（heartbeat消失など）の終了判定
- result converter: 対象ファイル・起動タイミング・失敗時の扱いの明確化
