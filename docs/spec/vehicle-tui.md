# 車両 PC 操作 TUI（vehicle console）

> 仕様ドキュメント（現仕様の正）。文書運用方針は [docs/README.md](../README.md) を参照。

走行枠のあいだ、車両 PC（ECU）上で行う操作を 1 つの TUI に集約する設計。
既存の `make` ターゲット・`setup_check.sh`・`prestage` を**呼ぶだけ**に徹し、
順序の強制と状態の可視化だけを新しく担う。

## 背景と課題

走行枠ごとの車両側作業は、現在すべて生のシェルで行われている。実際の導線は次のとおり。

| # | 場所 | 操作 |
|---|------|------|
| 1 | 手元 | ターミナル① を開く |
| 2 | 手元 | 遠隔操作ツールを起動 |
| 3 | 手元 → 車両 | `ssh` |
| 4 | 車両 | 車両側 zenoh を起動 |
| 5 | 車両 | `cd vehicle` → `download_submission.sh` |
| 6 | 車両 | `cd ..` → `make autoware-build` |
| 7 | 車両 | `make autoware-driver-zenoh` |
| 8 | 車両 | `make setup-vehicle` |
| 9 | 手元 | ターミナル② → `cd remote` → zenoh 接続 |
| 10 | 手元 | RViz 起動 |

ここには次の問題がある。

- **preflight が最後にある。** `Makefile` の `autoware-driver-zenoh-rosbag` は
  「preflight → 起動 → runtime」の順に組まれているのに、実際の導線では
  `make setup-vehicle`（`--phase all` 相当）が最後に来ている。
  CAN や GNSS/RTK の異常を、数分〜十数分かけた `autoware-build` の**後**に知ることになる。
- **順序を人間が覚えている。** ステップ間の前提条件はドキュメントとして存在するだけで、
  実行系のどこにも表現されていない。順序を誤ると失敗が build 後まで遅延する。
- **`cd` の往復がある。** `download_submission.sh` は `vehicle/` にあり、`make` はリポジトリルートにある。
- **長時間処理の進捗が見えない。** `make autoware-build` の残り時間もパッケージ数も分からない。
- **`prestage` が導線に現れない。** オフライン会場向けの事前ビルド（[prestaged-submissions.md](prestaged-submissions.md)）は
  提出物取得と build を枠の前に済ませられるが、既存の手順書はその分岐を持たない。

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

1. **既存の実行系を再実装しない。** TUI は `make` / `setup_check.sh` / `prestage` を
   subprocess で呼ぶだけとする。チェック項目やビルド手順を TUI 側に複製しない。
2. **順序は画面に示すが、強制はしない。** 前提を満たさないステップにも警告
   （`⚠ <前提の名前> 未完了`）を表示するが、Enter は常に受け付ける（実行中の
   ステップ自身を除く）。CAN の無い開発機で preflight が落ちる場合など、
   運転者が「それでも先に進む」と判断できる場面が実在するため、逸脱を隠さず
   見せることに徹し、押せなくすることはしない。
3. **状態は保存せず検出する。** ステップの完了状態はファイルに書かず、毎回実測する
   （`install/` の存在、`docker compose ps`、`setup_check.sh` の終了コード）。
   TUI を再起動しても、ssh が切れても、状態は一貫する。
4. **tmux の中で動かす。** ssh 切断で作業が消えないこと、貼り直せることを前提とする。
5. **純ロジックを分離する。** ステップの前提判定と状態遷移を curses から切り離し、
   ROS も端末もなしにテストできるようにする（`racing_kart_manager_core.py` と同じ作法）。

## ステップ定義

「通常の前提」列は画面に警告として表示するだけの情報であり、実行を止める
ものではない。すべてのステップは、他のどのステップの状態に関わらず、
実行中でない限り Enter で起動できる。

| # | ステップ | 実行するもの | 通常の前提（警告のみ） | 完了の判定 |
|---|----------|--------------|------------------------|------------|
| 1 | preflight | `./setup_check.sh --phase preflight` | なし | 終了コード 0 |
| 2 | 提出物 | `make download`（参加者）／ステージ済みの検出 | 1 が完了 | `src/aichallenge_submit/` に提出物が存在、**または** prestage により `install/` が展開済み |
| 3 | build | `make autoware-build` | 2 が完了 | `workspace/install/setup.bash` が存在し、`src/` より新しい。prestage 経路では展開時点で完了扱い |
| 4 | スタック起動 | `make autoware-driver-zenoh-rosbag CHECK=0` | 3 が完了 | `driver` / `autoware` / `zenoh` / `rosbag` が compose 上で running |
| 5 | runtime check | `./setup_check.sh --phase runtime` | 4 が完了 | 終了コード 0 |
| 6 | 片付け | `make down` ＋ `output/` の回収案内 | なし（いつでも可） | compose 上に稼働サービスなし |

