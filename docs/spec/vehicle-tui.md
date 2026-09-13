# 車両 PC 操作 TUI（vehicle console）

> 仕様ドキュメント（現仕様の正）。文書運用方針は [docs/README.md](../README.md) を参照。

走行枠のあいだ、車両 PC（ECU）上で行う操作を 1 つの TUI に集約する設計。
既存の `make` ターゲットと `setup_check.sh` を**呼ぶだけ**に徹し、
順序の提示と状態の可視化だけを新しく担う。

## 背景と課題

走行枠ごとの車両側作業は、もともとすべて生のシェルで行われていた。実際の導線は次のとおり。

| # | 場所 | 操作 |
|---|------|------|
| 1 | 手元 | ターミナル① を開く |
| 2 | 手元 | 遠隔操作ツールを起動 |
| 3 | 手元 → 車両 | `ssh` |
| 4 | 車両 | 車両側 zenoh を起動 |
| 5 | 車両 | `cd vehicle` → `download_submission.sh` |
| 6 | 車両 | `cd ..` → `make autoware-build` |
| 7 | 車両 | `make setup-vehicle` |
| 8 | 車両 | `make autoware-driver-zenoh` |
| 9 | 手元 | ターミナル② → `cd remote` → zenoh 接続 |
| 10 | 手元 | RViz 起動 |

ここには次の問題があった。

- **チェックがビルドの後にある。** `Makefile` の `autoware-driver-zenoh-rosbag` は
  「preflight → 起動 → runtime」の順に組まれているのに、実際の導線では
  `make setup-vehicle` がビルドの後（ステップ 7）に来ていた。
  CAN や GNSS/RTK の異常を、数分〜十数分かけた `autoware-build` の**後**に知ることになる。
- **しかもその位置では runtime チェックが必ず落ちる。** `make setup-vehicle` は
  `--phase all` 相当で runtime チェックを含むが、ステップ 7 の時点ではスタックが
  まだ起動していない（起動はステップ 8）。`vehicle/README.md` 自身が
  「停止中に叩くと runtime 系が一斉に fail します」と警告している状態を、
  導線がそのまま踏んでいた。preflight と runtime を別のステップに分ける動機は
  ここにもある。
- **順序を人間が覚えている。** ステップ間の依存はドキュメントとして存在するだけで、
  実行系のどこにも表現されていない。
- **`cd` の往復がある。** `download_submission.sh` は `vehicle/` にあり、`make` はリポジトリルートにある。
- **長時間処理の進捗が見えない。** `make autoware-build` の残り時間もパッケージ数も分からない。
- **失敗が流れて消える。** 端末をそのまま眺めていると、`setup_check.sh` の失敗行は
  後続の出力に押し出されて読めなくなる。

## 対象と非対象

対象は**車両 PC 上の操作だけ**である。

遠隔操作側（joy の中継・車両選択・緊急停止・遠隔 RViz）は
[aichallenge-racingkart-remote](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart-remote)
が担当し、`racing_kart_manager` として実装・仕様化されている。本 spec はそこに触れない。
参加者 joy と遠隔SD joy の優先度解決は `racing_kart_interface` 側（muxer）の責務である。

| 領域 | 担当 |
|------|------|
| 車両 PC 上の準備・起動・片付け | **本 spec（この repo）** |
| joy 中継・車両選択・緊急停止・遠隔 RViz | `aichallenge-racingkart-remote` |
| joy 優先度解決・緊急停止のラッチ | `racing_kart_interface` |
| 全車両の状態監視 | Grafana（`aic-telemetry`） |

## 設計方針

1. **既存の実行系を再実装しない。** TUI は `make` と `setup_check.sh` を
   subprocess で呼ぶだけとする。チェック項目やビルド手順を TUI 側に複製しない。
2. **順序は提示するが強制しない。** ステップは実行順に並べ、前提が未達なら印で示すが、
   実行は妨げない。CAN ハードウェアの無い開発機では preflight が正当に落ちるし、
   それでも build して Autoware を上げたい場面がある。進めるかどうかの判断は
   オペレータの領分であり、ツールが禁じるべきではない。
   例外は**実行中の同一ステップ**のみで、これは二重起動に実害があるため禁じる。
