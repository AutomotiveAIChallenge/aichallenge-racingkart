# research_tools

研究用の計測ツールです。コンテナからは `/aichallenge/research_tools` として見えます。

- `lap_monitor.py`: `/awsim/status` を購読し、周回タイムを表示する
  ```
  CMD="python3 /aichallenge/research_tools/lap_monitor.py 120" docker compose run --rm --no-deps autoware-command
  ```
- `bench.sh <label> [秒]`: `make dev` で起動し直し、周回タイムと MPC ソルバーの統計（`[mpc_stats]`）を記録する。最後に `make down` で停止する
  ```
  aichallenge/research_tools/bench.sh mylabel 290 | tee aichallenge/research_tools/results/bench_mylabel.txt
  ```
- `results/`: 計測結果
  - `bench_baseline.txt`: 公式サンプルのまま（a284274）
  - `bench_fixed_bc_only.txt`: 修正 (b)(c) だけ。1周目の途中で停止した
  - `bench_fixed_all.txt`: 修正 (a)(b)(c) を全部入れたもの。**これが研究のベースライン**