ステップ 1 と 6 は通常の前提を持たない。異常時にいつでも実行できる必要が
あるためである。他のステップも前提の有無に関わらずいつでも実行できるが、
前提が未完了の間は画面に警告が出る。

### 2 つの経路

提出物と build には 2 つの経路があり、TUI は実測でどちらかを判定する。

| 経路 | 前提 | ステップ 2 | ステップ 3 |
|------|------|-----------|-----------|
| オンライン（練習日など） | 回線がある | `make download` で取得。`src/` が埋まる | `make autoware-build` を実行 |
| prestage（オフライン会場） | 運営が `prestage-stage` 済み | `install/` の存在で「ステージ済」表示 | 展開済みのため実行不要 |

[prestaged-submissions.md](prestaged-submissions.md) のとおり、prestage が配布するのは
**ビルド済み `install/` のみで `src/` を含まない**。したがって
「`src/` があるか」だけでステップ 2 を判定してはならない。判定は次の順とする。

1. `workspace/install/` が存在し `src/aichallenge_submit/` が空 → prestage 経路。2 と 3 を完了扱い
2. `src/aichallenge_submit/` に提出物がある → オンライン経路。2 は完了、3 は `install/` の新旧で判定
3. どちらもない → 2 は未完了

### 二重チェックの回避

`autoware-driver-zenoh-rosbag` は現在、起動前に preflight、起動後に runtime を内包している。
TUI がステップ 1 と 5 を独立に持つと、preflight が 2 回走る（1 回あたり 30 秒程度）。

`Makefile` に `CHECK` 変数を追加し、`CHECK=0` のとき内包チェックを飛ばす。
既定値は `1` とし、`make` を直接叩く既存の運用は挙動を変えない。

## 役割

車両 PC は参加者と運営の両方が触る。[prestaged-submissions.md](prestaged-submissions.md) は
「参加チームが自分で車両 PC 上で走行準備を行う」運用を前提とし、その前後で運営が
ステージング／ワイプを行う。したがって TUI は役割を持つ。

- **既定は参加者。** 表示するのはステップ 1〜6。
- **`--organiser` を渡したときだけ** `make prestage-stage` と `make prestage-unstage` を表示する。

`prestage-unstage` は他チームの成果物と eval イメージを削除する破壊的操作であり、
参加者の画面に出してはならない。参加者向けの片付けステップには
「運営を呼ぶ」旨だけを表示する。

`--organiser` はアクセス制御ではない（車両 PC 上で docker が使える者は実質 root であり、
[prestaged-submissions.md](prestaged-submissions.md) の脅威モデルどおり境界にならない）。
誤操作を防ぐための表示制御である。

## 画面設計

```
┌─ Racing Kart Vehicle Console ── A2 / ECU-RK-01 ── ROS_DOMAIN_ID 1 ──────┐
│                                                                          │
│  1  preflight        ✅ 12/12   CAN 1.2k f/s  GNSS RTK-FIX  docker ok    │
│  2  提出物            ✅ ステージ済 (team_07 / sha256 一致)               │
│  3  build            ✅ 済 (事前ビルドの install/ を展開)                 │
│  4  スタック起動      ▶  driver ● autoware ● zenoh ○ rosbag ○           │
│  5  runtime check    ─  未実行（4 の完了後に自動）                        │
│  6  片付け            ─  make down / output 回収                          │
│                                                                          │
│  ↑↓ 選択   Enter 実行   l ログ   r 再実行   q 終了                       │
│  ─────────────────────────────────────────────────────────────────────── │
│  [4] $ make autoware-driver-zenoh-rosbag CHECK=0                         │
│      Container aichallenge-driver-1  Started                             │
│      待機中 15s → zenoh 起動…                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

- 上段がステップ一覧、下段が実行中コマンドのログ。
- 前提を満たさないステップにも `⚠ <前提の名前> 未完了` を行末に表示するが、
  Enter は常に受け付ける（そのステップ自身が実行中の場合を除く）。前提を
  外れて実行するかどうかは運転者が判断する。
- ステップ 1 は起動時に自動実行する。ステップ 5 はステップ 4 の完了後に自動実行する。
- 失敗したステップは失敗として残し、`r` で再実行できる。後続のステップも
  （前提未完了の警告付きで）実行は可能なままである。
- 実行中も画面は応答する。ログは別スレッドから queue 経由で受け取り、描画スレッドが読む。

### 対話が必要なステップ

`download_submission.sh` は `read -r -s -p` でユーザー名とパスワードを聞き、
`download_submission.py` は `--latest` を渡さない限り `input()` で提出物を選ばせる。
TUI は端末上で動くため、この対話をそのまま通せる。

該当ステップの実行中は curses を一時的に解除し（`curses.endwin()`）、
子プロセスに端末をそのまま渡す。終了後に画面を復帰させる。
認証情報を TUI 側で保持したり、環境変数へ書き出したりはしない。

## 実装

| ファイル | 役割 |
|----------|------|
| `vehicle/tui_core.py` | ステップ定義・前提判定・状態遷移。curses と subprocess に依存しない純ロジック |
| `vehicle/tui.py` | curses の描画、subprocess の実行、ログのストリーミング |
| `vehicle/tests/tui_core_test.py` | `tui_core` の単体テスト |

Python 3 標準ライブラリのみを使う（`curses` / `subprocess` / `threading` / `queue`）。
車両 PC には `download_submission.py` が動く Python 3 が既に必要なため、追加依存はない。

`Makefile` に `vehicle-tui` を追加する（命名は
[makefile-target-naming.md](makefile-target-naming.md) の `<service>-<command>` に従う）。

```make
# 車両 PC 上の操作コンソール。tmux 常駐なので ssh が切れても生き残る。
vehicle-tui:
	tmux new -A -s aic-vehicle "vehicle/tui.py $(TUI_ARGS)"
