# Plan（今後やりたいこと）

このファイルは `design_docs/*` の設計メモを前提に、今後の改善タスクを優先度順に集約するバックログです。

## P0（壊れやすさ潰し / 運用の安定化）

- CPU/GPU 分岐のガード強化（CPU-only で GPU compose/override を混ぜない）
  - 対象: `run_parallel_submissions.bash`, `docker-compose.gpu.yml`
- readiness（`/admin/awsim/state`）待ちのタイムアウト・失敗時ログ導線の明確化
  - 対象: `run_parallel_submissions.bash`, `aichallenge/utils/publish.bash`
- capture/rosbag が動かない時の原因切り分けを 1箇所に集約（前提: `autostart_orchestrator_py` 起動）
  - 対象: `aichallenge/workspace/src/aichallenge_system/autostart_orchestrator_py/README.md`, `design_docs/run_parallel_submissions.md`

## P1（見通し改善 / デバッグ性）

- compose override の DRY 化（`docker-compose.yml` の `x-autoware-base` をテンプレ利用して drift を減らす）
  - 対象: `run_parallel_submissions.bash`, `docker-compose.yml`
- ROS2 ログの集約（`ROS_LOG_DIR` 等を `output/<run_id>/dN/ros/log` へ）
  - 対象: `run_parallel_submissions.bash`, `run_evaluation.bash`, `aichallenge/run_evaluation.bash`
- “最初に見るログ3点セット” の徹底（スクリプト出力 / README / docs の整合）
  - 対象: `README.md`, `design_docs/run_parallel_submissions.md`

## P2（拡張 / 要件確定後）

- >4 台の並列起動（Domain/bridge/評価仕様/負荷まで含めて再設計）
  - 対象: `run_parallel_submissions.bash`, `aichallenge_system_launch`, 競技仕様
- 終了自動化（finish 検知→ `down` 相当までを自動化するかは要件次第）
  - 対象: `run_parallel_submissions.bash`, `autostart_orchestrator_py`

