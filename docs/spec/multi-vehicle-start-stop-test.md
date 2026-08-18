# racing_kart_manager テスト設計書

> 対象設計: [`multi-vehicle-start-stop.md`](multi-vehicle-start-stop.md)
> 作成日: 2026-08-13

## 1. 目的

`racing_kart_manager` の誤りによって**車両が意図せず動くこと**を防ぐ。機能が動くことの確認より、危険な出力を出さないことの確認を優先する。

## 2. 危険分析

`racing_kart_driver` から逆算すると、車両が動くのは次の3条件が揃ったときだけ。

- `is_initialized_` が真
- `is_joystick_available()` が真（`buttons` 11個 / `axes` 8個）
- `is_emergency_` が偽

そして踏む量は `input_`（最後に受け取った joy）から決まる。したがって manager が危険を作れる経路は次の4つに限られ、テストはこの4つを塞ぐことに集中する。

| ID | ハザード | 発生機序 | 対応観点 |
| --- | --- | --- | --- |
| HZ-1 | 無操作のつもりで踏んだ joy を送る | `axes[2]`/`axes[5]` の無操作は `+1.0` であり `0.0` ではない。ゼロ埋めするとアクセル50%・ブレーキ50% | A |
| HZ-2 | 緊急停止が届かない・遅れる | 緊急停止ボタンを落とす、またはemergency 確認前に publish を止める | B |
| HZ-3 | 意図しない車両へ joy が届く | 送信先の誤り、名前空間の誤り | C |
| HZ-4 | 停止していない車両がいるのに操作を許可する | ガードのすり抜け、テレメトリ途絶を停止と誤判定 | D, F |

### HZ-2 の中核：「joy を止める」は「止める」ではない

`on_joy` は `input_ = msg` を代入するだけで（`racing_kart_driver_node.cpp:115-119`）、`on_timer` は `joy_delay_threshold`（5秒）を超えるまで最後の `input_` を使い続ける（`:192-199`）。

**publish を止めた車両は、最後に届いた joy の値のまま最大5秒走り続ける。** アクセルを踏んだ joy が最後だったら5秒間加速する。この事実は自動テストでは検証できないため、人力チェック C-09 で実測する。

## 3. テスト対象と構造

manager は3つの純関数と、それを繋ぐ薄い ROS 層に分割する。純関数はすべて ROS 非依存で、pytest だけで検証できる。

| 関数 | 責務 |
| --- | --- |
| `transform(joy, spec) -> dict[vehicle_id, JoyValue]` | joy 変換。`spec` は `destinations` / `suppress_axes` / `force_emergency` |
| `next_state(state, event, observations, joy, vehicles) -> ManagerState` | モード遷移の決定 |
| `status(state, observations, joy, vehicles) -> Status` | モード・各遷移の可否・不許可の理由・警告 |
| `render_messages(status) -> tuple[Message, ...]` | 表示文言。`targets` で塞いでいる操作を並べる |
| `vehicle_label(control_mode, stopped, emergency, receiving_joy) -> str` | 車両1台分の状態を1行にする |
| `control_mode_of(observation) -> str \| None` | `ControlModeReport` の値を表示名にする。途絶なら `None` |
| `status_to_json(status, stamp_ns) -> str` / `parse_command(str) -> Event \| None` | GUI との JSON 境界 |
| `gui_gate(status_age_s, schema_version) -> GuiGate` | GUI 側に唯一許した判定。status 途絶とバージョン不一致 |

`status()` は GUI 描画と遷移判定の**両方**が使う唯一の判断材料。GUI 側に条件式を持たせないことで、表示と判定の乖離を構造的に防ぐ。

## 4. テストレベルと環境

| レベル | 手段 | 環境 | 対象 |
| --- | --- | --- | --- |
| L1 | pytest（ROS 不要） | ホストで直接。数秒で完走 | 純関数すべて |
| 人力 | 台上（bench モード）でのチェックリスト | 実機 | 配線、実挙動、設計の前提 |

**自動化するのは L1 だけ。** ROS を起動するテストと実機 driver を使うテストは自動化せず人力で行う（第13章）。

L1 の実行方法。プロパティテスト（第7.5節ほか、`@given` を付けた11本）が hypothesis を使うため、
pytest だけでは `ModuleNotFoundError` になる。

```bash
# ホスト。システムを汚さずに実行する
uv run --with pytest --with hypothesis python -m pytest remote/tests

# コンテナ内。pytest と python3-hypothesis はイメージに入っている
python3 -m pytest remote/tests
```

## 5. 共通のテストデータ

`remote/tests/conftest.py` に置く。ROS 型ではなく core の `JoyValue` を使う。

