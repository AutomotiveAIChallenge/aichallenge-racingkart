# Log設計メモ（/output 配下に集約）

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

作成日: 2026-01-27  
更新日: 2026-09-14

対象: `docker-compose.yml`（make 経由 / 主要パス）・`aichallenge/run_evaluation.bash`（評価オーケストレータ）

## 0. 目的

- **評価1回の結果と実行時ログを、1つの「実行単位ディレクトリ」に集約して追跡可能にする**
- **/output（ホストの `./output`）配下を見るだけで、何が起きたか・なぜ失敗したかを再現できる状態にする**

## 1. 現在の実行経路

### 主要パス（docker compose + make）

- **ビルド**: `./docker_build.sh [dev|eval] [--submit <tar>]`
- **開発 / 評価起動**: `make dev` / `make dev2..4` / `make eval`
- `docker-compose.yml` で `./output:/output` と `./aichallenge:/aichallenge` をマウント

`make` 経由であれば `HOST_UID`/`HOST_GID` が自動設定され、`output/` の所有者がホストユーザになる。

### レガシーパス（docker_run.sh / rocker）

`./docker_run.sh eval` による単発実行は旧来の一回限り用途として残っている。eval イメージは `CMD ["bash", "/aichallenge/run_evaluation.bash"]` で評価を実行するが、`./aichallenge` がコンテナにマウントされないため **スクリプト変更を反映するにはイメージの再ビルドが必要**。

## 2. 現在の出力レイアウト（実装済み）

### 2.1 run ディレクトリ

```
output/
  <YYYYMMDD-HHMMSS>/
    awsim.log                  # run_simulator.bash が LOG_DIR 直下に書く
    d<N>/                      # N = 1..4（Autoware domain per vehicle）
      autoware.log
      capture/
      ros/log/
      rosbag2_autoware/
      <dN>-result-details.json
      result-summary.json      # AWSIM が cwd (= d<N> or run_dir) に書く
  latest/                      # 実ディレクトリ（autostart_orchestrator が更新）
    d<N>/                      # 車両ごとのサブディレクトリ（N = domain id）
      result-details.json      -> ../<run_id>/d<N>/<dN>-result-details.json
      result-summary.json      -> ../<run_id>/d<N>/result-summary.json（またはその親）
      capture.mp4              -> ../<run_id>/d<N>/capture/cap-*.mp4
      rosbag2_autoware.mcap    -> ../<run_id>/d<N>/rosbag2_autoware/...
      motion_analytics.html    -> ../<run_id>/d<N>/motion_analytics-*.html
      autoware.log             -> ../<run_id>/d<N>/autoware.log
    docker_build.log           -> docker/<ts>-docker_build-<pid>.log
    docker_run.log             -> docker/<ts>-docker_run-<pid>.log
  docker/
    <ts>-docker_build-<pid>.log
    <ts>-docker_run-<pid>.log
```

### 2.2 ログの基本方針

- 重要ログは標準出力に出すだけでなく、**必ずファイルに tee する**
- build / run / eval の各段階で、コマンド・引数・環境（GPU/DOMAIN_ID 等）をログ先頭に記録する

## 3. 設計上の注意点

### 3.1 rosbag の安全停止

rosbag compose サービスには `stop_grace_period: 10s` を設定してあり、`docker compose down` 時に SIGINT が届いてメタデータ/クローズ処理が完了するまで待機する。`docker compose down --timeout` を短くすると rosbag が破損する可能性があるため注意。

### 3.2 COMPOSE_FILE による GPU/音声切り替え

`.env` の `COMPOSE_FILE` 変数でオーバーレイを選択する。5 つの compose ファイルの組み合わせは `makefile-target-naming.md` を参照。

### 3.3 `output/latest/` について

`latest/` は `autostart_orchestrator_node.py`（`_refresh_latest_artifact_links`）が評価完了時に更新する実ディレクトリ。`latest/d<N>/` 配下に最新 run の成果物を指す symlink が置かれる。`docker_build.log` / `docker_run.log` については `docker_build.sh` / `docker_run.sh` が `latest/` 直下に symlink を直接作成する。`topic_check.sh` が `output/latest/topic_check.txt` を出力する用途も引き続き有効。

### 3.4 実車 all-topic rosbag の型定義と除外対象

`rosbag` サービスの `record_all_rosbag.bash` は Autoware イメージの型定義を使用する。ホストの `racing_kart_interface` ワークスペースはマウントせず、その `install/setup.bash` も source しない。ドライバーと記録側でメッセージ定義が一致しない状態による記録失敗を避けるため、`-a --include-hidden-topics` に `--exclude '^(/racing_kart/.*|/comm_status)$'` を組み合わせる。

| 除外対象 | 理由 |
| --- | --- |
| `/racing_kart/.*` | `racing_kart_msgs` など、ドライバー側のワークスペースに依存する内部トピック。`/racing_kart/comms/status`（`racing_kart_msgs/msg/CommsStatus`）も含む。 |
| `/comm_status` | V2X の `tier4_v2x_msgs/msg/CommStatusArray`。Autoware 側にも同名パッケージが存在するが、このメッセージの定義と型サポートは存在しない。`/racing_kart/` の外にあるため個別の除外が必要。 |

2026-09-14 に `ssh tier4@a3` で次を確認した。

- `/comm_status` の publisher は `/v2x_communicator`、型は `tier4_v2x_msgs/msg/CommStatusArray`。
- ドライバーの `/workspace/install/tier4_v2x_msgs` には `CommStatusArray` の定義と C++ 型サポートのシンボルがある。
- rosbag と Autoware のコンテナは同一イメージを使用していた。その `/autoware/install/tier4_v2x_msgs` にはパッケージと C++ 型サポートライブラリがあるが、`CommStatusArray` の `.msg` / `.idl` と対応シンボルはない。
- `output/20260819-093911/d1/rosbag.log` には `racing_kart_msgs` の定義欠落と `Failure in topics discovery` が記録されていた。一方、直近の `output/20260914-145624/d1/rosbag.log` は正常終了しており、今回の確認では `/comm_status` によるコンテナ異常終了自体は再現していない。

A3 の rosbag2 は `0.15.15`。この版の [TopicFilter](https://github.com/ros2/rosbag2/blob/0.15.15/rosbag2_transport/src/rosbag2_transport/topic_filter.cpp#L156) はパッケージの型サポートライブラリの存在を確認するだけで、個々のメッセージの対応シンボルまでは確認しない。そのため `/comm_status` は「未知の型として自動でスキップされる」とは限らず、購読作成時に型サポートの読み込みに失敗する。[Recorder](https://github.com/ros2/rosbag2/blob/0.15.15/rosbag2_transport/src/rosbag2_transport/recorder.cpp#L182) の初回購読と後続のトピック探索では例外処理も異なるため、起動順によっては記録開始の失敗、探索エラーの繰り返しにつながり得る。

マウント削除と明示的な除外はセットで扱う。除外以外のトピックと hidden topic は引き続き記録対象とするが、記録側に型定義がない別パッケージのトピックは rosbag2 によってスキップされる。これらを記録対象へ戻す場合は、送信側と一致するメッセージ定義・型サポートを記録側に用意して確認する。

## 4. 今後の改善候補

- `meta.json`（run_id, started_at, exit_code, image, host, container など）の充実
- `ROS_HOME` / `ROS_LOG_DIR` を `output/<run_id>/dN/ros/` へ確実に誘導
- `logs/`, `results/`, `artifacts/` への整理（互換 symlink を残しつつ移行）
- 古い run のローテーションポリシー（要件確定後）
