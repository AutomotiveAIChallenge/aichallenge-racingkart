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

- 固定サービス構成（`autoware-domain1..4`）と `docker-compose.yml` の drift 防止
  - 対象: `docker-compose.yml`, `design_docs/run_parallel_submissions.md`
- ROS2 ログの集約（`ROS_LOG_DIR` 等を `output/<run_id>/dN/ros/log` へ）
  - 対象: `run_parallel_submissions.bash`, `run_evaluation.bash`, `aichallenge/run_evaluation.bash`
- “最初に見るログ3点セット” の徹底（スクリプト出力 / README / docs の整合）
  - 対象: `README.md`, `design_docs/run_parallel_submissions.md`

## P2（拡張 / 要件確定後）

- >4 台の並列起動（Domain/bridge/評価仕様/負荷まで含めて再設計）
  - 対象: `run_parallel_submissions.bash`, `aichallenge_system_launch`, 競技仕様
- 終了自動化（finish 検知→ `down` 相当までを自動化するかは要件次第）
  - 対象: `run_parallel_submissions.bash`, `autostart_orchestrator_py`

---

# docker_build_run.bash 設計・実装計画（memo.md反映）

目的: `docker build` と `run-sim-eval`（docker compose）を「ホストから1コマンドで」回せるようにし、ログ/成果物を **`./output` 配下に綺麗に集約**する。

参照: `memo.md`（2026-01-28）  
重要論点: `docker compose down` で全停止できる・ただし **rosbag は SIGINT で自然終了**させたい（`stop_grace_period` を確保）。

---

## 1. 背景（memo.md の要旨）

- rocker は便利だが、コンテナを抜けると履歴が消えたり、外から叩きにくい。
- `make run-sim-eval` のように **ネイティブ環境から docker compose を叩く**のが楽。
- submit fileをdocker内のみで展開して実行すれば複数台のバトルが可能になるはず
- 一回一回ローカルにsubmitファイルを展開していると差分の管理が大変になるので、docker_build_run.bashで起動するときはsubmit fileは展開しないようにする。
- `docker compose down` で全部落ちるのは運用上ラクだが、**rosbag はメタデータ/クローズ処理が必要**で、SIGKILL だと壊れる可能性がある。
- 運営/参加者ともに「ログ以外の情報を取りに行く」のが辛いので、**ログに情報を詰め込みたい**。

---

## 2. 現状（このリポジトリ内の実装状況）

既に整備済み（Planの前提）:

- 出力は `output/<run_id>/d<domain_id>/` に割り振り（`latest` は `/output/latest/...` で固定参照）。
  - 複数提出物のグルーピング: `output/<run_id>/<run_group>/d<domain_id>/`
- host側ログは `output/docker/<event_id>/`（`output/latest/docker_build.log`, `output/latest/docker_run.log`）。
- `make run-sim-eval` は `DOMAIN_IDS=...` で複数domain連続実行可能（`run-sim-eval-1-4` あり）。
- rosbag compose サービスは `stop_signal: SIGINT` + `stop_grace_period` を確保。
- `./docker_build.sh eval --submit <tar.gz>` で eval 用イメージに提出物を差し替え可能（`Dockerfile` に `ARG SUBMIT_TAR`）。
- `run_parallel_submissions.bash` を用意（複数 submit の同時起動）。
  - `--submit` 順に domain id `1..4` を固定割当して同時起動する。
  - 提出物はリポジトリ配下の tar.gz をビルド引数として渡し、作業ツリーは直接展開しない。

残課題（今回の主対象）:

- run_parallel運用の共通CLI未整備に伴う課題（`docker_build_run.bash` の未実装対応）：  
  - multi-submit/ログ/停止/成果物整理を 1 つにまとめる前段計画。
- fail-fast（初期姿勢/トピック/スタック等）と、その判定・打ち切り理由の成果物への記録。

---

## 3. ゴール（要件）

### 3.1 機能要件

- `./docker_build_run.bash all` で、`--submit` を複数回指定して **build → eval** を一気通貫で実行できる（最大4件）。
  - `--submit` の **指定順**に domain id を `1,2,3,4` に固定割当（domain id 指定は不要）。
  - 提出物は **ホストに展開しない**（`aichallenge/workspace/src/aichallenge_submit/` を汚さない）。Docker volume に展開してマウントする。
- `docker_build_run.bash` により、以下をホストから1コマンドで実行できる:
  - `dev` / `eval` イメージのビルド（`./docker_build.sh` のラップ）
  - 評価の起動（docker compose ベース）
- `make run-sim-eval` は domain id を複数指定して連続実行できる（デフォルト `1,2,3,4`）。
- 結果/ログは `./output` 配下に集約し、Run 追跡が簡単（`LOG_DIR` でグルーピング）。
- fail-fast:
  - initial pose / 制御要求 / 必須トピックの成立がタイムアウトした時点で打ち切り、次の domain / submit へ進める。
  - スタック/衝突等の復帰不能状態は早期終了できる（打ち切り理由を結果JSONとログへ残す）。
