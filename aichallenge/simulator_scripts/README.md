# simulator_scripts

モード別の AWSIM 起動スクリプト。**起動引数の正本は各 `<mode>.sh`**。

## 呼び出しの仕組み

```
make simulator-<mode> / make dev / make dev2..dev4 / make gate1..gate3
  → docker compose up simulator (SIM_MODE=<mode>)
    → run_simulator.bash <mode> [args...]
      → simulator_scripts/<mode>.sh [args...]

make eval → run_evaluation.bash → evaluation.launch.xml
  → run_simulator.bash <sim_mode>（既定 eval、SIM_MODE で上書き可）
```

- `run_simulator.bash` はモード名（第1引数 > `SIM_MODE` > 既定 `simulator`）で `<mode>.sh` に委譲する。
- モード名 `dev<N>` / `gate<N>` は `dev.sh N` / `gate.sh N` に解決される
  （例: `SIM_MODE=dev2` → `dev.sh 2`）。
- 不明なモードはフォールバックせず、対応モード一覧を出して exit 1。
- Makefile は `*.sh` を wildcard で拾って `make simulator-<mode>` を自動生成する。
  `dev2..dev4` / `gate1..gate3` のエイリアスも `SIM_MODES` に追加してあり、
  `make simulator-dev2` / `make simulator-gate1` のように使える（AWSIM のみ起動）。
- `make dev` / `make gate1..gate3` / `make e2e` は AWSIM に加えて Autoware も起動する複合ターゲット。
  `make e2e` が起動するのは `e2e` モード（`e2e-final` は `make simulator-e2e-final`）。
  `make dev2..dev4` は N 台分の autoware を別 compose プロジェクト（ROS_DOMAIN_ID=1..N）で起動する。

## モード一覧

| スクリプト | 用途 | 引数 | 主な設定 |
|---|---|---|---|
| `eval.sh` | 評価 | - | 1台 / 6 laps / 600s / count開始 / handicap・wall-recovery・ranking off |
| `dev.sh` | 開発 / S2R 練習 | 車両数 N（既定 1） | unlimited laps・timeout / count開始 / handicap・wall-recovery・ranking off / camera・lidar off |
| `parallel.sh` | 複数台レース | - | 3台 / 6 laps / 600s / sync開始 / handicap・ranking・start-random off / wall-recovery off |
| `e2e.sh` | E2E 練習兼提出参考 | - | 1台 + NPC 2体 / 6 laps / timeout 実質なし / count開始（0秒） / start-random on / handicap・ranking off / camera・lidar cpu |
| `e2e-final.sh` | E2E 決勝 | - | 4台 / 6 laps / 420s / sync開始 / handicap・ranking on / camera・lidar cpu / sound on |
| `s2r-final.sh` | S2R 決勝 | - | 4台 / 6 laps / 420s / sync開始 / handicap・ranking on / camera・lidar off / sound on |
| `gate.sh` | Safety Gate テスト | テスト番号 1/2/3/all（既定 all） | 1台。all は test1〜3 を順次実行 |
| `sample-scenario.sh` | シナリオ指定起動 | - | `StreamingAssets/Race/official.yaml` を `--scenario` で読み込む |
| `multiplay-server.sh` | Multiplay 専用サーバー | - | `-batchmode -nographics`、port 7777 |
| `multiplay-host.sh` | Multiplay ホスト | - | 127.0.0.1:7777、vehicle-index 1 |
| `multiplay-client.sh` | Multiplay クライアント | - | 127.0.0.1:7777、vehicle-index 1 |
| `simulator.sh`（既定） | 引数なし素起動 | - | 起動時UIで設定を選択 |

- start-mode: `dev.sh` は count（全車接地後にカウントダウン開始、`/admin/awsim/start` 不要）。
  `eval.sh` / `parallel.sh` は sync（`/admin/awsim/start` 待ち。評価では awsim_state_manager が
  自動送信、手動で送るなら `make awsim-request-start`）。
- センサー（camera/LiDAR）は off が既定（E2E 系 2 モードのみ cpu）。GPU 描画への切り替えは各ファイル末尾のコメント参照。
- 引数の完全な仕様は AWSIM リポジトリの `internal-docs/specs/CLI.md`、
  または `AWSIM.x86_64 --help` を参照。

## 競技モード（E2E / S2R）

競技課題ごとに 1 系統。系統内は **練習（提出前の確認もこれ）→ 決勝** の 2 モードで、
handicap / ranking / 車両数だけが変わる。

| | E2E 系 | S2R 系 |
|---|---|---|
| 課題 | End-to-End（カメラ・LiDAR から直接制御） | Sim-to-Real（実車移行前提） |
| camera / lidar | `cpu` | `off` |
| imu / gnss / v2x | `off`（明示指定） | 既定の `on`（指定しない） |
| 練習 | `e2e.sh`（1台 + NPC 2体） | `dev.sh`（車両数は引数） |
| 決勝 | `e2e-final.sh` | `s2r-final.sh` |

- センサーの on/off がそのまま系統の定義。`--imu` / `--gnss` / `--v2x` は AWSIM 側の既定が
  `on` なので、E2E 系だけが明示的に `off` を書いている（S2R 系は書かないことで on）。
- **S2R の練習には `dev.sh` をそのまま使う**（camera・lidar off / handicap・ranking off /
  laps・timeout 無制限）。専用モードを別に持つ必要がないため、S2R 系のスクリプトは決勝用のみ。
- 練習は laps / timeout 無制限（`e2e.sh` は `--timeout 10000000.0`、`dev.sh` は
  加えて `--laps unlimited`）。周回や時間切れで止めずに走り続けられる。
  時間制限付きで確認したい場合は決勝モードか `eval.sh`（`make eval`）を使う。
- 練習は count 開始（接地後に自動カウントダウン。`e2e.sh` はカウント 0 秒で即スタート）、
  決勝は sync 開始（`/admin/awsim/start` 待ち = 全車の準備完了を待って一斉スタート）。
- `e2e.sh` は `--start-random on`。開始位置が毎回変わるので、特定のスタート位置に
  依存しない挙動を確認できる。決勝は `off`（公平性のため固定）。
- エンジン音（`--sound`）は決勝の 2 モードのみ on。練習・評価・開発モードは off。
- 複合ターゲットは `make dev` / `make e2e` の 2 つ（AWSIM + Autoware）。決勝モードは
  `make simulator-<mode>` で AWSIM だけ起動し、`make autoware-simulator` を別途叩く。

## 設計方針

**あえてモード別 1 ファイルにしている**（config 集約しない）。
1 ファイルで完結し、コピーしてモードを増やせ、`gate` のような差分も素直に書ける。
そのため `dev.sh` と `eval.sh` のようなほぼ同一ファイルもあるが、意図した重複であり DRY 化しない。

新モードは近いものを `cp` して引数を直すだけ（`simulator-<新mode>` が自動で使える）。
末尾の GPU 切り替えコメントは編集対象行の隣に置くガイドなので、共通化せず各ファイルに残す。