3. **状態は保存せず検出する。** 実測できるステップの完了はファイルに書かず毎回測る
   （`install/` の存在、`docker compose ps`）。実測できないステップ（チェックの合否は
   終了コードにしか現れない）はプロセス内でそのセッションの結果を覚える。
   いずれの場合も状態ファイルは作らない。
4. **失敗は流さない。** 失敗行は log とは別の領域に retain し、log が流れても残す。
5. **tmux の中で動かす。** ssh 切断で作業が消えないこと、貼り直せることを前提とする。
6. **純ロジックを分離する。** ステップの前提判定と状態遷移を curses から切り離し、
   端末もプロセスもなしにテストできるようにする。
7. **役割でステップを出し分ける。** 参加者は autoware と提出物だけを触る。
   driver / zenoh / rosbag の起動・停止、提出物のダウンロード、スタック全体の停止は運営の仕事で、
   参加者の画面には出さない（`--role participant|staff`、既定は participant）。
   運営の画面は参加者の並びとは独立の 8 ステップだけの画面で、preflight を含め
   参加者用のステップは一切出さない。参加者と運営で番号は共有しない
   （参加者の 1 と運営の 1 は別のステップを指す）。

## ステップ定義

参加者（`make vehicle-tui`、`--role participant`）

| # | 表示名 | 実行するもの | 前提（助言） | 完了の判定 |
|---|--------|--------------|--------------|------------|
| 1 | `check preflight` | `./setup_check.sh --phase preflight` | なし | 終了コード 0（セッション記憶） |
| 2 | `extract` | `make submission-extract`（`vehicle/.submissions/<id>.zip` を ID とパスワードで展開し `src/aichallenge_submit/` を入れ替える） | 1 | 終了コード 0（セッション記憶） |
| 3 | `build` | `make autoware-build` | 2 | `workspace/install/setup.bash` が存在し `src/` より新しい（実測） |
| 4 | `autoware-vehicle` | `make autoware-vehicle` | 3 | `autoware` が compose 上で running（実測。`driver` / `zenoh` / `rosbag` はサービス行で見せるだけ） |
| 5 | `check runtime` | `./setup_check.sh --phase runtime` | 4 | 終了コード 0（セッション記憶） |
| 6 | `autoware-vehicle down` | `docker compose down autoware` | なし | `autoware` が running でない（実測） |
| 7 | `cleanup` | `make workspace-clean` | なし | `aichallenge/workspace/` が checkout と一致（`git status --porcelain --ignored` が空、実測） |

運営（`make vehicle-tui-staff`、`--role staff`）は参加者の並びとは独立の、次の 8 行だけの画面。

| # | 表示名 | 実行するもの | 前提（助言） | 完了の判定 |
|---|--------|--------------|--------------|------------|
| 1 | `download` | `make download` | なし | 終了コード 0（セッション記憶） |
| 2 | `driver` | `make driver` | なし | `driver` が running（実測） |
| 3 | `zenoh` | `make zenoh` | なし | `zenoh` が running（実測） |
| 4 | `driver down` | `docker compose down driver` | なし | `driver` が running でない（実測） |
| 5 | `zenoh down` | `docker compose down zenoh` | なし | `zenoh` が running でない（実測） |
| 6 | `rosbag` | `make rosbag` | なし | `rosbag` が running（実測） |
| 7 | `rosbag down` | `docker compose down rosbag` | なし | `rosbag` が running でない（実測） |
| 8 | `down all` | `make down` | なし | このリポジトリから compose で起動された running コンテナが 0（全プロジェクト、実測） |

走行枠の流れは、運営が 2・3（`driver` / `zenoh`）で土台を上げ、6（`rosbag`）で記録を始め、
参加者が 1〜5 で autoware を上げて走り、参加者の 6 で autoware を落とし、運営が 7（`rosbag down`）で
記録を閉じて 8（`down all`）で全部を落とし、参加者の 7（`cleanup`）で片付ける、である。
rosbag の 2 行を `down all` の直前に置くのは、記録の開始と終了が走行枠の前後に来る操作で、
土台の上げ下げとは使う場面が違うからである。
Autoware を入れ替えるときは参加者の 6 → 4 と辿る。土台のうち 1 サービスだけを入れ替えたいときは、運営がそのサービスの
down → up と辿る（`driver` は 4 → 2、`zenoh` は 5 → 3、`rosbag` は 7 → 6）。