```python
# 無操作 joy（axes の無操作は Accel/Brake が +1.0、Steer と Dpad が 0.0）
JOY_NO_INPUT = JoyValue(axes=NO_INPUT_AXES, buttons=NO_BUTTONS)

# 全開 joy（アクセル全開・右ステアリング・ギアD）
JOY_FULL = JoyValue(
    axes=(0.7, 0.0, +1.0, 0.0, 0.0, -1.0, 0.0, +1.0),
    buttons=NO_BUTTONS,
)

# ゼロ埋め joy（HZ-1 の再現用。driver はアクセル50%・ブレーキ50%と解釈する）
JOY_ZEROS = JoyValue(axes=(0.0,) * 8, buttons=NO_BUTTONS)

# 対象車両は起動時引数。テストでは既定を3台にし、台数依存のケースだけ明示的に渡す。
VEHICLES = ("A2", "A3", "A7")
```

観測のビルダも conftest に置く。`all_stopped()` は「対象車両すべてが停止・emergency 済み・テレメトリ新鮮」を返し、`all_stopped(A3=dict(velocity=0.5))` のように車両単位で崩す。

## 6. L1: `transform` のテストケース

| ID | 観点 | 前提 | 入力 | 期待結果 |
| --- | --- | --- | --- | --- |
| T-01 | A-1, A-2 | `spec = (4台, suppress=True, force=False)` | `JOY_FULL` | 出力4件すべてで `axes[5] == +1.0`, `axes[2] == +1.0`, `axes[0] == 0.0`, `axes[6] == 0.0`, `axes[7] == 0.0` |
| T-02 | A-3 | 任意の `spec` | 任意の joy | 全出力で `len(buttons) == 11` かつ `len(axes) == 8` |
| T-03 | A-5 | `spec = ({A2}, suppress=False, force=False)` | `JOY_FULL` | 出力の `axes` が入力と完全一致 |
| T-04 | A-4 | 宛先2台以上、`suppress=True` | `axes` を hypothesis で8要素ランダム生成 | 出力の `axes` が常に無操作値の定数と一致（プロパティテスト） |
| T-05 | B-1 | 任意の宛先 | `buttons[LB]=1`（`RB`/`Start`/`Back` でも同様にパラメータ化） | 出力全件で該当ボタンが `1` |
| T-06 | A-6 | `spec = (送信先, suppress=True, force=True)` | `buttons` 全0 | 出力全件で `buttons[4]`(LB), `buttons[5]`(RB), `buttons[6]`(Start), `buttons[7]`(Back) の**4つすべて**が `1`。かつ `axes` が無操作 |
| T-06b | A-6 | 同上 | オペレータが `buttons[4]=1` のみ押下 | 出力は T-06 と同一（冪等。既に押されているボタンを重ねても変わらない） |
| T-07 | C-1, C-3 | `spec = ({A2}, ...)` | 任意 | 出力のキーが `{"A2"}` のみ |
| T-08 | B-4 | `spec = (送信先なし, ...)` | 任意 | 出力が空 |
| T-09 | 素通し性 | `force=False` | 任意の `buttons` | 出力の `buttons` が入力と完全一致（`ButtonY` をマスクしないことの確認） |
| T-10 | 追跡性 | 任意 | `header.stamp` を設定 | 出力の `header.stamp` が入力と一致（遅延計測のため引き継ぐ） |
| T-11 | 頑健性 | `force=True` | `buttons` が2要素しかない joy | 11要素に補ったうえで緊急停止ボタンが立つ。`IndexError` で落ちない |

## 7. L1: `status` のテストケース

`stopped` と `emergency` は `Tri`（`TRUE` / `FALSE` / `UNKNOWN`）で、`UNKNOWN` をどちらにも倒さない。

### 7.1 基本

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| S-01 | INV-1 | `mode=PARK`、A2 の `velocity_age = 2.0`（閾値超過） | `A2.stopped == UNKNOWN`、`can_enter_all_mode` が偽、blocker に `VEHICLE_STATE_UNKNOWN(("A2",))` |
| S-02 | INV-1 | `mode=PARK`、A2 の `debug_age = 2.0` | `A2.emergency == UNKNOWN`、`can_enter_all_mode` が偽 |
| S-03 | 正常系 | `mode=PARK`、全車 速度0・emergency 済み・テレメトリ新鮮、スティック無操作、joy 新鮮 | `can_enter_all_mode` が真、`enter_all_mode_blockers` が空、全車 `can_enter_single_mode` が真 |
| S-04 | D-3 | `mode=PARK`、A3 の速度 `0.5` | `can_enter_single_mode("A2")` が偽、blocker に `VEHICLE_MOVING(("A3",))` |
| S-05 | D-3 | `mode=PARK`、A3 の `emergency == false` | `can_enter_single_mode("A2")` が偽、blocker に `VEHICLE_EMERGENCY_CLEARED(("A3",))` |
| S-06 | F-4 | `mode=PARK`、スティックが無操作でない | 全車 `can_enter_single_mode` が偽、blocker に `STICK_IN_USE` |
| S-07 | F-4 | joy 入力が途絶 | `can_enter_all_mode` が偽、blocker と alert の両方に `JOY_STALE` |

