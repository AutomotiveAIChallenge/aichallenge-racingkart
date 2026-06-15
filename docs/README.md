# ドキュメント運用方針

## 方針

- `docs/spec/` — 現仕様を記述する spec。コミットして最新を維持する。
- `docs/plan/` — 実装計画（手順 / PR 分割 / TODO）。陳腐化しやすいため **コミットしない**（`.gitignore` 済み）。ローカル管理のみ。耐久性のある設計判断は spec に反映する。
- `docs/interface/` — **インターフェイス契約**（変更すると依存側が壊れる安定面）。spec が「なぜ・どう作ったか」を記述するのに対し、interface は「何を約束し、破ると何が壊れるか」を記述する。コミットして最新を維持する。
- `docs/guide/` — オンボーディング・発表用ガイド・スライド。**ここだけ Marp スライド（`*.marp.md`）を許可**する。
- `docs/spec/architecture.md` — リポジトリ構成・Compose トポロジ・評価フローのテキスト図（画像は使わず ASCII / 表で表現）。
- リファレンスドキュメント（`docs/spec/`・`docs/interface/`）は画像なし・Marp なし・SVG なしの plain Markdown のみ。図は ASCII ツリー / 表で表現する。`docs/guide/` は Marp デッキ（`*.marp.md`）を使用可。
- 命名規約: spec / interface はトピック名・日付なし（例: `compose-overlays.md`、`participant-interface.md`）。

## docs/spec/ 一覧

- `host-uid-containers.md` — 全 dev サービスをホスト UID/GID で実行する設計（rocker 相当を docker compose の `user:` / `HOME=/tmp` / `group_add` で再現）。実装状況・差異（sound.yml は simulator のみ、group_add は数値 GID）も記載。
- `architecture.md` — リポジトリ構成 / Compose トポロジ / ドメイン・Launch 階層 / 評価フローのテキスト図。
- `compose-overlays.md` — `.env` の `COMPOSE_FILE` で GPU / サウンド を切り替えるオーバーレイ設計（複数 compose ファイル構成の正）。
- `docker-audio.md` — Linux（PipeWire / Pulse）で `simulator` コンテナからホストへ音声を出すための前提と手順。
- `how-to-setup.md` — 環境構築（Ubuntu 22.04 想定）。`setup.bash` による対話一括セットアップから起動確認まで。
- `introduction.md` — 初学者向け `make dev` / `make eval` 入門。ホストでコマンドを実行し docker compose で AWSIM と Autoware を動かす全体像。
- `log-design.md` — `/output` 配下へログを集約する設計（compose / `run_evaluation.bash` の出力レイアウト）。
- `makefile-target-naming.md` — Makefile ターゲットの `<service>-<command>` 命名ガイドライン。
- `mpc-integration.md` — `multi_purpose_mpc_ros` のインテグレーション設計。

## docs/guide/ 一覧

- `beginner-deck.marp.md` — リポジトリの構造と基本操作を10分で把握する初学者向け Marp スライド。

## docs/interface/ 一覧

- `participant-interface.md` — 参加者（提出者）が守るべき契約（提出 tar.gz レイアウト / `control_method` / 必須トピック I/O / eval イメージで固定される前提）。
- `evaluation-interface.md` — 評価・システム基盤側が守るべき契約（ドメイン規約 / admin・awsim トピック / ノード責務分離 / 成果物・result JSON スキーマ）。

## 今後の課題（バックログ）

実装計画（plan; ローカル管理）から引き継いだ未対応項目。実装済みは除外。

- **readiness タイムアウト強化** — `/admin/awsim/state` 待ちのタイムアウトと失敗時ログ導線の明確化（対象: `autostart_orchestrator_py`）。
- **fail-fast（スタック / 衝突の早期打ち切り）** — 衝突 N 秒継続・速度閾値未満 N 秒継続などで早期終了し、打ち切り理由を `result-summary.json` とログに残す（対象: `aichallenge_system/autostart_orchestrator_py`）。
- **ROS 2 ログ集約の強化** — `ROS_LOG_DIR` を `output/<run_id>/dN/ros/log` へ確実に誘導し、「最初に見るログ 3 点セット」を整備。
- **>4 台の並列起動** — Domain 設計・評価仕様・負荷を含めた再設計が前提。
- **finish→down 自動化** — finish 検知後に `make down` 相当の自動停止（要件確定後）。
- **配布用 rosbag のトピックフィルタ** — カメラ等センサを含む配布用 rosbag を軽量化するためのフィルタリング。

## 既知の残クリーンアップ

`repo-cruft-cleanup` 計画の未完了項目（いずれも実在確認済み）。

- `record_rosbag.bash` が `aichallenge/utils/` と `vehicle/` に重複。手動記録ワークフロー（`vehicle/README.md`）が参照するのは `aichallenge/utils/record_rosbag.bash` で、`vehicle/record_rosbag.bash` への参照は見当たらない。どちらを正とするか（重複解消）を要確認。
- `docker-entrypoint.sh` 3 行目のコメントに `for rocker sessions` が残存 → rocker 廃止に合わせた更新が必要。
