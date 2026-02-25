# Introduction（初学者向け）: `make dev` / `./run_evaluation.bash`

このリポジトリは、**ホスト（あなたのPC）でコマンドを打つ → `docker compose` で AWSIM と Autoware を動かす**、という形になっています。

- **AWSIM**: シミュレータ（`simulator` サービス）
- **Autoware**: 自動運転ソフト（`autoware` サービス）
- **`make`**: `docker compose ...` を叩くための「短い入口」
- **出力先**: だいたい `output/` 配下（ログや結果）

---

## まずは結論（最短コピペ）

### 開発として起動して触りたい（おすすめ）

```bash
./docker_build.sh dev      # 開発用イメージを作る（最初に1回）
make autoware-build        # ワークスペースをビルド（最初に1回）
make dev                   # AWSIM + Autoware を起動

# 終わったら（困ったらこれ）
make down
```

### 評価フローを最後まで回したい（結果を残したい）

```bash
./run_evaluation.bash      # 評価を実行（実行後に自動で停止・片付けまでやる）

# 短く動くかだけ試したい
./run_evaluation.bash test
```

---

## コマンド早見表（「何をする？」「いつ使う？」）

| コマンド | 何をする？（役割） | いつ使う？ | 主なログ/出力 |
| --- | --- | --- | --- |
| `./docker_build.sh dev` | **開発用Dockerイメージ**（`aichallenge-2025-dev`）を作る | 初回、またはDockerfile更新後 | `output/docker/<timestamp>-docker_build-<pid>.log`（最新は `output/latest/docker_build.log`） |
| `make autoware-build` | コンテナ内で **ROSワークスペースをビルド**（`aichallenge/workspace/install/` を作る） | 初回、または依存/ソース更新後 | （ビルド中は端末に表示。失敗したら直近の出力を確認） |
| `make dev` | **開発起動**: AWSIM + Autoware を起動して “動かしっぱなし” にする | 手元でデバッグ/可視化したい時 | `output/<run_id>/d<id>/awsim.log` / `output/<run_id>/d<id>/autoware.log` |
| `make ps` | 起動中コンテナを一覧表示 | 「動いてる？」確認 | （標準出力） |
| `make down` | 起動したコンテナをまとめて停止・片付け | 終了時、または詰まった時 | （標準出力） |
| `./run_evaluation.bash` | **単独走行の評価**を実行（AWSIM起動→準備待ち→Autoware起動→終了待ち→停止/片付け） | “評価を回したい” 時 | `output/<run_id>/d1/autoware.log`、`/output/latest/d1` 配下の固定リンク群（`result-details.json` / `capture.mp4` / `rosbag2_autoware.mcap` / `motion_analytics.html`）、`/output/<run_id>/d1/result-details*.json` |
| `./run_evaluation.bash test` | **短いスモークテスト**（評価を短時間・単純条件で回す） | “まず動くか” だけ確認 | 上と同様（`output/<run_id>/...`） |

> 補足: `./run_evaluation.bash` は内部で `docker compose` を実行します。GPU/CPU の切り替えは `.env` の `COMPOSE_FILE` で行います。

---

## 使い分け（迷ったらここ）

- **`make dev`**: 「起動して触る」。止めるまで動き続けます。最後は `make down`。
- **`./run_evaluation.bash`**: 「評価を回して結果を残す」。終わったら自動で停止・片付けます（途中で `Ctrl+C` しても後片付けが走ります）。

---

## よく使う“設定”（環境変数）

環境変数は、コマンドの前に `NAME=value` を付けます（その1回だけ効きます）。

### GPUを使う/使わない（詰まったらまず CPU）

`.env` の `COMPOSE_FILE` を編集します。

```bash
# GPU（デフォルト）
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml

# CPU（GPUなし環境、または動作確認したい時）: 上の行を削除またはコメントアウト
```

### Domain ID（複数作業/衝突回避）

```bash
DOMAIN_ID=1 make dev
DOMAIN_ID=2 make dev
```

Domain ID は、同じマシンで複数セットを動かす時などに「衝突を避ける番号」です。
迷ったら `1` のままでOKです。

---

## よくある詰まり（最短で戻る）

- **起動できない / `pull_policy: never` っぽいエラー**: まず `./docker_build.sh dev`
- **`.../install/setup.bash` が無い**: まず `make autoware-build`
- **とにかく一旦止めたい**: まず `make down`

---

## 参考（必要になったら読む）

- 並列起動（複数提出物）: `design_docs/run_parallel_submissions.md`
- ログ設計メモ: `design_docs/log_design.md`