### 7.2 禁止遷移

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| S-08 | INV-4 | `mode=ALL` | `can_enter_all_mode` が偽、全車 `can_enter_single_mode` が偽、blocker に `NOT_IN_PARK` |
| S-09 | D-2 | `mode=SINGLE("A2")` | `can_enter_single_mode("A3")` が偽（単車操作から別の単車操作へ直接行けない） |
| S-10 | INV-4 | `mode=STOPPING` | すべての遷移が不可 |

### 7.3 停止プロトコルの警告

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| S-11 | INV-5, F-3 | `mode=STOPPING`、`stopping_elapsed_s = 5.1`、A6 がemergency 未確認 | alert に `EMERGENCY_CONFIRM_TIMEOUT(("A6",))`。車両IDが含まれること |
| S-12 | 境界 | `stopping_elapsed_s = 4.9`、A6 がemergency 未確認 | `EMERGENCY_CONFIRM_TIMEOUT` が出ない |
| S-13 | F-6 | `stopping_elapsed_s = 6.0`、全車 emergency 済み | `EMERGENCY_CONFIRM_TIMEOUT` が出ない（解消したら消える） |
| S-14 | INV-5 | `stopping_elapsed_s = 6.0`、A3 と A6 がemergency 未確認 | alert の `vehicles` が `("A3", "A6")` 相当。1台だけにならない |

### 7.4 境界値

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| S-15 | 境界 | 速度 `0.099` / `0.101`（閾値 `0.1`） | 順に `stopped == TRUE` / `stopped == FALSE` |
| S-16 | 境界 | テレメトリの受信からの経過時間 `0.99` / `1.01`（閾値 `1.0`） | 順に値が反映 / `UNKNOWN` |
| S-17 | F-5 | velocity 途絶 | `stopped` が `FALSE` ではなく `UNKNOWN`。両者を区別すること |
| S-18 | F-5 | 一度も受信していない | `velocity_age_s is None` かつ `stopped == UNKNOWN` |
| S-24 | D-3 | スティック無操作判定の5分岐（完全無操作 / ステアリングを切る / アクセルを踏む / ブレーキを踏む / 軸不足） | 完全無操作のときだけ真。**ステアリングが中立でもアクセルを踏んでいれば偽** |
| S-25 | D-1 | joy を一度も受信していない（manager 起動直後） | 全操作が不可。起動直後に一斉発進準備完了を押せない |

### 7.5 プロパティテスト

| ID | 観点 | 命題 |
| --- | --- | --- |
| S-19 | INV-1 | 任意の入力で、`Tri.UNKNOWN` の車両が1台でもあれば `can_enter_all_mode` は偽 |
| S-20 | INV-2 | 任意の入力で、`stopped != TRUE` の車両は必ずいずれかの blocker の `vehicles` に現れる |
| S-21 | INV-3 | `can_enter_single_mode(v)` が真なら、`v` 以外の3台すべてが `stopped == TRUE` かつ `emergency == TRUE` |
| S-22 | INV-6 | `VEHICLE_*` 系の blocker は `vehicles` が非空 |
| S-23 | F-2 | `can_enter_all_mode` が `len(enter_all_mode_blockers) == 0` と常に一致（可否を別に保持していないことの確認） |

## 8. L1: `next_state` のテストケース

