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
> - control mode request は `std_msgs/msg/Bool` で `data=true` を publish
> - start/stop 待機はタイムアウトなし（対象状態が来るまで待機）

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
- 記録停止・終了待機にはタイムアウトを設けていない（状態変化はワーカーが検知するまで継続待ち）

## パラメータ
### `aichallenge_system_launch/launch/mode/awsim.launch.xml` の引数（AWSIM時のみ有効）
- `capture`（true/false: 画面キャプチャを開始/停止する）
- `rosbag`（true/false: rosbag を開始/停止する）
- `start_on_vehicle_state`（default: 空 = 即開始）
- `stop_on_vehicle_state`（default推奨: `Finish`。空にすると自動停止しない）
- `exit_on_finish`（default: false。true だと stop 後にノードが終了する）
- `autostart_orchestrator_params`（default: `autostart_orchestrator_py` の `config/autostart_orchestrator.param.yaml`）
- 追加可能なノード引数は上記 YAML で管理でき、launch 側の引数は必要時に上書きする方針になっています

### `autostart_orchestrator_py` のノードパラメータ（必要ならlaunch側で上書き）
- `vehicle_state_topic`（車両状態topic。デフォルトは `/<vehicle_ns>/awsim/state` は想定される形式）
- `exit_on_finish`（stop 完了後にノード終了するか）
- `enable_debug_visualization`（デバッグ表示の全状態可視化を有効化する: true/false、default: false）
- `call_initial_pose`（起動直後に初期姿勢 service を呼ぶか）
- `request_control_mode`（起動直後に control mode を要求するか）
- `rosbag_topics` / `rosbag_output`（記録対象topic、出力bag名）
- `rosbag_storage_id` / `rosbag_compression_format` / `rosbag_compression_mode`（保存形式・圧縮設定）
- `initial_pose_service` / `capture_service`（サービス名）
- `control_mode_request_topic`（default: `/awsim/control_mode_request_topic`）

### デバッグ可視化
- `enable_debug_visualization: true` のとき、Qt パネルでオーケストレーションの全状態を表示し、現在状態を青字で強調します。
  - 全状態: `BOOT -> WAIT_INITIAL_POSE -> REQUEST_CONTROL_MODE -> IDLE -> WAIT_START -> RECORDING -> WAIT_STOP -> AUTO_STOP_DISABLED -> STOPPING -> FINISHED -> ERROR`
  - `QListWidget` で縦並び表示し、現在状態だけ青字でハイライトします。
  - パネルサイズに応じて、表示文字サイズが自動的に拡縮されます。
  - パネル描画には `PySide6`（または `PyQt5`）が必要です。未インストール時は標準ログにフォールバックします。

## 成果物（Artifacts）
実行カレントディレクトリ配下に、タイムスタンプ付きで保存する想定:
- `rosbag2/`（bag一式）
- `screen_capture/`（動画/画像）
- `logs/`（オーケストレータのログ、実行コマンドのstdout/stderr）
- `results/`（result converter の出力）
- `ros/log/`（ROS 2 launch/node のログ。`ROS_LOG_DIR` は実行環境側で `<rosbag 出力先>/ros/log` 相当へ設定）

## 改善候補
- トリガ整理: initial pose/control を `Spawned` 到達後に実行するオプション（現状はノード起動直後）
- 記録開始の安全性: `start_on_vehicle_state` が未設定なら即時開始
- 停止保険: `/<vehicle_ns>/awsim/state` が長時間変化しない場合の停止保証（設計検討）
- result converter: 対象ファイル・起動タイミング・失敗時の扱いの明確化
