# simulator_scripts

モード別の AWSIM 起動スクリプト。**起動引数の正本は各 `<mode>.sh`**。

## 呼び出しの仕組み

```
make simulator-<mode> / make dev / make dev2..dev4
  → docker compose up simulator (SIM_MODE=<mode>)
    → run_simulator.bash <mode> [args...]
      → simulator_scripts/<mode>.sh [args...]
```

- `run_simulator.bash` はモード名（第1引数 > `SIM_MODE` > 既定 `simulator`）で `<mode>.sh` に委譲する。
- モード名 `dev<N>` / `gate<N>` は `dev.sh N` / `gate.sh N` に解決される
  （例: `SIM_MODE=dev2` → `dev.sh 2`）。`make dev2/dev3/dev4` はこの経路。
- 不明なモードはフォールバックせず、対応モード一覧を出して exit 1。
- Makefile は `*.sh` を wildcard で拾って `make simulator-<mode>` を自動生成する。

## モード一覧

| スクリプト | 用途 | 引数 | 主な設定 |
|---|---|---|---|
| `eval.sh` | 評価 | - | 1台 / 6 laps / 600s / count開始 / handicap・wall-recovery・ranking off |
| `dev.sh` | 開発 | 車両数 N（既定 1） | unlimited laps・timeout / count開始 / wall-recovery on / handicap・ranking off |
| `gate.sh` | Safety Gate テスト | テスト番号 1/2/3/all（既定 all） | all は test1〜3 を順次実行 |
| `sample-scenario.sh` | シナリオ指定起動 | - | `StreamingAssets/Race/official.yaml` を `--scenario` で読み込む |
| `multiplay-server.sh` | Multiplay 専用サーバー | - | `-batchmode -nographics`、port 7777 |
| `multiplay-host.sh` | Multiplay ホスト | - | 127.0.0.1:7777、vehicle-index 1 |
| `multiplay-client.sh` | Multiplay クライアント | - | 127.0.0.1:7777、vehicle-index 1 |
| `simulator.sh`（既定） | 引数なし素起動 | - | 起動時UIで設定を選択 |

- start-mode は count（全車接地後にカウントダウン開始、`/admin/awsim/start` 不要）。
- センサー（camera/LiDAR）は off が既定。GPU 描画への切り替えは各ファイル末尾のコメント参照。
- 引数の完全な仕様は AWSIM リポジトリの `docs/AIChallenge/specs/CLI.md` を参照。

## 設計方針

**あえてモード別 1 ファイルにしている**（config 集約しない）。
1 ファイルで完結し、コピーしてモードを増やせ、`gate` のような差分も素直に書ける。
そのため `dev.sh` と `eval.sh` のようなほぼ同一ファイルもあるが、意図した重複であり DRY 化しない。
台数・テスト番号のような「同型で数だけ違う」ものはファイルを増やさず引数で取る（旧 `1p.sh`〜`4p.sh`、`safety-gate1〜3.sh` は廃止）。

新モードは近いものを `cp` して引数を直すだけ（`simulator-<新mode>` が自動で使える）。
末尾の GPU 切り替えコメントは編集対象行の隣に置くガイドなので、共通化せず各ファイルに残す。