| ID | 観点 | 現在 | イベント | 期待遷移 |
| --- | --- | --- | --- | --- |
| N-01 | D-1 | 初期状態 | — | `PARK` |
| N-02 | 正常系 | `PARK` | 一斉発進準備完了、`can_enter_all_mode` が真 | `ALL` |
| N-03 | D-6 | `PARK` | 一斉発進準備完了、`can_enter_all_mode` が偽 | `PARK` のまま |
| N-04 | 正常系 | `PARK` | 車両選択 A2、`can_enter_single_mode("A2")` が真 | `SINGLE("A2")` |
| N-05 | D-2 | `ALL` | 車両選択 A2 | `ALL` のまま |
| N-06 | D-2 | `SINGLE("A2")` | 車両選択 A3 | `SINGLE("A2")` のまま |
| N-07 | B-1 | `ALL` | 緊急停止ボタン | `STOPPING` |
| N-08 | B-1 | `SINGLE("A2")` | 緊急停止ボタン | `STOPPING` |
| N-09 | B-2 | `STOPPING` | 一部の車両がemergency 未確認 | `STOPPING` のまま。`PARK` へ行かない |
| N-10 | B-2 | `STOPPING` | 全車emergency 確認 | `PARK` |
| N-11 | B-3 | `STOPPING` | 車両選択・一斉発進準備完了 | `STOPPING` のまま（割り込みを無視） |
| N-12 | B-2 | `STOPPING` | 経過5秒超・emergency 未確認あり | `STOPPING` のまま。警告は出るが publish は止めない |
| N-13 | D-5 | `SINGLE("A2")` | A3 の `stopped` が `FALSE`（動き出した） | `STOPPING` |
| N-14 | D-5 | `SINGLE("A2")` | A3 の `stopped` が `UNKNOWN`（テレメトリ途絶） | `STOPPING`。確認できない場合を安全側に倒す |
| N-15 | D-5 | `SINGLE("A2")` | A3 の `emergency` が `FALSE`（停止中だが emergency 解除） | `STOPPING` |
| N-16 | 決定3 | `SINGLE("A2")` | **A2 自身**の `stopped` が `FALSE` | `SINGLE("A2")` のまま。対象車は操縦中なので動いてよい |
| N-17 | 決定3 | `SINGLE("A2")` | **A2 自身**の `stopped` / `emergency` が `UNKNOWN` | `SINGLE("A2")` のまま。警告のみ |
| N-18 | D-5 | `ALL` | いずれかの車両が動いている | `ALL` のまま。4台とも自動走行してよい |
| N-19 | D-5 | `ALL` | いずれかの車両のテレメトリが途絶（`UNKNOWN`） | `ALL` のまま。警告のみ。誤ってレースを止めない（REQ-05） |
| N-20 | D-5 | `PARK` | いずれかの車両が動いている / テレメトリ途絶 | `PARK` のまま。警告のみ。joy を送っていないため介入手段がない |
| N-21 | D-6 | `PARK` | テレメトリ更新のみ、GUI 操作なし | `PARK` のまま。勝手に広がらない |
| N-22 | 網羅 | 任意 | 遷移表に無い組み合わせ全部 | 現状維持（プロパティテスト） |
| N-23 | D-5 | `PARK` | 緊急停止ボタン | `PARK` のまま。joy を送っていないので停止プロトコルを始めても送り先が無い |

N-13〜N-20 は「停止しているべき車両」の範囲がモードによって異なることの確認。`SINGLE(v)` では `v` 以外の3台のみが対象で、`ALL` と `PARK` では対象なし。判定条件は `stopped != TRUE` または `emergency != TRUE`（`FALSE` と `UNKNOWN` の両方を含む）。

N-19 は誤停止を避ける側の確認。テレメトリだけ途絶して joy は届いている状況で自動フォールバックすると、正常なレース中に全車を止めることになる。joy も届いていないなら driver 側が5秒で緊急停止する（REQ-04）ので放置してよい。

## 9. L1: 表示文言のテストケース

観点 F。表示が危険側に誤ると、オペレータが誤った状況認識のまま操作する。**文言そのものではなく「可否と表示が常に対応すること」を検証する**ので、文言を変えてもテストは壊れない。

### 9.1 `render_messages`（操作を塞ぐ理由）

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| F-01 | F-1 | すべての `BlockerCode` | 空でない文言が返る（パラメータ化で網羅） |
| F-02 | F-1 | すべての `AlertCode` | 同上 |
| F-03 | F-4 | 任意の観測（プロパティ） | 操作できないなら、必ずその操作を `targets` に含む文言がある。**理由の出ない不許可を作らない** |
| F-04 | F-4 | 任意の観測（プロパティ） | 操作できるなら、その操作を `targets` に含む文言は無い。押せるのに「できません」と出さない |
| F-05 | F-6 | 全車正常・パーク | 文言が1件も出ない。常時警告が出ていると誰も読まなくなる |
| F-06 | F-5 | A6 のテレメトリ途絶 | 「不明」と出る。**「停止しています」とは出ない** |
| F-07 | F-3 | 停止プロトコル5秒超、A3 と A6 が未確認 | `error` の文言に A3 と A6 の両方が含まれる |
| F-08 | F-6 | 原因の解消前後 | 解消したら文言が消える。解消していないのに消えない |
| F-09 | F-1 | A3 が動いている | 「A3 が停止していません」が**1件だけ**で、`targets` が `all` / A2 / A6 / A7 |
| F-10 | F-2 | スティック操作中 | 単車操作だけが塞がれ、一斉発進は塞がれない |
| F-11 | F-2 | 一斉モード中 | 全操作に理由が出る |
| F-12 | F-1 | 文言の無いコード | 空文字ではなく `KeyError`。足し忘れに黙って通されない |