- `Ctrl+C` / `TERM` で `docker compose down` により回収できること（運用上 Autoware は必ず落ちる前提）。
  - rosbag は compose 側の `stop_signal: SIGINT` + `stop_grace_period` により自然終了させる。
- 参加者ローカル検証は「手元の `tar.gz` を `--submit` で指定」して完結する。
- 運営向け（他チーム提出物の収集/一括評価）は別スクリプト系として分離できる。

### 3.2 非機能要件

- 失敗時の原因が追える（標準出力だけでなくファイルに残す）。
- GPU/CPU を切り替え可能（`.env` の `COMPOSE_FILE` で制御）。
- 成果物は「ディレクトリ丸ごと回収/アップロード」しやすい構成（`output/<run_id>/...` を基本単位）。
- 運用性: 起動・停止・後片付けが簡単（`docker compose down` で確実に止まる）。
- 可観測性: 失敗したステップ（build/起動/初期姿勢/トピック/結果生成等）がログから一目で分かる。
- セキュリティ/権限: 参加者が他チーム提出物を参照・取得できない（運営用導線は分離）。

---

### 3.3 メモ（設計メモ反映）

#### 3.3.1 評価フロー（基本形）

単独走行のフローは大筋このままで成立するが、途中で異常が起きた場合に無駄な実行を減らす（fail-fast）分岐を増やしたい。

- シミュレータ起動（AWSIM）
- AWSIM の準備確認（/clock 等の到達確認）
- Autoware 起動（domain id ごと）
- 初期姿勢設定（initial pose）
- 開始指示（manual → auto 相当）
- トピック/状態チェック（制御系トピックが来ているか等）
- 任意: rosbag2 記録、画面キャプチャ
- 終了待ち → 結果生成（result json 等）
- 成果物整理（必要なら圧縮）
- クリーンアップ（最終的に `docker compose down` で回収）

#### 3.3.2 multi-submit 時の前提（複数車両の想定）

- `./docker_build_run.bash all` の `--submit` 指定順に domain id を `1..4` へ固定割当（最大4件）。
- 提出物はホストへ展開しない（作業ツリーを汚さない）。Docker 内で submit ごとの eval image をビルドして切替える。

#### 3.3.3 改善したい点（fail-fast とログ）

- initial pose / 制御要求 / トピックチェックが成立しない時点で評価を打ち切り、次の domain / submit に進む。
- 「どこまで進んだか」がログだけで追えるようにする（状態変数の新設は最終手段、まずはログ整備で十分）。
- 失敗時に「何を見ればよいか」を `output/docker/.../docker_build.log` / `output/docker/.../docker_run.log` と `output/<run_id>/d<domain_id>/autoware.log` の先頭に明示する。

#### 3.3.4 起動順序の補足（将来の最適化候補）

- multi-domain/multi-submit で、シミュレータを毎回立ち上げ直すか、1回だけ起動して domain ごとに Autoware を切り替えるかは運用/安定性で選ぶ。
  - 現状は安定性優先で「domainごとに起動→停止」を基本にし、必要に応じて「起動1回」に最適化する。

#### 3.3.5 ログ/成果物の考え方（自己診断できる運用）

参加者の問い合わせで多いのは以下:

- ビルドが失敗している
- 重要トピックが来ていない / 制御が入っていない
- 環境差（GPU/driver/compose差分等）で動かない

運営が個別に調査するのではなく、参加者が自己診断できる形に寄せるために:

- 実行時の標準出力・標準エラーは可能な限りファイルへ落とし、成果物として回収する
- `output/docker/...`（ホスト側の統合ログ）と `output/<run_id>/...`（実行成果物）の両方が揃う前提で設計する

#### 3.3.6 成果物アップロード方針（Web側の設計メモ）

現状のように「特定ファイルだけ列挙してアップロード」は漏れや設計硬直化を招くため:

- `output/<run_id>/` など「成果物ディレクトリ丸ごとアップロード」を基本にし、Web側で参照対象を選ぶ設計が望ましい

#### 3.3.7 Docker運用（ホスト側オーケストレーション）

- rocker 内で作業するより、ホスト側から `docker compose` / `make` / `docker_build_run.bash` で完結した方が運用が楽
- `docker compose down` で確実に落とせる運用に寄せる
- rosbag はシグナル（SIGINT）で安全に止めたい（途中ぶった切りを避ける）

#### 3.3.8 複数台（対戦）への拡張方針

- まずは「提出物を集めて、運営が同一環境で最大4台を走らせる」方式が現実的

#### 3.3.9 技術案: Autoware複数 + Simulator共通

- 技術的には、`ROS_DOMAIN_ID` 等で分離して複数Autowareを起動し、シミュレータは1つで共通運用する構成が候補
- ただし安定性/切り分けの観点で「domainごとに起動→停止」から開始し、運用が固まったら最適化する

#### 3.3.10 提出物取り込み（権限/運営用の分離）