`driver` / `zenoh` は常時 ON で、落とすのは異常時だけである。`zenoh` を落とすと遠隔からの
監視が切れ、`driver` を落とすと車両が動かなくなる。`rosbag` / `rosbag down` は大会中は押さない。
`down all` は 1 日の終わりに使う。参加者の `autoware-vehicle down` はこの 3 つを触らない。

`driver` / `zenoh` に加えて運営が `rosbag` も上げるのは、`autoware-vehicle` が rosbag を上げないのに
`check runtime` の必須サービスに rosbag が入っているためである。運営が rosbag を上げ忘れると
参加者の 5 が必ず落ちる。

チェックの 2 ステップは `check preflight` / `check runtime` と表示する。
`setup_check.sh` の `--phase` の値をそのまま名前にしているので、画面の名前から
実行されるコマンドが辿れる。内部のステップ ID は `preflight` / `submission` /
`build` / `up` / `runtime` / `autoware_down` / `clean` と運営用の `driver` / `zenoh` / `rosbag` /
`driver_down` / `zenoh_down` / `rosbag_down` / `download` / `teardown` で、表示名とは別である。

### 停止の 2 段と cleanup の責務

停止まわりを 1 つのステップに畳まない。落とす範囲と担当が違うためである。

| ステップ | 役割 | 落ちるもの | 使う場面 |
|----------|------|------------|----------|
| `autoware-vehicle down` | 参加者 | `autoware` のみ（`docker compose down autoware`） | Autoware だけ落とす。`driver` / `zenoh` / `rosbag` は繋いだまま。入れ替えは続けて `autoware-vehicle` |
| `driver down` / `zenoh down` / `rosbag down` | 運営 | それぞれ 1 サービスだけ（`docker compose down driver` / `zenoh` / `rosbag`） | 土台のうち入れ替えたいサービスだけ落とすとき |
| `down all` | 運営 | compose のスタック全部（プロジェクト 1〜4 を含む） | 走行枠の終わり |

`cleanup` はコンテナを触らない。`aichallenge/workspace/` を checkout 直後の状態へ戻すだけである:
提出物で上書きされた `src/aichallenge_submit/` を `git restore --source=HEAD --staged --worktree`
で HEAD に戻し（`git checkout -- <path>` は index から戻すので stage 済みの提出物が残る）、
`git clean -fdx aichallenge/workspace` で `build/` `install/` `log/`（ignored）と
untracked ファイルを消す。これで `git status` に提出物の差分が残らず、
次の `extract` をまっさらな状態から始められる。

git 操作は **このディレクトリに限定する**。`git stash` + `git stash drop` のように
リポジトリ全体へ効かせると、車両 PC 上の `vehicle/zenoh.json5` や Makefile への
ローカル変更まで巻き込む。しかも stash は目的に合っていない: `-u` を付けないと
untracked な提出物ファイルは残り、付けても ignored な `build/` `install/` は残る。

この分担は、提出物を置く側の責務が
**「`aichallenge_submit/` を入れ替えるところまで」**と決まったことから来ている。
`extract_submission.py` は zip に車両別校正値を適用して既存の `aichallenge_submit/` を入れ替える。
それ以外の後片付け（前回のビルド成果物を消す、提出物を消して checkout 状態へ戻す）は
展開側ではなく `cleanup` が持つ。展開側に後片付けを足すと、
「展開したら build も消えた」という副作用を持つことになる。

### 提出物 zip の置き場と形式

運営は走行枠の前に全チームの提出物を `vehicle/.submissions/<id>.zip` として車両 PC に置く。
`aichallenge/workspace/` の外に置くのは、`cleanup` の `git clean -fdx aichallenge/workspace`
で消えないようにするためである（`vehicle/.submissions/` は `.gitignore` 済み）。

zip は **トップレベルが `aichallenge_submit/` だけ**で、**従来の PKZIP 暗号**
（`cd <提出物の親> && zip -er <id>.zip aichallenge_submit`）で作る。
`extract_submission.py` は Python 標準の `zipfile` で復号するため AES 暗号（7-Zip の既定など）は
開けず、その場合は `❌ unsupported zip encryption` として失敗する。

展開は `src/` 直下の一時ディレクトリへ行い、成功してから既存の `aichallenge_submit/` を消して
`os.replace` で入れ替える。パスワード違い・レイアウト違い・破損 zip のいずれでも、
既存の `aichallenge_submit/` は触られない。