### 9.2 `vehicle_label`（車両1台分の状態）

各車両ボタンの下に出す1行。`<制御モード> / <走行中\|停止中\|不明> / 緊急停止 <有効\|解除\|不明> / joy <送信中\|送信なし>` の4項目を常に出す。

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| V-01 | F-1 | 設計書の6例すべて | 期待どおりの文字列になる（パラメータ化） |
| V-02 | F-1 | 任意の入力 | 区切りが3つ、項目が常に4つある。省略しない |
| V-03 | F-5 | 制御モードが途絶 | `不明` と出る。既定値（`MANUAL` など）に倒さない |
| V-04 | F-5 | `stopped` / `emergency` の各3値 | `走行中`/`停止中`/`不明`、`有効`/`解除`/`不明` に対応（パラメータ化） |
| V-05 | F-1 | `receiving_joy` の真偽 | `joy 送信中` / `joy 送信なし` に対応 |
| V-06 | 頑健性 | `ControlModeReport` に無い値（`7` など） | 例外にせず、値が分かる形で出す |

### 9.3 `control_mode_of`（制御モードの解釈）

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| V-07 | 契約 | 定義済みの全モード値 | `MANUAL` / `AUTONOMOUS` などの名前になる（パラメータ化） |
| V-08 | 境界 | 受信からの経過時間 `0.99` / `1.01`（閾値 `1.0`） | 順に値が反映 / `None` |
| V-09 | F-5 | 一度も受信していない | `None`。`stopped` / `emergency` と同じく無音を既定値に倒さない |

`control_mode` は**表示にのみ使い、遷移の判定には使わない**。`next_state` の入力にはならないので、N グループにケースは足さない。

F-09 は重複の抑制。1つの理由が複数の操作を塞ぐとき、操作ごとに文言を作るとメッセージ表示エリアに同じ文が4回並ぶ。GUI 側で重複を除くと F-1 が破れるため、`targets` にまとめる。

## 10. L1: 対象車両リストのテストケース

対象車両は起動時引数で、台数も車両IDも固定しない。台数が変わることで新しく起きうる
失敗を潰す。既存のケースの車両IDを差し替えるだけでは足りない。

### 10.1 起動引数の検証

`parse_vehicles(args) -> tuple[str, ...] | None` として core に置き、純関数のまま検証する。

| ID | 前提 | 期待結果 |
| --- | --- | --- |
| P-01 | `["A2", "A3", "A7"]` | そのままの順で受理する。GUI のボタン並びに効くので順序を保つ |
| P-02 | 空（車両を1台も指定しない） | 拒否。台数0で起動すると、何も送れないノードが黙って立ち上がる |
| P-03 | 未知のID（`A9`） | 拒否 |
| P-04 | 重複（`A2 A2`） | 拒否。片方の観測がもう片方を上書きして判定が壊れる |

### 10.2 対象外の車両を無視すること

| ID | 前提 | 期待結果 |
| --- | --- | --- |
| P-05 | 対象車両 `A2 A3 A7`、観測に A6 が混ざる | `status` の `vehicles[]` に A6 が出ない |
| P-06 | 同上、A6 が走行中・emergency 解除 | `can_enter_all_mode` と `can_enter_single_mode` が影響を受けない |

P-06 が安全面で効く。対象車両に入っていない車の状態で操作の可否が変わってはならない。

### 10.3 台数固有のふるまい

| ID | 前提 | 期待結果 |
| --- | --- | --- |
| P-07 | 対象車両1台、一斉モード | **軸が無操作値になる。** 台数で切り替えると1台構成のときだけ一斉が単車操作と同じ挙動になり、モードの意味が崩れる |
| P-08 | 対象車両1台、単車操作に入る | 「対象車以外」が空集合なので、他に止めるべき車が無く入れる |
| P-09 | 対象車両2台、単車操作 | 「対象車以外」は1台。その1台の停止と emergency を見る |

### 10.4 不変条件を台数横断で確認する

既存のプロパティテスト（S-19〜S-23、F-03、F-04）は対象車両を 1 / 2 / 3 / 4 台で横断させる。
不変条件が台数によらず成り立つことが、この変更で確認したいことそのもの。

