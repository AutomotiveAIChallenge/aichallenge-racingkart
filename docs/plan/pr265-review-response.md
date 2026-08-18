# PR #265 レビュー対応計画（修正内容とテスト）

## 前提

- 対象 PR: [#265 fix: add vehicle setup preflight and runtime checks](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265)
- 作業ブランチ: `fix/driver-shutdown-and-setup-check`（base: `experiment` @ `f9faaa4`）
- レビューコメント: Codex 1 件 + taikitanaka3 のインライン 5 件 / 軽微な指摘 5 件（一部は返信で決着済み）
- テスト環境の区別
  - **dev PC** — 車両ハードウェア（CAN / VCU / GNSS）なし。イメージ `ghcr.io/tier4/racing_kart_interface:latest-experiment` と `aichallenge-2025-dev:latest` はローカルにある
  - **実車** — ECU 上で `make autoware-driver-zenoh-rosbag` / `make down` が実行できる環境

> この文書は `docs/plan/` 配下（`docs/README.md` の規約ではコミット対象外）だが、実車作業の共有物として例外的にコミットしている。対応完了後は削除してよい。

## サマリ

| # | コメント | 判定 | 変更 | テスト環境 |
| --- | --- | --- | --- | --- |
| 1 | `run_driver.bash` の EXIT trap が `compose down` で不発火 | 実バグ | trap 分離 + `set -m` + bg/`wait` + pgid 宛 `kill -INT` | dev PC（機構）+ 実車（最終） |
| 2 | セクション番号がドキュメントと実出力でずれ | 要修正 | `print_section()` カウンタ化 + md / README 同期 | dev PC で完結 |
| 3 | VEHICLE_ID→port が `run_zenoh.bash` と重複 | 要修正 | `vehicle/vehicle_ports.sh` に集約 | dev PC（機構）+ 実車（疎通） |
| 4 | runtime トピックが false fail し得る | 妥当 | `ROS_TOPIC_RETRY` 追加 | ロジックのみ dev PC / 実証は実車 |
| 5 | `docker compose ps` のインライン再実装 | 要修正 | `compose_running_services()` に集約 | dev PC で完結 |
| 6 | `check_docker` に空行だけの追加 | 要修正 | 削除 | dev PC で完結（実行不要） |
| 7 | `read_env_value` が `export KEY=value` を拾えない | 任意 | sed で正規化 | dev PC で完結 |
| 8 | NavPVT `flags` の完全一致判定 | 返信で決着 | ビットマスク判定（任意） | ロジックは dev PC / 回帰は実車 |
| 9 | `setup-vehicle` の `--phase` 既定値 | 合意・クローズ | コード変更なし / README 1 行 | 不要 |

---

## 1. run_driver.bash のシグナル処理（P1）

- 指摘: [discussion_r3654260977](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654260977) / [discussion_r3654264097](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654264097)
- 対象: `vehicle/run_driver.bash`

### 診断（確認済み）

`docker-compose.yml` の `driver` は `entrypoint: []` + `command: ["bash","-lc","exec /vehicle/run_driver.bash ..."]` なので `run_driver.bash` が PID 1 になる。PID 1 はハンドラ未設定のシグナルをカーネルに無視されるため、EXIT trap しか無い現状では SIGTERM が効かず `stop_grace_period: 10s` 経過後に SIGKILL される。結果、実行中に root が作る `${ROS_LOG_DIR}` 配下のファイルがホスト側で root 所有のまま残る。

`driver.log` は 2 回目の `fix_ownership` で inode が chown 済みなので追記のみ = 影響なし。

### 提案パッチからの修正点

レビューでの提案（`kill -TERM "${child_pid}"`）だけでは不足する。起動チェーンを実イメージで確認した結果:

```
run_driver.bash -> /entrypoint.sh -> /workspace/utils/run.bash -> ros2 launch
```

**どこも `exec` を使っていない**ため、直接の子（`/entrypoint.sh` の bash）にしかシグナルが届かず、その bash は PID 1 ではないので default 動作で即死し `ros2 launch` が孤児化する。プロセスグループ宛に送る必要がある。

### リポジトリ内の既存パターン

コンテナ PID 1 になるスクリプトの現状。**`run_driver.bash` だけがハウスパターンを踏んでいない**。

| service | PID 1 スクリプト | シグナル処理 | grace |
| --- | --- | --- | --- |
| `autoware` | `aichallenge/run_autoware.bash:38-42` | `set -m` + bg + `trap 'kill -INT $!' TERM INT` + `while kill -0 $!; do wait; done` | 10s |
| `rosbag` | `aichallenge/utils/record_all_rosbag.bash:29-78` | `setsid` + bg + `trap ... EXIT` / `trap '...; exit 0' SIGINT SIGTERM`、pgid 宛 `kill -INT -- "-${PID}"` | 60s |
| `driver` | `vehicle/run_driver.bash:14` | **EXIT trap のみ** ← 本項の対象 | 10s |
| `zenoh` | `vehicle/run_zenoh.bash` | **trap なし**（`while true` ループ） | 10s |

非 PID 1 のヘルパーも同形（`aichallenge/utils/record_rosbag.bash:18`、`vehicle/record_rosbag.bash:79`）。

`driver` は「終了時の後処理が必要」= `record_all_rosbag.bash` 型、かつ exec なしチェーンのため pgid 宛送信が必須なので、両者を合わせた形にする。

- 送るシグナルは `SIGTERM` ではなく **`SIGINT`**。既存 2 実装と揃え、`ros2 launch` の graceful shutdown 経路に乗せる
- `set -m` は「子を独立 pgid にする」と「非対話 bash が背景ジョブの SIGINT を `SIG_IGN` にするのを防ぐ」の両方を満たす（後者は `run_autoware.bash:38` にコメントで根拠が書かれている）

### 変更

```bash
child_pid=""

fix_ownership() {
    chown -R "${target_uid}:${target_gid}" "${out_dir}" 2>/dev/null || true
}

finish() {
    if [ -n "${child_pid}" ] && kill -0 "${child_pid}" 2>/dev/null; then
        # entrypoint.sh -> run.bash -> ros2 launch はどこも exec しないので pgid 宛に送る
        kill -INT -- "-${child_pid}" 2>/dev/null || kill -INT "${child_pid}" 2>/dev/null || true
        wait "${child_pid}" 2>/dev/null || true
    fi
    child_pid=""
    fix_ownership
}

trap finish EXIT
trap 'finish; exit 0' SIGINT SIGTERM
...
# set -m: 子を独立 pgid にし、かつ背景ジョブの SIGINT が SIG_IGN になるのを防ぐ
set -m
/entrypoint.sh "${mode}" "${@:4}" &
child_pid=$!
set +m
wait "${child_pid}" || true
child_pid=""
fix_ownership
```

`docs/spec/host-uid-containers.md` の「開始時と終了時に所有者を直す」記述は本修正で事実になるため変更不要。

### テスト（dev PC）

スタブ chain テスト。実イメージ・実 `run_driver.bash`・実 PID 1 構成（`bash -lc "exec ..."`）を plain `docker run` で再現し、`/entrypoint.sh` を「exec しない 2 段ネスト + 実行中に root で `${ROS_LOG_DIR}` にファイルを作り続ける」スタブに差し替える。

`docker stop` 後にアサートする 2 点:

1. リーフが `SIGINT RECEIVED` を出力 → exec なしの入れ子を越えて pgid 宛シグナルが届いた証拠（提案パッチとの差分部分）
2. 実行中に作られた `${ROS_LOG_DIR}/node-*.log` が `HOST_UID:HOST_GID` 所有 → trap が実際に走り chown された証拠（変更の目的そのもの）

注意: compose 経由（`docker compose up driver`）でテストすると `/dev/vcu:/dev/vcu` と `/dev/gnss:/dev/gnss` の bind mount 元を docker が自動作成し、**ホストに root 所有の空ディレクトリができる**。plain `docker run` で `./vehicle` と出力先だけマウントして回避する。

dev PC で確認できないこと: 実 `ros2 launch racing_kart_vehicle` が pgid 宛 SIGINT で落ちるか。スタブはプロセスの入れ子構造しか再現しない。

### テスト（実車）

- `make autoware-driver-zenoh-rosbag` → `make down` → `ls -ln output/<ts>/d1/ros/log` がホストユーザー所有
- `driver.log` の末尾に ROS 側の shutdown ログが残っている
- 停止後に再起動でき、CAN/DDS 初期化がやり直せる

---

## 2. セクション番号 + ドキュメント同期

- 指摘: [discussion_r3654260981](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654260981)
- 対象: `vehicle/setup_check.sh`, `vehicle/setup_check.md`, `vehicle/README.md`

### 現状

指摘より状況は悪く、`--phase all` では番号が**重複**する。`check_runtime_hardware` が `1.`、`check_runtime_docker_services` が `3.` のため、実出力は `1,2,3,1,3,4,5,6,7` になる。

`setup_check.md` には番号以外の齟齬もある。

- 見出しが「チェック項目（5 段階）」のまま
- `--phase` が未記載
- 出力例に `Phase:` 行がない
- 出力例に**スクリプトに存在しないチェック**が残っている（`User t4tanaka in dialout group`、`Avoid direct sunlight exposure for batteries`）
- `Required compose services are running` が「3. Docker・環境確認」に置かれているが実際は runtime セクション

`vehicle/README.md` も preflight 相当の 1〜5 のみで、runtime 項目と `--phase` が未記載。

### 変更

固定番号をやめてカウンタ方式にする。

```bash
SECTION_INDEX=0
print_section() {
    SECTION_INDEX=$((SECTION_INDEX + 1))
    log "${INFO} ${SECTION_INDEX}. $1"
    log "----------------------------------------"
}
```

各 `check_*` 先頭 2 行をこれに置換すれば、どの phase でも 1 から連番になる。あわせて `setup_check.md` / `vehicle/README.md` を preflight / runtime の 2 フェーズ構成に書き換え、`--phase` の使用例を追記し、実在しないチェックの記述を削除する。`vehicle/README.md` には #9 の「`make setup-vehicle` はスタック起動中に実行する」の 1 行も含める。

### テスト（dev PC）

- `--phase preflight` / `--phase runtime` / 引数なし の 3 通りを実行し、**各 phase で 1 から連番・重複なし**を確認。runtime は dev PC では全 fail するが番号の確認には十分
- ドキュメントの出力例と実出力を突き合わせ

副作用: `--phase preflight` は `.env` の `VEHICLE_ID` を読んで **本番 zenoh サーバへ TCP 接続を試みる**（他に `ping 8.8.8.8` と DNS 解決）。害はないが外向き通信が出る。

実車確認は不要。

---

## 3. VEHICLE_ID→port の共有化

- 指摘: [discussion_r3654260985](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654260985)
- 対象: `vehicle/setup_check.sh`, `vehicle/run_zenoh.bash`, 新規 `vehicle/vehicle_ports.sh`

### 変更

`zenoh_port_for_vehicle_id()`（A1〜A8 → 7448-7454）と hostname→VEHICLE_ID のマッピングを `vehicle/vehicle_ports.sh` に集約し、両方から `source "$(dirname "$0")/vehicle_ports.sh"` する。`vehicle/` は zenoh コンテナ（`x-autoware-base`）に `./vehicle:/vehicle` でマウントされているため、ホスト実行の `setup_check.sh` とコンテナ内実行の `run_zenoh.bash` の両方で同じ相対パスが通る。

### テスト（dev PC）

- ホスト側: A1〜A8 全 ID で期待ポートが返り、未知 ID で非 0 終了する
- コンテナ側の source パス: `docker run --rm -v ./vehicle:/vehicle aichallenge-2025-dev bash -c 'source /vehicle/vehicle_ports.sh && zenoh_port_for_vehicle_id A6'` → `7450`
- `run_zenoh.bash` を不正 ID で叩き、従来同様 `Invalid VEHICLE_ID` + exit 1

### テスト（実車）

`make zenoh` で bridge が正しいポートに接続（`docker compose logs zenoh` と setup_check の Zenoh 疎通 pass）。

---

## 4. runtime トピックの false fail 対策

- 指摘: [pullrequestreview-4783450754](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#pullrequestreview-4783450754)（軽微な指摘）
- 対象: `vehicle/setup_check.sh`

### 変更

`Makefile` の `sleep 15` 直後に runtime チェックが走るため、autoware 側トピック（特に `/control/command/*`）が `ROS_TOPIC_TIMEOUT_SEC=4` に間に合わず false fail し得る。`ROS_TOPIC_RETRY`（既定 2）を追加し `check_ros_topic_once` をリトライループ化する。`--help` と ENVIRONMENT 節にも追記。

最悪ケースは 13 トピック × 4s × 2 ≒ 100 秒だが全滅時のみ。`sleep 15` の延長より、正常時のコストがゼロなリトライを採る。

### テスト（dev PC・限定的）

サービス未起動時は `is_compose_service_running` で早期 fail するためリトライループに入らない。ループ回数を実測するには `docker` を PATH shim でモックする必要がある（`ps` は running を返し `exec` は失敗させ、経過時間が `RETRY × TIMEOUT` になるか）。

### テスト（実車）

false fail の再現に autoware 起動が必要なため**実証は実車のみ**。`make autoware-driver-zenoh-rosbag` を複数回回して runtime チェックが安定 pass するか、特に `/control/command/control_cmd` / `actuation_cmd` を確認。

---

## 5. compose ps ヘルパーの集約

- 指摘: [pullrequestreview-4783450754](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#pullrequestreview-4783450754)（軽微な指摘）
- 対象: `vehicle/setup_check.sh`

### 変更

`compose_running_services()` を切り出し、`is_compose_service_running` / `check_runtime_docker_services` / `check_gnss_rtk_status` の 3 箇所から使う。`check_runtime_docker_services` は missing 一覧を出すため生のリストが必要なので、bool ヘルパー 1 本に統一するのではなくリスト返却ヘルパーを挟む。これにより「inspect 失敗」と「未起動」のメッセージ区別も維持できる。

### テスト（dev PC）

`--phase runtime` を次の 2 条件で実行し、従来と同じ文言が出ることを確認。

1. docker 起動中・compose サービス未起動 → `not running`
2. docker daemon 停止 → `Cannot inspect`

実車不要。

---

## 6. `check_docker` の空行削除

- 指摘: [pullrequestreview-4783450754](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#pullrequestreview-4783450754)（軽微な指摘）
- 対象: `vehicle/setup_check.sh`

`check_docker` 内の閉じ `fi` 直前にある空行だけの追加（diff 汚れ）を削除する。テストは `git diff` の目視 + pre-commit のみ。スクリプト実行は不要。

---

## 7. `read_env_value` の `export` 対応（任意）

- 指摘: [pullrequestreview-4783450754](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#pullrequestreview-4783450754)（軽微な指摘）
- 対象: `vehicle/setup_check.sh`

現状 `awk -F=` の `$1 == key` 判定なので `export VEHICLE_ID=A6` や先頭空白付きの行を拾えない。ただし `.env` / `.env.example` はいずれも素の `VEHICLE_ID=...` なので現状でも動く（レビュアーも同意）。入れるなら awk 前段で正規化するだけ。

```bash
sed -E 's/^[[:space:]]*(export[[:space:]]+)?//' "${env_file}" | awk -F= ...
```

### テスト（dev PC）

テスト用の一時ディレクトリに `.env` を置き、`KEY=value` / `export KEY=value` / 先頭空白 / 引用符付き / コメント行の各形式で期待値が返るか確認する。**リポジトリの `.env` は書き換えない**。実車不要。

---

## 8. NavPVT flags のビットマスク判定（任意 / 返信で決着）

- 指摘: [discussion_r3654023917](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654023917)（Codex P1）/ [discussion_r3654260979](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654260979)
- 返信: F9R は heading 無効・PSM 無効のため 131 固定で問題なし。レビュアーも決着済みとしている

クローズ可。入れる場合の変更は以下で、**現行 F9R 設定では 131 / 67 と判定結果が完全に一致**する（受信機設定を変えたときだけ効く保険）。

```bash
fix_ok=$((flags & 1))
carr_soln=$(((flags >> 6) & 3))
# fix_ok=1 && carr_soln=2 -> RTK fixed / carr_soln=1 -> RTK float / それ以外 -> fail
```

ログには `flags` の生値をそのまま出す。

### テスト

- dev PC: 判定部分を関数に切り出し、131 / 67 / 163 / 195 / 0 / 空文字 の入力で期待通り pass / warn / fail になるか（純粋な算術なので単体テスト可）
- 実車: 実 NavPVT で従来どおり pass する回帰確認のみ

---

## 9. `setup-vehicle` の `--phase` 既定値（クローズ）

- 指摘: [discussion_r3654264101](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/265#discussion_r3654264101)
- 「起動中の車両に対するフルチェック」で認識合意済み。レビュアーも「現状のままで問題ない、クローズで大丈夫」と回答

コード変更なし。#2 のドキュメント更新に「`make setup-vehicle` はスタック起動中に実行する」の 1 行を含めるだけ。テスト不要。

---

## 共通

すべての変更後に pre-commit を通す。`shellcheck` / `shfmt -w -s -i=4` / `end-of-file-fixer` / `mixed-line-ending` が対象。

```bash
pre-commit run --files vehicle/run_driver.bash vehicle/setup_check.sh vehicle/vehicle_ports.sh \
  vehicle/run_zenoh.bash vehicle/setup_check.md vehicle/README.md
```

既存の `SC2329`（`log_only`）は本 PR 以前からの警告なので対象外。

## dev PC 検証結果

実装後に dev PC で実施した検証。すべて pass。

| 対象 | 内容 | 結果 |
| --- | --- | --- |
| #1 | スタブ chain テスト（実イメージ・実 `run_driver.bash`・PID 1 構成 + `/entrypoint.sh` 差し替え） | リーフが SIGINT 受信 / graceful shutdown 完了後に親が終了 / **停止前 `0:0` → 停止後 出力 18 件すべて `1000:1000`** |
| #1 | pgid 分離 | チェーン 3 プロセスが `pgid=13`（PID 1 の pgid=1 と別）→ group 宛送信が届いた |
| #2 | セクション番号 | preflight 1-5 / runtime 1-4 / all 1-9、いずれも連番・重複なし（修正前 all は `1,2,3,1,3,4,5,6,7`） |
| #2 | ドキュメント同期 | `setup_check.md` の出力例のセクション見出しが実出力と完全一致（preflight 5 件 / runtime 4 件を機械的に diff） |
| #4 | リトライ回数 | `docker` を PATH shim にして `compose exec` の呼び出し回数を計測。`ROS_TOPIC_RETRY=1/2/3` で 13 トピック × RETRY = 13/26/39 回に正確に一致（off-by-one なし） |
| #4 | リトライの効果 | 1 回目失敗・2 回目成功のモックで 13/13 トピックが pass。対照として `RETRY=1` では同条件で 7 件が fail に落ちる → リトライが false fail を救っていることを確認 |
| #3 | `vehicle_ports.sh` | A1-A8 全 ID が元の `run_zenoh.bash` と同一ポートを返す / 不正 ID と空文字で `rc=1` / hostname 4 種を解決 |
| #3 | コンテナ内 source | `aichallenge-2025-dev` 内で `/vehicle/vehicle_ports.sh` を source して `A6 -> 7450` |
| #3 | `run_zenoh.bash` | 不正 ID で `Invalid VEHICLE_ID: A4 (valid: ...)` + exit 1 |
| #5 | エラー分岐の区別 | サービス未起動 → `not running` / `docker compose` 失敗（PATH shim）→ `Cannot inspect` / `docker ps` 失敗 → `Docker daemon not accessible` |
| #7 | `read_env_value` | `KEY=value` / `export KEY=value` / 先頭空白 / 引用符 / コメント行無視 / 前方一致除外 / 重複時は最後が勝つ の 12 ケース |
| 全体 | pre-commit（shellcheck / shfmt / end-of-file-fixer / mixed-line-ending） | 全 hook pass |
| 全体 | exit code | `--phase all` で fail あり → 1、不正 phase → 1 |

## 実車での確認チェックリスト

dev PC で検証できず、実車でのみ確認できる項目。**すべて検証完了**（2026-07-29、A6 / ECU-RK-06、`06555e4`）。

- [x] **#1** `make autoware-driver-zenoh-rosbag` → `make down` → `output/<ts>/d1/ros/log` がホストユーザー所有
- [x] **#1** `driver.log` に ROS 側の shutdown ログが残る / 停止後に再起動できる
- [x] **#4** runtime トピックチェックが安定 pass（特に `/control/command/control_cmd` / `actuation_cmd`）
- [x] **#3** `make zenoh` で Zenoh が正しいポートに接続し、setup_check の疎通が pass
- [ ] **#8** 実 NavPVT で GNSS チェックが従来どおり pass（ビットマスク化を入れた場合のみ）→ 本 PR ではビットマスク化を入れていないため対象外

## 実車検証結果

2026-07-29 に A6（ECU-RK-06）で実施。対象コミットは `06555e4`。すべて pass。

| 対象 | 内容 | 結果 |
| --- | --- | --- |
| #1 | `make down` 後のログ所有者 | `output/<ts>` 配下 43/44 が `1000:1000`。2 回の up/down で同一結果（再現性あり） |
| #1 | 修正前との対比 | 同一実車の旧ブランチ実行分（09:00 / 09:02）は root 所有ファイルが **261 件**残存 → PR ブランチでは **0 件** |
| #1 | `driver.log` の shutdown ログ | `signal_handler(SIGINT/SIGTERM)` / `process has finished cleanly` を 18 行検出。`ntrip_ros.py` は `exit code -2`（SIGINT）で、INT がリーフまで到達していることを確認 |
| #1 | 停止後の再起動 | `make down` → `make autoware-driver-zenoh-rosbag` が rc=0 で成功 |
| #2 | セクション番号 | preflight 1-5 / runtime 1-4 が連番・重複なし |
| #2 | ドキュメント同期 | 実出力のセクション見出しが `setup_check.md` と完全一致 |
| #4 | runtime トピック | 4 回連続実行で 0 fail。13 トピック全 pass（`/control/command/control_cmd` / `actuation_cmd` を含む） |
| #3 | Zenoh 接続先 | `zenoh-bridge-ros2dds` が `13.231.141.103:7450` に ESTABLISHED（A6 → 7450） |
| #3 | `vehicle_ports.sh` | 実車上で `A6 -> 7450` / `ECU-RK-06 -> A6` / 不正 ID・空文字で `rc=1` |
| #5 | preflight | 18 checks / 16 pass / 2 warn / 0 fail、exit 0 |
| #8 | GNSS チェック | 実 NavPVT で `flags=67`（float → warn）と `flags=131`（fixed → pass）の両分岐を確認 |

### 残課題（本 PR のスコープ外）

`output/<ts>` の**トップディレクトリのみ `root:root drwxr-xr-x` のまま**残る。`fix_ownership` の対象が `out_dir=${3}/d${id}`（= `/output/<ts>/d1`）で、親はコンテナが root で `mkdir` するため対象外になるのが原因。

実害はホストユーザーが `output/<ts>` 配下に書き込めず、`rm -rf output/<ts>` に sudo が必要になる点（`touch` が `Permission denied` になることを実測）。レビュー指摘の対象（`output/<ts>/d1/ros/log`）は満たしているため本 PR では対応しない。