### 実測とセッション記憶

展開時の accel/brake map と IMU バイアスの適用、保存元の初回配置、
runtime 再計測時の保存については [車両別校正値](../../vehicle/calibration.md) を参照する。

`build` / `autoware` / `autoware down` / `down all` / `cleanup` は環境から実測する
（`cleanup` は `git status --porcelain --ignored -- aichallenge/workspace` が空か）。
`down all` は `docker compose ps` では判定できない: それは 1 プロジェクトしか見ないが、
`make down` は default と `-p 1..4` の全部を落とす。compose が各コンテナに付ける
`com.docker.compose.project.working_dir` ラベルでこのリポジトリ由来の running コンテナを
数え、0 なら済とする。
実測を優先するため、別のシェルで `make down` された場合も次の観測で反映され、
TUI 内のキャッシュと実態が食い違うことがない。

`preflight` / `extract` / `check runtime` / `download` は実測できない。
合否は終了コードにしか現れず、後からファイルシステムを見て再現できないためである。

`autoware` の完了は `autoware` だけで判定する。`autoware-vehicle` が上げるのは
autoware だけで、`driver` / `zenoh` / `rosbag` は運営がそれぞれのステップで個別に上げた結果である。
4 サービス全部で判定すると、参加者が正しく起動できても運営側の都合で「未完了」に見える。
土台の欠けはサービス行（`stopped: driver zenoh rosbag` など）で分かる。

`extract` は特に注意が要る。`aichallenge/workspace/src/aichallenge_submit/` には
**git 追跡された参加者パッケージが 15 個ある**ため、このディレクトリはチェックアウト時点で
既に空でない。したがって「提出物が存在するか」をディレクトリの中身で判定してはならない。
判定すると常に完了と出て、ステップの存在意義が失われる。

### TUI が呼ぶ起動ターゲットはチェックを内包しない

TUI の起動ステップは `autoware-vehicle`（参加者）と `driver` / `zenoh` / `rosbag`（運営、各 1
サービスずつ）で、どれもチェックを含まない。チェックは `check preflight` / `check runtime` が
独立に担う。

以下は CLI 用に残している `autoware-driver-zenoh-rosbag` の経緯である。
このターゲットは preflight と runtime の両方を内包していた。
TUI は同じチェックを `check preflight` / `check runtime` として独立に持つため、
両方を残すと 2 つとも二重に走る。

**runtime の内包は外した。** runtime フェーズは CAN の 3 秒サンプリング、
GNSS の 8 秒待ち、13 topic ぶんの `docker compose exec` + ROS 環境の source を含み、
健全でも 15〜40 秒、異常時はそれ以上かかる。TUI の設計順（起動 → `check runtime`）を
辿るだけで、状態が変わっていないのに 2 回走ることになる。

**preflight の内包は残した。** スタックが上がる直前にもう一度走るのは安全側に転ぶ。
重複コストは 30 秒程度である。

`CHECK=0` のような抑止フラグは一度実装して外した。`CHECK` は衝突しやすい名前で、
環境変数から継承されていると実車の安全チェックが黙って飛ぶ（実測で確認した）。
防御として `ifneq ($(origin CHECK),command line)` を足す必要が出た時点で、
形が違うという合図である。ターゲットを起動専用と合成用に分けるのが筋だが、
それは本 spec より前から存在する構造の問題であり、別途とする。

## 画面設計

```
[A2] vehicle console [participant]           ↑↓ enter q
running: driver autoware
stopped: zenoh rosbag
1 NG check preflight
2 ?  extract
3 OK build
4 -  autoware-vehicle
5 ?  check runtime
6 -  autoware-vehicle down
7 -  cleanup
-- failures (8) ------------------------------------------
❌ CAN interface can0 not found
❌ VCU directory missing: /dev/vcu
❌ Invalid VEHICLE_ID for Zenoh: A0
-- log ---------------------------------------------------
$ ./setup_check.sh --phase preflight
📊 12 checks: 8 ok, 1 warn, 3 fail
[preflight] exit 1
```

```
[A2] vehicle console [staff]                 ↑↓ enter q
running: driver zenoh
stopped: autoware rosbag
driver image: 2025-09-04  aic commit: bd9c626
1 -  download
2 OK driver  (always on)
3 OK zenoh  (always on)
4 -  driver down  (on faults only)
5 -  zenoh down  (on faults only)
6 -  rosbag  (not during the event)
7 -  rosbag down  (not during the event)
8 -  down all  (end of the day)
-- log ---------------------------------------------------
```