「対象車以外」のロジックは台数が減るほど境界に近づく（2台なら他は1台、1台なら他は0台）。

## 11. L1: JSON 境界のテストケース

status と command は `std_msgs/String` に JSON を載せる。ここは「表示と実体がずれうる箇所」なので形の崩れを潰す。

| ID | 観点 | 前提 | 期待結果 |
| --- | --- | --- | --- |
| J-01 | F-5 | A6 のテレメトリ途絶 | `stopped` が `"UNKNOWN"` の**文字列**。真偽値に潰さない |
| J-02 | 契約 | 任意 | 必須キーが揃い、`schema_version` と `stamp_ns` が入る |
| J-03 | 契約 | 任意 | `vehicles` と `can_enter_single_mode` に4台すべてが現れる |
| J-04 | 契約 | `SINGLE("A2")` | `mode` と `selected` が出る |
| J-05 | 契約 | 停止プロトコル中 | `stopping_elapsed_s` が出る |
| J-06 | F-1 | 文言あり | 各文言に `level` / `targets` / `text` が付く |
| J-07 | 契約 | 任意の観測（プロパティ） | JSON として出力でき、`Tri` は常に3値の文字列 |
| J-08 | 契約 | 正常なコマンド2種 | `Event` になる |
| J-09 | 頑健性 | 不正なコマンド8種 | すべて `None`。**例外を投げない** |
| J-10 | 前方互換 | 未知フィールド付き | 無視して受理する |
| J-11 | 契約 | 任意 | `vehicles[]` の各要素に `control_mode` と `label` が入る。GUI が変換表を持たずに描ける |

J-01 が最重要。`Tri` を真偽値に潰すと `UNKNOWN` が表現できなくなり、テレメトリ途絶を「停止」と誤表示する事故に直結する。

J-09 も安全性に直結する。manager が落ちると joy が止まり、5秒後に全車が緊急停止するため、不正入力で落ちないことが要件になる。

## 12. L1: `gui_gate` のテストケース

GUI 側に唯一許したロジック。manager が落ちても GUI には最後の status が残り続けるが、これは manager 自身からは送れないので GUI が検出する。

| ID | 前提 | 期待結果 |
| --- | --- | --- |
| G-01 | 新鮮・バージョン一致 | 操作できる |
| G-02 | `status_timeout_s` 超過 | 全ボタン非活性。理由に「通信」を含む |
| G-03 | 一度も受信していない（GUI 起動直後） | 全ボタン非活性。見ていない画面への操作を送らせない |
| G-04 | `schema_version` 不一致 | 全ボタン非活性。片方だけ更新して黙って誤動作するのを防ぐ |
| G-05 | 閾値の境界 | `status_timeout_s` を境に切り替わる |
| G-06 | 経過時間もバージョンも異常 | 判定順序によらず必ず非活性 |

## 13. 自動化しない範囲（人力で実施）

**自動テストは L1 の純関数のみとする。** ROS を起動する配線テスト（旧 L2）と実機 driver を動かす結合テスト（旧 L3）は自動化せず、人力で確認する。維持コストに見合わないと判断した。

その代わり、**それらでしか検証できない前提を第14章のチェックリストへ明示的に移す**。ロジックテストは「決めた通りに動くか」しか見ておらず、「決めたことが正しいか」は見ていない。この区別を失うと、全ケース緑のまま設計の前提が間違っている状態になる。

代表例が無操作値である。`test_t01` は出力が `NO_INPUT_AXES` と一致することを確認するが、比較対象は自分で定義した定数なので、**無操作値の解釈自体を誤っていればテストごと揃って間違う**。実際に driver へ流して確認する以外に検出手段がない。

| 人力で確認すること | 対応するチェック項目 |
| --- | --- |
| joy を止めても最後の値で最大5秒走り続ける | C-09 |
| 無操作はアクセル・ブレーキとも `+1.0`（ゼロ埋めでは動いてしまう） | C-10, C-11 |
| `buttons` 11 / `axes` 8 でないと停止指令に落ちる | C-12 |
| manager の publish が対象車にだけ届く | C-04 |
| QoS が一致してメッセージが届く | C-01 |
| `autorepeat_rate` により joy が自動再送される | C-13 |
| zenoh の 10 Hz 間引き下で `telemetry_timeout_s = 1.0` が妥当 | C-14 |

## 14. 人力チェックリスト（台上）

駆動輪を浮かせた状態、または bench モードで実施する。C-09 以降は、自動テストでは原理的に検証できない前提の確認にあたる。

遠隔操作PC側の起動:

