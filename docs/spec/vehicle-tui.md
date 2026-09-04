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

## ステップ定義

| # | 表示名 | 実行するもの | 前提（助言） | 完了の判定 |
|---|--------|--------------|--------------|------------|
| 1 | `check preflight` | `./setup_check.sh --phase preflight` | なし | 終了コード 0（セッション記憶） |
| 2 | `download` | `make download` | 1 | 終了コード 0（セッション記憶） |
| 3 | `build` | `make autoware-build` | 2 | `workspace/install/setup.bash` が存在し `src/` より新しい（実測） |
| 4 | `autoware` | `make autoware-driver-zenoh-rosbag` | 3 | `driver` / `autoware` / `zenoh` / `rosbag` が compose 上で running（実測） |
| 5 | `check runtime` | `./setup_check.sh --phase runtime` | 4 | 終了コード 0（セッション記憶） |
| 6 | `cleanup` | `make down` | なし | 上記サービスがいずれも running でない（実測） |

チェックの 2 ステップは `check preflight` / `check runtime` と表示する。
`setup_check.sh` の `--phase` の値をそのまま名前にしているので、画面の名前から
実行されるコマンドが辿れる。内部のステップ ID は `preflight` / `submission` /
`build` / `up` / `runtime` / `teardown` で、表示名とは別である。

### 実測とセッション記憶

`build` / `autoware` / `cleanup` は環境から実測する。実測を優先するため、
別のシェルで `make down` された場合も次の観測で反映され、TUI 内のキャッシュと
実態が食い違うことがない。

`preflight` / `download` / `doctor` は実測できない。合否は終了コードにしか現れず、
後からファイルシステムを見て再現できないためである。

`download` は特に注意が要る。`aichallenge/workspace/src/aichallenge_submit/` には
**git 追跡された参加者パッケージが 15 個ある**ため、このディレクトリはチェックアウト時点で
既に空でない。したがって「提出物が存在するか」をディレクトリの中身で判定してはならない。
判定すると常に完了と出て、ステップの存在意義が失われる。

### 起動ターゲットは preflight だけ内包する

`autoware-driver-zenoh-rosbag` は preflight と runtime の両方を内包していた。
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
vehicle console                              ↑↓ enter q
1 NG preflight
2 ?  download
3 OK build
4 -  autoware ----
5 ?  doctor
6 OK cleanup ----
-- failures (8) ------------------------------------------
❌ CAN interface can0 not found
❌ VCU directory missing: /dev/vcu
❌ Invalid VEHICLE_ID for Zenoh: A0
-- log ---------------------------------------------------
$ ./setup_check.sh --phase preflight
📊 12 checks: 8 ok, 1 warn, 3 fail
[preflight] exit 1
```

- ヘッダは 1 行で、右端にキー操作を置く。
- ステップは縦 1 列。印は 2 文字固定（`OK` / `NG` / `>>` 実行中 / `-` 未実行 / `?` 前提未達）。
- `autoware` と `cleanup` の右のバッジは `driver` / `autoware` / `zenoh` / `rosbag` の
  状態を 1 文字ずつ並べたもの。起動中は頭文字、停止中は `-`（`dazr` / `da--` / `----`）。
  位置で意味が決まるので凡例が要らない。
- **failures は log とは別領域**で、log が流れても内容を保つ。残り高さの 2/3 までを使う。
  ステップを実行し直すとクリアされ、常に「今の実行」の失敗を映す。
- 長い行は折り返す。切り詰めると長いパスやコンパイラ出力の末尾が読めなくなる。
- ステップ 1 は起動時に自動実行する。
- 最低端末サイズは 40x12。内訳はヘッダ 1 + ステップ 6 + failures 見出し 1 + failures 1
  + log 見出し 1 + log 1 で 11 行、1 行の余裕を見て 12。下回る場合は起動時に警告して終了する。

### 失敗行の判定

TUI は `❌` で始まる行を失敗として retain する。これは `setup_check.sh` の `FAIL`
マーカーに依存しており、あちらを変えると retain が黙って止まる。
`make` や `docker` の出力も、同じマーカーが載っていれば拾える。

### 対話が必要なステップ

`download_submission.sh` は `read -r -s -p` でユーザー名とパスワードを聞き、
`download_submission.py` は `--latest` を渡さない限り `input()` で提出物を選ばせる。
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

`Makefile` の `vehicle-tui` で起動する（命名は
[makefile-target-naming.md](makefile-target-naming.md) の `<service>-<command>` に従う）。
`tmux new -A -s aic-vehicle` で包むので、ssh が切れても作業が残り、再接続して
同じターゲットを叩けば同じセッションへアタッチする。

参加者は `ssh` の後に `make vehicle-tui` を実行する。
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
- **端末が狭い**：40x12 を下回る場合は起動時に警告して終了する。

## テスト方針

`python3 vehicle/tests/<name>_test.py` で走る（`unittest.main()` 経由。
サードパーティ製ランナーを使わない）。

| 観点 |
|------|
| ステップ数と実行順 |
| `download` ステップが対話扱いであること |
| `autoware` ステップが起動ターゲットを呼ぶこと |
| `install/` と `src/` の新旧による build の完了判定（同時刻を含む境界） |
| 実測ステップが古いセッション記録より実測を優先すること |
| 実行中のステップだけが実行不可であること |
| 前提未達でもステップが実行可能であること |
| 未達の前提を列挙できること |
| 観測関数が一時ディレクトリの実体を正しく読むこと |
| サービスバッジの位置が `REQUIRED_SERVICES` の順序に従うこと |
| 失敗行の判定（インデントあり・警告と成功の除外） |
| 折り返し（短い行の素通し・長い行の分割・空行の保持） |
| 最低端末サイズの境界 |
| アイドル中の再観測の判定（実行中は取り直さない・間隔の境界） |

curses の描画、実車での疎通、`make` ターゲットの実行そのものは手動確認とする。

## スコープ外

- 遠隔操作側の実装（`aichallenge-racingkart-remote` が担当）
- `racing_kart_manager` および muxer（`racing_kart_interface` が担当）
- Grafana ダッシュボードおよび telemetry / v2x 側の実装
- `prestage` 連携（[prestaged-submissions.md](prestaged-submissions.md) の
  事前ビルドを「済」として扱う分岐）。`vehicle/prestage/` は別ブランチにあり、
  この TUI はまだ参照しない
- 役割の切り替え（運営用に `prestage-stage` / `prestage-unstage` を出し分ける）。
  prestage 連携と同時に入れる
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