- ヘッダは 1 行で、左端に動かしている車両を `[A2]`、続けて役割を `[participant]` /
  `[staff]` と示し、右端にキー操作を置く。車両は環境変数 `VEHICLE_ID` →
  リポジトリ直下の `.env` の順に読み、取れなければ `[-]` と出す（空欄だと見落とす）。
  hostname からの引き当ては持たない。その対応表は `vehicle_ports.sh` にあり、
  ここへ写すと表が二重になる。
  その下の 2 行がサービス行（`running:` / `stopped:`）。運営の画面は参加者の並びとは別で、`1 - download` / `2 - driver` /
  `3 - zenoh` / `4 - driver down` / `5 - zenoh down` / `6 - rosbag` / `7 - rosbag down` /
  `8 - down all` の 8 行だけ。
- ステップは縦 1 列。印は 2 文字固定（`OK` / `NG` / `>>` 実行中 / `-` 未実行 / `?` 前提未達）。
- 押してよい場面が題名から読み取れないステップは、行末に括弧書きで一言添える。
  `driver` / `zenoh` は `always on`（走行枠の間ずっと上げたまま）、`driver down` /
  `zenoh down` は `on faults only`（異常時だけ）、`rosbag` / `rosbag down` は
  `not during the event`（大会中は触らない）、`down all` は `end of the day`。
  行の幅計算は文字数なので注釈は ASCII で書き、注釈込みでも 47 桁に収まる長さにする。
- サービス行は `running: driver autoware` と `stopped: zenoh rosbag` の 2 行で、`driver` / `autoware` /
  `zenoh` / `rosbag` を `REQUIRED_SERVICES` の順に running / stopped へ振り分けて名前のまま出す。
- 運営の画面だけ、サービス行の下に version 行を 1 行置く
  （`driver image: 2025-09-04  aic commit: bd9c626`）。`driver image` は driver サービスが使う
  `ghcr.io/tier4/racing_kart_interface` イメージの作成日、`aic commit` はこのリポジトリの短縮 hash。
  由来が別リポジトリなので、ラベルはどちらが何か単体で判る形にする。起動時に 1 度だけ採り、
  取れなければ `unknown` と出す（黙って省くと古いイメージのまま走っていることに気付けない）。
  空側は `-`。画面に 1 箇所しか出さないので略さない（頭文字にすると凡例が要る）。
  どのサービスの状態も画面全体で 1 つの事実なので、ステップ行ごとに繰り返さない。
- **failures は log とは別領域**で、log が流れても内容を保つ。残り高さの 2/3 までを使う。
  ステップを実行し直すとクリアされ、常に「今の実行」の失敗を映す。
- 長い行は折り返す。切り詰めると長いパスやコンパイラ出力の末尾が読めなくなる。
- 参加者のステップ 1（`check preflight`）は起動時に自動実行する。運営の画面には preflight が無く、
  起動時の自動実行もしない。
- 最低端末サイズは参加者 47x15、運営 47x17（桁数は参加者・運営共通）。行数の内訳は
  ヘッダ 1 + サービス行 2 + ステップ数（7 / 8）+ version 行（運営のみ 1）
  + failures 見出し 1 + failures 1 + log 見出し 1 + log 1、に 1 行の余裕。桁数は画面中で
  いちばん幅を食う固定行、ヘッダの最長形 `[test] vehicle console [participant]` + 区切り 1
  + キー操作 10 = 47 桁に合わせたもの（version 行は 45 桁）。version 行は運営の画面にしか
  出ないが、役割で最低幅を変えると tmux を役割ごとに張り替える羽目になるので幅は共通にしている。
  下回る場合は起動時に警告して終了する。
  `min_lines()` は役割のステップ数から導くので、ステップを増減させても手で直す箇所は無い。

### 失敗行の判定

TUI は `❌` で始まる行を失敗として retain する。これは `setup_check.sh` の `FAIL`
マーカーに依存しており、あちらを変えると retain が黙って止まる。
`make` や `docker` の出力も、同じマーカーが載っていれば拾える。

### 対話が必要なステップ