```bash
PACKAGES=racing_kart_msgs make autoware-build   # 初回だけ
make remote VEHICLES="A2 A3 A7"                 # 対象車両は実際に使う分だけ挙げる
```

`make ps` で `zenoh-remote` / `joy` / `manager` / `manager-gui` の4つが Up であることを先に確認する。
`manager` が Exit していれば `docker compose logs manager` に理由が出る。

### 10.1 基本動作

| ID | 確認内容 |
| --- | --- |
| C-01 | manager 起動直後、どの車両にも joy が届いていない（`ros2 topic hz` で0件）。GUI に `VEHICLES` で指定した車両の状態がすべて表示され、指定していない車両は現れない |
| C-02 | テレメトリを切ると該当車両が「不明」表示になる。**「停止」ではない** |
| C-02b | ジョイスティックで `ButtonY` / `ButtonA` を押すと、該当車両の表示が `AUTONOMOUS` / `MANUAL` に追随する。zenoh の許可リストに `/vehicle/status/control_mode` を足し忘れていると変わらない |
| C-03 | 1台のテレメトリを切った状態で「一斉発進準備完了」が押せない。理由が表示される |
| C-04 | 単車操作で対象車のみが反応し、対象外の車両が無反応 |
| C-05 | 単車操作中に他車を動かすと（手押しなど）自動的に停止プロトコルへ落ちる |
| C-06 | 緊急停止ボタンで全車が即座に停止し、GUI が emergency 確認済みを表示する |
| C-07 | 1台の `debug/status` を切って緊急停止し、5秒後に該当車両IDを含む警告が GUI に出る |
| C-08 | `ButtonY` を押しっぱなしで `LSB+RSB` を押すと即発進する（既知の挙動。手順として周知する） |

### 10.2 設計の前提の確認

| ID | 確認内容 |
| --- | --- |
| C-09 | 単車操作でアクセルを踏んだ状態から manager を停止し、**5秒間は走り続けてから止まる**ことを実測する。「joy を止める = 止まる」ではないことの確認 |
| C-10 | 一斉モードでスティックを全方向に振っても車両が動かない |
| C-11 | **ゼロ埋めした joy を driver へ直接流すと動いてしまう**ことを確認する。C-10 が本物であることの逆証明。これが動かなければ C-10 は何も検証していない |
| C-12 | 要素数の違う joy を流すと停止指令に落ちる |
| C-13 | ジョイスティックに触れず放置しても joy が流れ続け、車両が緊急停止しない（`autorepeat_rate` の確認） |
| C-14 | 対象車両をすべて接続した状態でテレメトリの受信間隔を観測し、`telemetry_timeout_s = 1.0` が誤検出しない余裕を持つことを確認する |

C-11 はネガティブ確認。「無操作値を正しく送れている」と主張するには、「無操作値を間違えると本当に動く」ことを同じ環境で示す必要がある。

## 15. トレーサビリティ

| 要件 | ハザード | 観点 | 代表ケース |
| --- | --- | --- | --- |
| REQ-01 一斉発進 | HZ-1 | A | T-01, T-04 / C-10, C-11 |
| REQ-02 一斉緊急停止 | HZ-2 | B | T-05, N-07〜N-12 / C-06, C-09 |
| REQ-03 任意の1台を手動操縦 | HZ-3, HZ-4 | C, D, F | T-03, T-07, S-04, S-05, S-21, F-03, F-04 / C-04 |
| REQ-04 5秒途絶で緊急停止 | HZ-2 | B-5 | C-09, C-13 |
| REQ-05 4台同時自動走行 | HZ-1 | A | T-01, T-04 / C-10 |

## 16. 出口基準

- L1 の全ケースが pass し、core の分岐網羅が 100%
- 人力チェックリストが全項目完了。とくに C-11 が「期待通り危険側に振れる」ことを確認済み
- 決定事項がすべて実装に反映されている

## 17. テストしない範囲と理由

| 対象 | 理由 |
| --- | --- |
| 入力 joy の配列サイズが異常なとき | そのまま素通しする。driver が `is_joystick_available()` で停止指令に落ちる（`racing_kart_driver_node.cpp:186-187`）ため安全側に倒れる。manager 側に分岐を作らない |
| `ButtonY` 押しっぱなしでの即発進 | 「joy を解釈しない」方針を優先し manager 側でマスクしない。既知の挙動として L4-8 で手順に落とす |
| zenoh 経路そのもの | 既存機能。名前空間の付与は設計側の変更点として別途確認する |
| `racing_kart_driver` 内部 | 既存機能。本テストは manager の出力が driver にどう解釈されるかのみを対象とする |

## 18. 決定事項