- 参加者権限では他チーム提出物をダウンロードできない前提
- 運営用スクリプト（提出物収集/一括評価）と参加者ローカル検証（手元の `tar.gz` を `--submit` で指定）を分離する

#### 3.3.11 早期終了（スタック/衝突）

- 壁衝突後に復帰できず、制限時間まで無駄に実行されるケースが多い
- シミュレータ側で衝突・スタックを検知して打ち切ることで、コストと待ち時間を削減できる
  - 判定候補:
    - 衝突状態が連続で N 秒以上継続
    - 速度が閾値未満の状態が N 秒以上継続（開始後のみ有効）
  - 完了条件:
    - 打ち切り理由が結果JSONとログの両方に残る

#### 3.3.12 rosbag配布（容量対策）

- センサ（特にカメラ）込みのrosbag配布は容量が爆発しやすい
- 配布用rosbagはトピックフィルタで軽量化する方針が妥当

#### 3.3.13 描画（CPU環境・参加者体験）

- CPUノート環境でも `docker compose` 経由で描画ありで動作する可能性がある（参加者体験の改善余地）
- rocker 経由では描画が破綻（ピンク表示）する現象があり、compose運用を優先する価値が高い
- デフォルトは描画あり（必要に応じてheadlessへ切替）

## 4. 出力・ログ仕様（最終形）

### 4.1 Run 構造

```
output/
  <run_id>/                      # 例: 20260128-145007
    awsim.log
    d1/
      autoware.log
      ros/...
      capture/...
      result-details.json (or d1-result-details.json 等)
    d2/...
    d3/...
    d4/...
  <run_id>/<run_group>/          # 例: 20260128-145007/car1 (複数提出物をまとめる場合)
    d1/...
    d2/...
    d3/...
    d4/...
  latest/...                    # 最新結果への固定参照（d1/d2... のリンク）
  docker/
    <event_id>/docker_build.log
    <event_id>/docker_run.log
  latest/docker_build.log -> <event_id>/docker_build-*.log
  latest/docker_run.log -> <event_id>/docker_run-*.log
```

### 4.2 ログの基本方針

- 「外から見て原因がわかる」が最優先（参加者/運営視点）。
- build/run/eval の各段階で、コマンド・引数・環境（GPU/DOMAIN_IDS 等）をログ先頭に残す。

---

## 5. docker_build_run.bash の仕様（案）

> 現時点では `docker_build_run.bash` は未作成。設計アイデアとして保持し、実装は
> `run_parallel_submissions.bash` と既存の `run_evaluation.bash` 運用で代替している。

### 5.1 コマンド体系

```
./docker_build_run.bash build [dev|eval] [--submit <tar.gz>]
./docker_build_run.bash eval  [--device auto|gpu|cpu] [--domain-ids 1,2,3,4] [--rosbag] [--capture] [--run-id ID] [--run-group NAME]
./docker_build_run.bash all   [--submit <tar.gz>]... [--device ...] [--rosbag] [--capture] [--run-id ID]
./docker_build_run.bash down  [--remove-orphans]
```

意図:
- `build` は `./docker_build.sh` の薄いラッパ（ログ先頭に付加情報を入れたい）。
- `eval` は `make run-sim-eval` の薄いラッパ（Run ID の表示、ログのまとめ、終了処理統一）。
- `all` で「提出物差し替え → overlay build → eval」を一発で回す。
- `all` の domain id 割当は固定（1つ目の提出物→1、2つ目→2…、最大4）。

### 5.2 実装方針（内部）

- build:
  - `./docker_build.sh <target> [--submit ...]` を呼ぶ（既存を再利用）
- eval:
  - `make eval` を呼ぶ（GPU/CPU は `.env` の `COMPOSE_FILE` で制御）
- down:
  - `docker compose -f docker-compose.yml down --remove-orphans`

### 5.3 終了・中断時の扱い

- `Ctrl+C`（SIGINT）で:
  - 実行中の `make run-sim-eval` に SIGINT を伝播
  - `docker compose down` を実行
- rosbag は compose 側の `stop_signal: SIGINT` + `stop_grace_period` に依存（スクリプト側での二重停止は最小化）

---

## 6. 実装タスク（PR単位）

1) `docker_build_run.bash` 追加（CLI/usage、build/eval/all/down 実装）  
2) `README.md` に利用例を追記（推奨手順を `docker_build_run.bash` に寄せる）  
3) ログ整備（`output/docker/<event_id>/docker_build.log` / `output/docker/<event_id>/docker_run.log` など、最小でも1ファイルにまとめる）  
4) 動作確認（`--device cpu` で最低限起動、`--device gpu` は環境がある場合のみ）

---

## 7. 仕様の未確定点（確認したい）

- `docker_build_run.bash` は「compose 推奨」に寄せるか
- `DOMAIN_IDS` のデフォルトを `1,2,3,4` にする
- `all` が build の `--no-cache` を常に使うか（eval は重いので切替が必要）