`extract_submission.py` は `input()` でチーム ID を、`getpass` で zip のパスワードを聞く
（ID は `SUBMISSION_ID=<id> make submission-extract` で先渡しできる）。
TUI は端末上で動くため、この対話をそのまま通せる。

該当ステップの実行中は curses を一時的に解除し（`curses.endwin()`）、
子プロセスに端末をそのまま渡す。終了後に画面を復帰させる。
認証情報を TUI 側で保持したり、環境変数へ書き出したりはしない。

## setup_check.sh の出力

- 要約は 1 行（`📊 N checks: X ok, Y warn, Z fail`）＋判定 1 行。従来は 7 行のブロックと
  `Critical issues found! Fix failures...` / `Recommended actions:` の 4 行を出していた。
- 判定行の行頭に `❌` / `⚠️` を置かない。TUI は行頭のマーカーで失敗行を拾うため、
  置くと判定行まで failures 領域に混ざる。
- 失敗の本文は、チェックの進行に合わせてその場で 1 回だけ出す。末尾に再掲はしない。
  「まとめて読みたい」は TUI の failures 領域が満たす。スクリプトを直接叩く人には
  判定 1 行が結論を与える。
- 終了コードは変えない。失敗ゼロなら 0（警告のみでも 0）、失敗ありなら 1。
  TUI の `check preflight` / `check runtime` の合否判定がこれに依存している。
- ログファイルは `vehicle/logs/` 配下に置く。呼び出し元の作業ディレクトリに
  散らさないためである。

## 実装

| ファイル | 役割 |
|----------|------|
| `vehicle/tui_core.py` | ステップ定義・前提のメタデータ・状態導出。curses と subprocess と filesystem に依存しない |
| `vehicle/tui.py` | 環境の観測、コマンド実行とログのストリーミング、curses の描画とキー処理 |
| `vehicle/tests/tui_core_test.py` | `tui_core` の単体テスト |
| `vehicle/tests/tui_test.py` | `tui.py` の観測関数と表示ヘルパの単体テスト |

Python 3 標準ライブラリのみを使う（`curses` / `subprocess` / `threading` / `queue` /
`textwrap` / `dataclasses`）。車両 PC には `download_submission.py` が動く Python 3 が
既に必要なため、追加依存はない。

`Makefile` の `vehicle-tui`（参加者）/ `vehicle-tui-staff`（運営、`tui.py --role staff`）で起動する
（命名は [makefile-target-naming.md](makefile-target-naming.md) の `<service>-<command>[-<variant>]` に従う）。
それぞれ `tmux new -A -s aic-vehicle` / `aic-vehicle-staff` で包むので、ssh が切れても作業が残り、
再接続して同じターゲットを叩けば同じセッションへアタッチする。セッションを役割で分けるのは、
参加者の画面が残った tmux に運営が `-A` で入っても運営のステップが出ないためである。

参加者は `ssh` の後に `make vehicle-tui`、運営は `make vehicle-tui-staff` を実行する。
遠隔側 GUI からワンクリックで端末を開く導線は
`aichallenge-racingkart-remote` 側の追加になるため、本 spec の対象外とする。

## エラーハンドリング

- **ステップの失敗**：終了コードを表示し、そのステップを失敗状態にする。
  失敗したステップは実行可能なまま残り、Enter で再実行できる。
  `setup_check.sh` の失敗項目は failures 領域にそのまま見せる（TUI 側で解釈しない）。
- **前提の崩れ**：アイドル中も 2 秒間隔で実測を取り直すため、外部で `make down` された
  場合や、コンソールを触っていないあいだにサービスが落ちた場合も自動的に反映される。
  ステップの実行中は取り直さない（`observe()` は `docker compose ps` を待つので
  描画スレッドを塞ぐし、ステップ終了時にはどうせ取り直す）。
- **docker が落ちている**：`docker compose ps` の失敗は空集合として扱い、例外にしない。
  デーモンが死んでいる機械でも画面が出て preflight が打てる必要がある。
  まさにその状況こそ preflight を走らせたい場面である。
- **ssh 切断**：tmux セッションが残る。再接続して `make vehicle-tui` を実行すると
  `-A` により同じセッションへアタッチする。実行中のステップは継続している。
- **端末が狭い**：役割ごとの最低サイズ（参加者 47x15、運営 47x17）を下回る場合は起動時に警告して終了する。

## テスト方針

