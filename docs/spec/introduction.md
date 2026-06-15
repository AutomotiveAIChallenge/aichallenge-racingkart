# Introduction（初学者向け）: `make dev` / `make eval`

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

**ホストのPC でコマンドを打つ → `docker compose` で AWSIM と Autoware が起動する**、という構成です。

- **AWSIM**: シミュレータ（`simulator` サービス）
- **Autoware**: 自動運転ソフト（`autoware` サービス）
- **`make`**: `docker compose ...` を短く呼び出すエントリポイント
- **出力先**: `output/` 配下（ログ・結果）

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
# 評価用イメージを作る（--submit に提出物 tar.gz を指定）
./docker_build.sh eval --submit submit/aichallenge_submit.tar.gz

# 評価を実行（AWSIM + Autoware をバックグラウンド起動して評価シナリオを開始）
make eval

# 終わったら
make down
```

> 補足: スモークテスト専用の引数はありません。シナリオを変えたい場合は
> `SIM_MODE` 環境変数か `aichallenge/simulator_scripts/` 配下のスクリプトで切り替えます
> （詳細は `aichallenge/simulator_scripts/README.md` 参照）。

---

## コマンド早見表

| コマンド | 役割 | いつ使う？ | 主なログ/出力 |
| --- | --- | --- | --- |
| `./docker_build.sh dev` | **開発用Dockerイメージ**（`aichallenge-2025-dev`）を作る | 初回、またはDockerfile更新後 | `output/docker/<timestamp>-docker_build-<pid>.log`（最新は `output/latest/docker_build.log`） |
| `./docker_build.sh eval --submit <tar>` | **評価用Dockerイメージ**（`aichallenge-2025-eval`）を提出物込みで作る | 評価実行前、または提出物を変えた時 | （ビルド中は端末に表示） |
| `make autoware-build` | コンテナ内で **ROSワークスペースをビルド**（`aichallenge/workspace/install/` を作る） | 初回、または依存/ソース更新後 | （ビルド中は端末に表示。失敗したら直近の出力を確認） |
| `make dev` | **開発起動**: AWSIM + Autoware を起動して動かしっぱなしにする（1台） | 手元でデバッグ/可視化したい時 | `output/<run_id>/awsim.log`（AWSIM）/ `output/<run_id>/d<id>/autoware.log`（Autoware） |
| `make dev2` / `make dev3` / `make dev4` | **多車両開発**: N台の Autoware を ROS_DOMAIN_ID 1..N で並列起動 | 複数提出物を同時に動かしたい時 | 各 Domain のログ（上と同様） |
| `make gate1` / `gate2` / `gate3` | **安全ゲートシナリオ**での開発起動 | 安全ゲートシナリオを確認したい時 | `output/<run_id>/...` |
| `make ps` | 起動中コンテナを一覧表示 | 「動いてる？」確認 | （標準出力） |
| `make down` | 起動したコンテナをまとめて停止・片付け | 終了時、または詰まった時 | （標準出力） |
| `make eval` | **評価フロー実行**（評価用イメージで AWSIM + Autoware をバックグラウンド起動。評価終了後に Autoware コンテナは自動 exit するが、停止・片付けは `make down` が必要） | 評価を回したい時 | `output/<run_id>/d1/autoware.log`、`/output/latest/d1` 配下の固定リンク群（`result-details.json` / `capture.mp4` / `rosbag2_autoware.mcap` / `motion_analytics.html`）、`/output/<run_id>/d1/result-details*.json` |

> 補足: `make eval` は内部で `docker compose` を実行します。GPU/CPU の切り替えは `.env` の `COMPOSE_FILE` で行います。

---

## 使い分け（迷ったらここ）

- **`make dev`**: 起動して触るだけ。止めるまで動き続けます。最後は `make down`。
- **`make eval`**: 評価を回して結果を残す。事前に `./docker_build.sh eval --submit <tar>` で評価用イメージを作成してから実行します。`docker compose up -d` でバックグラウンド起動するため、評価終了後も `make down` で明示的に停止してください。

---

## よく使う設定（環境変数）

環境変数は `.env` ファイルで管理します。`./setup.bash env` を実行すると、GPU の有無を自動検出して `.env` を生成します。

### GPU を使う / 使わない（詰まったらまず CPU）

`.env` の `COMPOSE_FILE` を編集します。eval サービスはベースの `docker-compose.yml` に含まれます。

```bash
# CPU + サウンド（デフォルト）
COMPOSE_FILE=docker-compose.yml:docker-compose.sound.yml

# GPU（NVIDIA）+ サウンド（GPU環境ではこちらを選択）
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.sound.yml

# ヘッドレス（サウンドなし）
COMPOSE_FILE=docker-compose.yml
```

`/dev/nvidia0` の有無から GPU/CPU を自動判定します。

### Domain ID（複数作業・衝突回避）

```bash
ROS_DOMAIN_ID=1  # .env で設定（デフォルト 1）
```

同じマシンで複数セットを動かす際の衝突を避ける番号です。通常は `1` のままで構いません。

---

## よくある詰まり（最短で戻る）

- **起動できない / `pull_policy: never` っぽいエラー**: まず `./docker_build.sh dev`
- **`.../install/setup.bash` が無い**: まず `make autoware-build`
- **とにかく一旦止めたい**: まず `make down`

---

## 参考（必要になったら読む）

- 多車両起動: `make dev2` / `make dev3` / `make dev4`（ROS_DOMAIN_ID 1..N で並列起動）
- ログ設計メモ: [log-design.md](log-design.md)
