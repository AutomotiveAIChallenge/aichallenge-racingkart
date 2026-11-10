# aichallenge-racingkart（fork）での作業規則

親の規則: `../CLAUDE.md`。方針の正本: `../docs/ai_dev_policy.md`（§4 層の表、§5 検証、§6 日々の進め方、§7 git 運用）。

## 最初に読む
- `../docs/baseline.md`（ベースライン = タグ `baseline-20261109`、既知課題）
- `../docs/env_map.md`（起動・停止、結果 JSON の場所と書かれるタイミング、再ビルド要否）
- `aichallenge/research_tools/README.md`（計測ツールと results の一覧）

## 変更前に「層」を宣言する（方針 §4）
変更に着手する前に、どの層の変更かを報告に書く。層ごとの自動検証・走行数・レビュー・人間の関与は次の表のとおり。

| 層 | 対象ファイル（例） | 自動検証 | AWSIM 走行数（暫定） | AIレビュー | 私の関与 |
|---|---|---|---|---|---|
| L0 解析・文書 | `docs/*.md`、`research_tools/` の**新規**解析スクリプト、テスト | ruff + pytest（コンテナ内） | 0 | 無し | 出力と表を見る。マージは私 |
| L1 シム設定 | `simulator_scripts/*.sh`、`Makefile` | shellcheck + 1回起動 | 1 | 無し | 評価条件が変わる差分のみ読む |
| L2 パラメータ | `config/config.yaml` の `Q`、`QN`、`ay_max`、`v_max`、`ref_vel*.yaml` | 値域検査 + invariant_check | 変更後3（ベースライン分布と比較） | 無し | 結果表を見て採否＋**物理的な意味を1行書く**。`config.yaml` の diff は短いので毎回全行見る |
| L3 経路計画 | `env/*/traj_*.csv`、`reference_path.py` | 経路の静的検査 + invariant_check | 3 | 別セッション1本 | 結果表＋経路の図 |
| **LM 計測器** | `research_tools/bench.sh`、`lap_monitor.py`、`summarize.py`、`invariant_check.py`、`check_claims.py`、`run_queue.sh`、`test_mpc_core.py`、`experiments/*/plan.md` の雛形 | 既存3結果（`bench_baseline/fixed_bc_only/fixed_all.txt`）を固定入力にした `test_research_tools.py`（集計値が1桁も変わらない） | 0 | 無し | **全文 diff を読み `approvals.md` に1行**。閾値・集計方法の変更は決定記録「規則変更」必須 |
| L4 制御則・安全ガード | `core/MPC.py`、`mpc_controller.py`、`spatial_bicycle_models.py`、`config.yaml` の安全値 | 単体テスト + invariant_check | A/B 交互に各3（計6） | 反証専用1本（別モデル推奨） | **全文 diff を読む**。`approvals.md` に1行 |
| L5 実車固有・提出物・規則 | `vehicle/.calibration/*/imu_bias.yaml`、`accel_map/brake_map.csv`、`reference.launch.xml` の実車分岐、提出 tar、`CLAUDE.md`、採用基準 | 実車前チェックリスト | シムでは検証できない部分あり | 反証専用1本 | **全文 diff＋翌日の自分で再読**。規則の変更は決定記録に残す |

- 安全に直結する値は `config.yaml` にあっても **L4**: `delta_max_deg` 32、`steer_rate_max` 0.35、`a_min` −1.6 / `a_max` 0.7、`width` 2.30、`steering_tire_angle_gain_var` 1.639。タスクカードの「変えてよい」欄に明記されていない限り触らない。
- 複数の層にまたがる変更は分ける。分けられないときは高い方の層として扱い、カードの「変えてよい」欄に両方を書いてから始める。関係ない変更（整形・リネーム・ついでの修正）を混ぜない。
- 2週間の例外（2026-11-23 まで）: `CLAUDE.md`・採用基準・層の表の変更は L0 扱い。ただし `../docs/decisions/` に「規則変更」として1行残す。

## 変えてはいけないもの
- `[mpc_stats]` の出力形式（`core/MPC.py`）。results の集計がこの形式に依存する。
- `/awsim/status` の購読（`research_tools/lap_monitor.py`）。
- 公式の責務分離（`docs/interface/evaluation-interface.md` §4）: `/admin/awsim/start` への pub/sub と `/awsim/state` の消費は運営側ノードの役割。提出コードから行わない。`aichallenge/workspace/src/aichallenge_system/` 配下は触らない。
- 公式の評価条件（`simulator_scripts/eval.sh`、`s2r-final.sh`、`parallel.sh`）。開発用の変更は `dev.sh` か、そのコピーで行う（L1）。

## シミュレーション（方針 §5・§6）
- 走行は必ず `aichallenge/research_tools/bench.sh <label> [秒]` 経由。`make dev` を直接叩かない（例外: `env_map.md` に書く確認作業で、ユーザーのカードにあるとき）。
- 1走行は約5分。1日の予算は日中2時間（ユーザー決定、`../docs/decisions/0000`）。カードの「打ち切り」を超えない。
- `research_tools/.bench.lock` があれば、ビルド・pytest・重い解析は終わるまで待つ（`wait_bench.sh`、作成後）。
- 結果 JSON（`aichallenge/d*-result-details.json`、`result-summary.json`）は `make down` のときに1回だけ書かれ、次の走行で上書きされる。走行後すぐ `results/` にコピーする（bench.sh 拡張後は自動）。
- `config.yaml` と `MPC.py` の変更は再ビルド不要。`make dev` の再起動で反映される。走行中のノードには反映されない。

## git（fork）
- `origin` = fork（atsuki-ichikawa）、`upstream` = 公式。ブランチ名: `exp/T-xxxx`（実験。タスクカードの ID と同じ）、`fix/`、`tools/`。
- 実行禁止: `git merge`、`git push origin main`、`git tag submit/*`、`git push --force`、`upstream` への push、`results/` 配下の削除・rename。
- PR の作成とマージはユーザーが GitHub で行う。AI はブランチの push まで。
- 1変更1コミット。メッセージの先頭に層とカード ID を書く（例: `L2 T-0007: ay_max 6.5→7.5`）。

## 完了の報告
- 書くこと: results のディレクトリ名（ファイル名）、commit、宣言した層、走行数。
- 書かないこと: 結果の解釈（速い・改善・良くなった）。解釈は文書セッションが集計スクリプトの出力から別に書く。
- 「完了」と言う前の確認: `test.sh`（作成後）が通る／diff に関係ない変更が無い／カードの「やらないこと」に触れていない／安全値を変えていない。

## テストと lint
- Python は `ruff`（ホストにある）。MPC の単体テストはコンテナ内の pytest（`research_tools/test.sh`、作成後。ホストに pytest と osqp は無い）。