| # | 内容 | 影響するケース |
| --- | --- | --- |
| 1 | `force_emergency` は `ButtonLB` / `ButtonRB` / `ButtonStart` / `ButtonBack` の**4つすべて**を立てる。取りこぼしが無く、かつ「4つ同時 = manager が合成」の署名になり rosbag から判別できる | T-06, T-06b |
| 2 | `header.stamp` は入力から**引き継ぐ**。driver は自身の `now()` を使うため安全性に影響せず、zenoh 経由の遅延計測に使える | T-10 |
| 3 | 単車操作の前提条件に対象車自身の停止・emergency は**含めない**。対象車は操縦中なので動いてよい。テレメトリ途絶時も警告のみ | S-04, S-05, S-21, N-16, N-17 |
| 4 | `telemetry_timeout_s = 1.0` / `stopped_speed_threshold_mps = 0.1` / `stick_no_input_tolerance`（ステアリング 0.1、アクセル・ブレーキ 0.9 以上） | S-15, S-16 |

## 19. 付録: テスト観点一覧

各ケースの「観点」列が参照する定義。`INV-*` は [`multi-vehicle-start-stop.md`](multi-vehicle-start-stop.md) の「Status が満たすべき不変条件」を参照。

### A. 送る中身が安全か

| # | 観点 |
| --- | --- |
| A-1 | 一斉モードで `axes[5]`(Accel) と `axes[2]`(Brake) が常に `+1.0`。ゼロ埋めすると50%踏んだ扱いになる |
| A-2 | 一斉モードで `axes[0]`(Steer) と `axes[6]`,`axes[7]`(Dpad) が `0.0` |
| A-3 | 出力が常に `buttons` 11個 / `axes` 8個 |
| A-4 | 宛先が2台以上のとき、入力の軸をどう動かしても出力の軸が無操作値から動かない（プロパティテスト向き） |
| A-5 | 単車操作では軸が改変されず実値がそのまま通る |
| A-6 | 停止中モードで、入力のボタンがどうであれ緊急停止ボタンが押された joy が出る。かつ軸が無操作値 |

### B. 止められるか

| # | 観点 |
| --- | --- |
| B-1 | 緊急停止ボタン4種のいずれかが 送信先の全車へ素通しされる |
| B-2 | 押下後、全対象車の `emergency == true` を確認するまで publish を続ける。早すぎる停止で無制御時間を作らない |
| B-3 | emergency 確認前に 送信先を縮める操作（GUIの車両切替など）が割り込んでも、先に緊急停止を通し切る |
| B-4 | パーク中は1メッセージも publish しない |
| B-5 | manager プロセスが消えたら5秒後に全車が停止する（driver 側担保の確認） |

### C. 宛先が正しいか

| # | 観点 |
| --- | --- |
| C-1 | 単車操作で対象1台にだけ届き、他3台のトピックには1メッセージも出ない |
| C-2 | rename が `/<VEHICLE_ID>/racing_kart/joy` の形で正しい |
| C-3 | 送信先に無い車両へ publish しない |

### D. モード遷移が仕様通りか

| # | 観点 |
| --- | --- |
| D-1 | 起動直後は送信先が空 |
| D-2 | 禁止遷移が起きない（一斉 ↔ 単車操作、単車操作 → 別の単車操作） |
| D-3 | 他3台が停止していないと単車操作に入れない |
| D-4 | `velocity_status` が届かないときに「停止している」と誤判定しない。無音を停止扱いしない |
| D-5 | 停止確認が崩れたらパークへ落ちる。ただし停止プロトコルを踏む |
| D-6 | GUI 操作なしに 送信先が広がらない |

### E. 並行性

GUI スレッドが 送信先を書き換え、joy コールバックが送信先を読む。送信先を不変オブジェクトで丸ごと差し替える設計にすれば、途中状態で publish される事故は構造的に起きない。

### F. GUI メッセージ

表示が危険側に誤ると、オペレータが誤った状況認識のまま操作する。メッセージ自体をテスト対象にする。

| # | 観点 |
| --- | --- |
| F-1 | 表示が `status()` の出力と1対1。GUI 側にロジックが無い |
| F-2 | 「単車操作に入れる」と表示する条件が、実際に 送信先を広げる条件と同一（同じ関数から導出されている） |
| F-3 | emergency 確認が5秒取れないとき、対象車両IDを含む警告が出る |
| F-4 | 危険側の誤表示が無い。条件を満たしていないのに「準備OK」と出ない |
| F-5 | `velocity_status` が途絶した車両を「停止」と表示しない。「不明」を出す |
| F-6 | 条件が解消したら消え、解消していないのに消えない |