```

参加者は `ssh` の後に `make vehicle-tui` を実行する。
遠隔側 GUI からワンクリックで端末を開く導線は
`aichallenge-racingkart-remote` 側の追加になるため、本 spec の対象外とする。

## エラーハンドリング

- **ステップの失敗**：終了コードを表示し、そのステップを失敗状態にする。後続ステップは
  実行可能なままだが、前提が未完了である旨の警告が画面に出る。ブロックはしない —
  前提を実行系で強制しない設計方針（上記 2.）どおり、進むかどうかは運転者が決める。
  `setup_check.sh` の失敗時は、失敗した項目名をログからそのまま見せる（TUI 側で解釈しない）。
- **前提の崩れ**：状態は毎回実測するため、外部で `make down` された場合などは
  次の描画で自動的に反映される。TUI 内のキャッシュと実態が食い違うことがない。
- **ssh 切断**：tmux セッションが残る。再接続して `make vehicle-tui` を実行すると
  `-A` により同じセッションへアタッチする。実行中のステップは継続している。
- **TUI の異常終了**：子プロセスは `make` / `docker compose` であり、
  `up -d` は既にデタッチされている。前景で動くのは `setup_check.sh` と
  `autoware-build` だけで、いずれも中断しても副作用が残らない。
- **端末が狭い**：最低 80x24 を前提とし、下回る場合は起動時に警告して終了する。

## テスト方針

`python3 -m unittest` で走る（既存の `vehicle/tests/download_submission_test.py` と同じ作法。
サードパーティ製ランナーを使わない）。

| 観点 |
|------|
| 実行中でない限り、どのステップも前提の状態に関わらず Enter で実行できる |
| 実行中のステップ自身は多重起動できない（これだけが実行を止める） |
| 前提が未完了（未実行・失敗のいずれも）のステップには警告が表示される |
| ステップ 1 と 6 は通常の前提を持たない |
| ステップ 4 の完了でステップ 5 が自動実行対象になる |
| 失敗したステップは再実行できる。後続ステップも警告付きで実行可能なままである |
| 既定（参加者）では prestage 系のステップが一覧に現れない |
| `--organiser` では prestage 系が現れる |
| prestage 経路（`install/` あり・`src/` 空）でステップ 2 と 3 が完了扱いになる |
| オンライン経路（`src/` あり）でステップ 3 が `install/` の新旧で判定される |
| 提出物も `install/` も無いときステップ 2 が未完了になる |
| 外部で状態が変わったとき（`install/` 消失、compose 停止）に前提判定が追従する |

curses の描画、実車での疎通、`make` ターゲットの実行そのものは手動確認とする。

## スコープ外

- 遠隔操作側の実装（`aichallenge-racingkart-remote` が担当）
- `racing_kart_manager` および muxer（`racing_kart_interface` が担当）
- Grafana ダッシュボードおよび telemetry / v2x 側の実装
- `setup_check.sh` / `prestage` / 各 `make` ターゲットの中身の変更
  （例外は `CHECK` 変数の追加のみ）
- この repo の `remote/` の整理（`aichallenge-racingkart-remote` と重複しているが、
  同 repo が main にマージされた後に別途扱う）

## 既知の齟齬（本 spec の対象外だが記録する）

`aichallenge-racingkart-remote` の README は `shared/` の同期元を本 repo と定めているが、
現時点で次の食い違いがある。同期 CI を作る際に解消が必要である。

- 同 README は正本を `remote/zenoh-user.json5.template` と記載しているが、
  本 repo にあるのは `remote/zenoh-user.json5` で、テンプレート版が存在しない。
- ~~`/v2x/vehicle_positions/markers` の許可が両側に存在しない~~ → **解消済み**。
  `vehicle/zenoh.json5` の `allow.publishers` と `remote/zenoh-user.json5` の
  `allow.subscribers` に追加した。`v2x_marker_publisher`
  （`aichallenge_system.launch.xml` が `domain_id != 0` のとき起動）が車両側で
  `MarkerArray` を publish し、遠隔側が subscribe する構図である。
  これがないと車両が V2X マーカーを送っても遠隔側の RViz に他車が映らない。