`python3 vehicle/tests/<name>_test.py` で走る（`unittest.main()` 経由。
サードパーティ製ランナーを使わない）。

| 観点 |
|------|
| ステップ数と実行順 |
| 運営が参加者の並びとは独立に `download` / `driver` / `zenoh` / `rosbag` / `driver down` / `zenoh down` / `rosbag down` / `down all` の 8 ステップだけを持つこと |
| 参加者のステップに運営用（土台の起動・停止・download・down all）が混ざらないこと。運営に参加者用ステップと preflight が混ざらないこと。未知の役割は拒否 |
| `autoware` / `autoware down` が autoware コンテナだけを対象にし、土台は `driver` / `zenoh` / `rosbag` とその down ステップが 1 サービスずつ上げ下げすること |
| `cleanup` がワークスペースの削除で、`down` がスタックの停止であること |
| `autoware down` の完了判定が `autoware` だけを見ること |
| `cleanup` の完了判定（`workspace_pristine` のときだけ完了、既定は未完了） |
| `workspace_is_pristine` が tracked 差分・untracked・ignored 生成物のどれでも false になり、workspace 外の変更は見ないこと |
| `MIN_LINES` がステップ数 + failures 1 行 + log 1 行を下回らないこと |
| `extract` ステップが対話扱いで `make submission-extract` を呼ぶこと |
| `autoware` の完了が autoware だけで決まり、`driver` / `zenoh` / `rosbag` の各 up ステップがそのサービスだけの running で、各 down ステップがそのサービスだけの停止で決まること |
| `download` が対話扱いで運営だけに出ること |
| `install/` と `src/` の新旧による build の完了判定（同時刻を含む境界） |
| 実測ステップが古いセッション記録より実測を優先すること |
| 実行中のステップだけが実行不可であること |
| 前提未達でもステップが実行可能であること |
| 未達の前提を列挙できること |
| 観測関数が一時ディレクトリの実体を正しく読むこと |
| サービス行（running / stopped の 2 行）が `REQUIRED_SERVICES` の順で名前をそのまま出し、ヘッダにもステップ行にも出ないこと |
| 失敗行の判定（インデントあり・警告と成功の除外） |
| 折り返し（短い行の素通し・長い行の分割・空行の保持） |
| 最低端末サイズの境界 |
| アイドル中の再観測の判定（実行中は取り直さない・間隔の境界） |

curses の描画、実車での疎通、`make` ターゲットの実行そのものは手動確認とする。

## TODO

- **zip の配布手順。** `vehicle/.submissions/<id>.zip` を車両 PC へ置く手段（scp か、
  `make download` で取った tar.gz から運営が zip を作り直すか）と、パスワードの受け渡しは未決。
  `make download`（`download_submission.sh`）は運営用にそのまま残している。

## スコープ外

- 遠隔操作側の実装（`aichallenge-racingkart-remote` が担当）
- `racing_kart_manager` および muxer（`racing_kart_interface` が担当）
- Grafana ダッシュボードおよび telemetry / v2x 側の実装
- `prestage` 連携（[prestaged-submissions.md](prestaged-submissions.md) の
  事前ビルドを「済」として扱う分岐）。`vehicle/prestage/` は別ブランチにあり、
  この TUI はまだ参照しない
- `prestage-stage` / `prestage-unstage` を運営のステップとして出すこと。prestage 連携と同時に入れる
- この repo の `remote/` の整理（`aichallenge-racingkart-remote` と重複しているが、
  同 repo が main にマージされた後に別途扱う）

## 既知の齟齬

`aichallenge-racingkart-remote` の README は `shared/` の同期元を本 repo と定めているが、
同 README は正本を `remote/zenoh-user.json5.template` と記載しており、
本 repo にあるのは `remote/zenoh-user.json5` でテンプレート版が存在しない。
同期 CI を作る際に解消が必要である。

`/v2x/vehicle_positions/markers` の許可が `vehicle/zenoh.json5` の `allow.publishers` と
`remote/zenoh-user.json5` の `allow.subscribers` のいずれにも無い。`v2x_marker_publisher`
（`aichallenge_system.launch.xml` が `domain_id != 0` のとき起動）が車両側で `MarkerArray` を
publish するが、この 1 行が無いとブリッジが中継せず遠隔側の RViz に他車が映らない。
本 spec の対象外で、別 PR で扱う。
