# autostart.launch 設計（要件整理 + 実装方針）

## 概要
`autostart.launch` は **オーケストレータ**（orchestrator）として動作し、車両ごとの AWSIM 状態（`/<vehicle_ns>/awsim/state`）を監視して、競技走行に必要な一連の準備〜実行〜後処理を自動化します。

このドキュメントは、現状の要件メモを **設計として読みやすい形**に整理したものです。

## 目的
- AWSIM の state 変化に追従して、必要な補助処理を **順序・タイミング通り**に実行する
- 実行開始と終了時に、収集物（rosbag / 画面キャプチャ）を **確実に開始・停止**する
- 終了後に、Autoware の停止と結果変換などの **後処理を自動化**する

## 非目的（このlaunchでやらないこと）
- 走行ロジック（プランニングや制御）の実装
- Autoware/AWSIM の内部状態推定の代替（あくまで state に従う）

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
- そのため、`autostart` を「AWSIM state 監視（domain0）」と「Autoware操作（通常ドメイン）」を同一ノード/同一プロセスに統合すると破綻しやすい

推奨アーキテクチャ:
- **domain0**: `utils/publish.bash wait-admin-state` で `/admin/awsim/state` を待つ（ROS_DOMAIN_ID=0）
- **通常ドメイン**: `autostart`（オーケストレータ）を起動し、Autoware の service call や rosbag/capture 等を制御する
- **通常ドメインで購読できる車両state（`/<vehicle_ns>/awsim/state`）を主トリガにする**と、ドメイン跨ぎの同期を減らせる
- domain0 側の待ち合わせは、必要な場合のみ別系統で扱う（`autostart.launch` 自体は **通常ドメイン + 車両state**で完結させる）

### 実行する操作（現状は「コマンド実行」を想定）
- 初期姿勢設定（initial pose set）
- 制御モード要求（request control mode）
- 画面キャプチャ開始/停止（screen capture start/stop）
- rosbag 記録開始/停止（rosbag record start/stop）
- Autoware の停止
- result converter 実行（結果変換）
- 後処理（ログ整理など）

> 注意: 可能なら「ros2 service / action / topic publish」など **ROS Native なI/F**を優先し、
> どうしても必要なものだけを `ExecuteProcess` 等のコマンド実行に寄せる（再現性と失敗時の扱いを明確化する）。

## 状態遷移（オーケストレータの内部ステート）
AWSIM state の到達順が前提通りでない場合に備え、内部的には以下のステートマシンで管理します。

- `IDLE` : 起動直後。AWSIM state 待ち
- `PREPARE` : 走行開始前処理（初期姿勢/制御モード等）
- `RECORDING` : 収集開始（screen capture + rosbag）
- `RUNNING` : 走行中（必要なら監視のみ）
- `FINISHING` : 終了処理（record stop）
- `POST_PROCESS` : Autoware停止、result converter、後処理
- `DONE` : 完了
- `ERROR` : 失敗（後述の方針でクリーンアップ）

## 処理フロー（要件）
### 1) 開始トリガ（推奨: 車両ごとの `TimingStart`）
開始タイミングは **車両ごとの `/<vehicle_ns>/awsim/state`** を主に使うことを推奨します。

- 推奨: `TimingStart` を “計測開始” とみなし、記録（screen capture / rosbag）を開始する
- 代替: `Running` を開始トリガにする（計測開始より早く録画したい場合）

### 2) 走行開始前処理（initial pose / control）
次の順序で実行する（**順序が重要**）:
1. initial pose set
2. request control mode（推奨: `/awsim/control_mode_request_topic` に publish）
3. （必要なら）screen capture start
4. （必要なら）rosbag record start

### 3) 停止トリガ（推奨: 車両ごとの `Finish`）
次の順序で実行する:
1. `/<vehicle_ns>/awsim/state == Finish` を検知したら stop を開始する（推奨）
2. screen capture stop
3. rosbag record stop
4. Autoware shutdown
5. result converter 実行
6. 後処理（成果物整理）

## 冪等性（同じstateを複数回受けても壊れない）
`/<vehicle_ns>/awsim/state` は同じ値が複数回流れる可能性があるため、各操作は以下を満たすこと:
- 既に開始済みの記録開始を再実行しても二重起動しない（pid管理 or フラグ管理）
- 停止操作は「未起動でも成功扱い」にできる設計（停止対象が無い場合はWARNで継続）

## 失敗時の方針
- どの段階で失敗しても、可能な範囲で **記録系を停止**してから `ERROR` に遷移する
- `ERROR` でも result converter などの後処理を回すかは設定で切り替える（例: `run_postprocess_on_error`）

## パラメータ
### `aichallenge_system_launch/launch/mode/awsim.launch.xml` の引数（AWSIM時のみ有効）
- `capture`（true/false: 画面キャプチャを開始/停止する）
- `rosbag`（true/false: rosbag を開始/停止する）
 - `vehicle_ns`（通常は `ROS_DOMAIN_ID` から `d<ROS_DOMAIN_ID>` を自動決定。必要ならノードパラメータで上書き）
- `start_on_vehicle_state`（default推奨: `TimingStart`。空にすると即開始）
- `stop_on_vehicle_state`（default推奨: `Finish`。空にすると自動停止しない）

### `autostart_orchestrator_py` のノードパラメータ（必要ならlaunch側で上書き）
- `wait_service_timeout_sec` / `call_timeout_sec`（サービス待ち/呼び出しタイムアウト）
- `finish_wait_timeout_sec`（開始/停止トリガ待ちのタイムアウト）
- `rosbag_cmd` / `rosbag_log_file` / `output_dir`（rosbag 実行コマンド、ログ、出力先）
- `initial_pose_service` / `capture_service`（サービス名）
- `control_mode`（`1`=AUTONOMOUS, `0`=MANUAL）
- `control_mode_request_topic`（default: `/awsim/control_mode_request_topic`）

## 成果物（Artifacts）
`output_dir` 配下に、タイムスタンプ付きで保存する想定:
- `rosbag2/`（bag一式）
- `screen_capture/`（動画/画像）
- `logs/`（オーケストレータのログ、実行コマンドのstdout/stderr）
- `results/`（result converter の出力）

## 実装メモ（launch構成のイメージ）
- `aichallenge_system_launch/launch/mode/awsim.launch.xml`
  - AWSIM 時に `autostart_orchestrator_py` の rclpy ノードを起動し、`/<vehicle_ns>/awsim/state` に基づき開始/停止を制御する
  - rosbag は `/aichallenge/utils/record_rosbag.bash` をサブプロセスとして起動し、`SIGINT` で停止する

## TODO（要確認事項）
- `/<vehicle_ns>/awsim/state` 以外の全体状態（`/admin/awsim/state`）を停止保険として使うか（使う場合はドメイン跨ぎ設計を別途用意）
- initial pose set / control mode request を **コマンドで行うのか、service/topicで行うのか**
- screen capture の実装（使用ツール、出力形式、停止方法）
- Autoware shutdown の推奨手段（launch停止 / lifecycle / service など）
- result converter の実体（パッケージ名/引数/入出力）
