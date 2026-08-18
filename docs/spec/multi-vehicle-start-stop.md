# 実機複数台走行開始・停止機能開発

> 出典: [Confluence AI/実機複数台走行開始・停止機能開発](https://tier4.atlassian.net/wiki/spaces/AI/pages/5464555535)
> 転記日: 2026-08-13（Confluence 上の最終更新: 2026-08-12）

## 要件

- **REQ-01** レース開始時の4台の同時発進を遠隔操作PCによってトリガーできること（実装は台数を固定せず、起動時に指定した対象車両すべてを同時発進させる）
- **REQ-02** いつでも遠隔操作PCの操作で全車両を同時に緊急停止できること
- **REQ-03** 操作する車両以外が停止していれば、いつでも遠隔操作PCの操作で任意の車両を遠隔手動操作できること
- **REQ-04** 遠隔操作PCとどれかの車両の通信が5秒以上途絶した場合、車両が緊急停止すること（従来通り）
- **REQ-05** 4台が同時に自動走行し続けられること

## 遠隔SD ユースケース

遠隔SDの遠隔PCでは、必要なソフトが立ち上がっている前提。

> 図: ユースケース図（Confluence 添付画像、952x674）。本ファイルには未転記。

### 特定の車両を遠隔手動操作したいとき

1. 全ての車両が停止していることを確認する
2. GUIで操作したい車両を選択する
3. 左右スティックの押し込み同時押しで、緊急停止を解除する
   - ※緊急停止を解除したあとの車両の状態は「MANUAL」（既存仕様）
4. スティックで操作する

### 全車両を同時発進させたいとき

1. 全ての車両が停止していることを確認する
2. GUIで「一斉発進準備完了ボタン」を押す
3. 左右スティックの押し込み同時押しで、全車両の緊急停止を解除する
4. 自動走行ボタン（`ButtonY`）を押す

### 全車両を緊急停止させたいとき

1. 緊急停止ボタン（`ButtonLB` / `ButtonRB` / `ButtonStart` / `ButtonBack`）のどれかを押す

## racing_kart_manager 概要設計

遠隔操作PC上に置く単一ノード。起動時に指定した対象車両に対する走行許可と操縦権を、GUIでの選択に従って joy の宛先と内容だけで表現する。

> 図: ノード構成図 → [`node.drawio`](node.drawio)
>
> `joy_node` → `/racing_kart/joy` → `racing_kart_manager` → zenoh_bridge ×4（車両ごとに1プロセス） → zenoh router ×4（車両ごとに別ポート） → 車両側 zenoh_bridge → `racing_kart_driver`。joy は片方向、`velocity_status` と `racing_kart/debug/status` は戻り方向。
>
> 図中のノード名 `racingkarts_manager` とトピック名 `/racingkart/joy` は表記ゆれで、実装は既存に合わせて `racing_kart_manager` / `/racing_kart/joy` とする。

### 設計

**対象車両は起動時に引数で渡す。** 台数も車両IDも固定しない。

```bash
manager.bash manager A2 A3 A7      # 3台で動かす
manager.bash manager A2 A3 A6 A7   # 4台
```

常に4台を使うとは限らない。A6 の EC2 ルータのポートが動いていない期間があり、2台だけで
試したい場面もある。固定にすると、使わない車両が永久に `UNKNOWN` のままになって
**全操作が塞がれる**（停止確認が取れないため）。

車両リストは安全側の判定（単車操作に入るとき「対象以外が停止しているか」）の入力なので、
純関数には**引数で明示的に渡す**。`observations` のキーから暗黙に導くと、呼び出し側が
一部の車両だけ渡したときに黙って対象台数が減り、少ない台数で判定が成立してしまう。

| 関数 | シグネチャ |
| --- | --- |
| `spec_for` | `(state, vehicles) -> TransformSpec` |
| `status` | `(state, observations, joy, vehicles) -> Status` |
| `next_state` | `(state, event, observations, joy, vehicles) -> ManagerState` |

GUI は引数を取らない。status の `vehicles[]` からボタンを作るので、台数に自動で追随する。

1. racing_kart_manager ノードは、joy_node の `/racing_kart/joy` を購読し、サブスクリプションコールバック内で（タイマー駆動しない）、走行を許可する車両の名前空間 `/<VEHICLE_ID>/racing_kart/joy` へ rename して publish する。
   - **前提: `joy_node` の `autorepeat_rate` を launch で明示的に設定すること。** タイマー駆動しない設計なので、joy が自動再送されないとオペレータが手を止めた瞬間に送信が止まり、5秒後に全車が緊急停止する。現在の `racing_kart_launch/launch/racing_kart_manager.launch.xml` は `deadzone` しか設定しておらず、上流の既定値に依存している（ヘッダのメンバ初期値は `0.0`、同梱の `joy-params.yaml` は `20.0`）。`20.0` を明示する。
2. **モードは「joy をどう変換するか」だけで定義する。** 軸を無操作値で上書きするか実値のまま通すかは**モードから決まる**。ボタンはどのモードでも常に素通しする。

   | モード | 送信先 | 軸の扱い |
   | --- | --- | --- |
   | パーク | なし | publish しない |
   | 一斉 | 対象車両全部 | 無操作値で上書きし、スティック入力を捨てる |
   | 単車操作 | 対象1台 | スティックの実値をそのまま通す |
   | 停止中 | 縮める前のまま | 無操作値で上書き |

   スティックで操縦するのは単車操作だけ。一斉モードで無操作値にするのは、スティック1本で
   複数台を同時にステアリングできないため。同じ舵角を全車へ送っても全部が同じ方向に曲がる
   だけで操縦にならない。

   **対象車両が1台のときも一斉モードでは無操作値にする。** 送信先の台数で切り替えると、
   1台構成のときだけ一斉モードが単車操作と同じ挙動になり、モードの意味が崩れる。
3. 一斉発進で manager がやることは 送信先を対象車両全部にすることだけ。車両は emergency 状態なので止まったままで、あとはジョイスティックの緊急停止解除（`LSB+RSB`、左右スティックの押し込み同時押し）と自動走行ボタン（`ButtonY`）を素通しすれば、各車が自力で MANUAL → AUTONOMOUS に入って発進する。**manager はこの2操作を解釈しない。**
4. 緊急停止は既存の緊急停止ボタン（`ButtonLB` / `ButtonRB` / `ButtonStart` / `ButtonBack`）をそのまま使い、joy を送っている全車へこのボタンを通す。1台だけ止める場面はないため宛先は絞らない。joy を送っていない車両は既に emergency 状態なので、結果として対象車両すべてが停止状態になる。送信先を縮めるのは後述の停止プロトコルに従う。
5. 手動操縦は GUI で車両を指定すると 送信先が対象1台になり、他の車両への送信は止まる。`LSB+RSB` を素通しすると対象車が MANUAL になり、以降スティック値を通す。前提条件の判定は後述。
6. 停止しているべき車両の停止が確認できなくなった場合は、manager が自発的にパークへ落とす。これも停止プロトコルに従う。「停止しているべき車両」の範囲はモードごとに異なる（後述のフォールバック条件）。

`racing_kart_driver` は AUTONOMOUS 中も joy の鮮度を要求するため、自動走行中の車両にも joy を送り続ける。

「自動走行発進待機」と「全車自動走行」は joy 変換が同一（対象車両全部へ、軸無操作、ボタン素通し）なので、manager から見て区別しない。両者をまとめて「一斉」モードとする。

`ButtonY` を押しっぱなしのまま一斉モードに入り `LSB+RSB` を押すと、解除の次サイクルで即 AUTONOMOUS に入る（`racing_kart_driver_node.cpp:247`）。manager 側でのマスクはしない。「joy を解釈しない」方針を優先する。

### 停止プロトコル

**送信先から車両を外すことは、その車両を止めることではない。**

`racing_kart_driver` の `on_joy` は `input_ = msg` を代入するだけで（`racing_kart_driver_node.cpp:115-119`）、`on_timer` は `joy_delay_threshold`（5秒）を超えるまで最後の `input_` を使い続ける（`:192-199`）。したがって publish を止めた車両は、**最後に届いた joy の値のまま最大5秒走り続ける**。アクセルを踏んだ joy が最後だったら5秒間加速する。

パークへ落とすときは必ず次の順序を踏む。

1. 緊急停止ボタンを押した状態の joy を、対象車へ送り**続ける**
2. `/<VEHICLE_ID>/racing_kart/debug/status` の `emergency == true`（`VehicleDebug.msg`）を全対象車で確認する
3. 確認できてから publish を止めて送信先を空にする

2 が5秒以内に確認できない車両があった場合は、**publish を止めずに**（止めると最後の joy で走り続けるため）緊急停止ボタンを押した状態の joy を送り続けたまま、GUI に該当車両IDを含む警告メッセージを出す。

この順序は緊急停止ボタン起因のパーク遷移と、設計6の自発フォールバックの両方に適用する。

自発フォールバックではオペレータがボタンを押していないため、manager が `buttons` の緊急停止ボタンを押された状態にする。**`ButtonLB` / `ButtonRB` / `ButtonStart` / `ButtonBack` の4つすべてを `1` にする。** driver は4つを OR で見ているので1つでも足りるが、4つとも `1` にすることで取りこぼしが無くなり、かつ「4つ同時に押されている joy = manager が合成した緊急停止」という署名になって rosbag から後で判別できる（オペレータが4つ同時押しすることは実運用で起きない）。

### フォールバックの条件

「停止しているべき車両」の範囲はモードごとに異なる。`Tri` が `TRUE` でない（`FALSE` または `UNKNOWN`）ことを条件にして、**確認できない場合を安全側に倒す**。

| 現在のモード | 監視対象 | パークへ落とす条件 | 遷移先 |
| --- | --- | --- | --- |
| パーク | — | なし | — |
| 一斉 | — | なし | — |
| 単車操作（対象 `v`） | `v` 以外の全車 | いずれかで `stopped != TRUE` または `emergency != TRUE` | 停止中 |
| 停止中 | 送信中の全車 | （逆向き）全車で `emergency == TRUE` になったらパークへ | パーク |

各モードでこう決めた理由。

- **一斉モードでは速度でもテレメトリ途絶でもフォールバックしない。** 対象車両すべてが自動走行してよいので「停止しているべき車両」が存在しない。テレメトリだけが途絶して joy は届いている状況で自動フォールバックすると、正常なレース中に全車を止めることになり REQ-05 に反する。逆に joy も届いていないなら driver 側が5秒で緊急停止するので放置してよい（REQ-04）。この場合は警告だけ出し、止めるかどうかはオペレータが緊急停止ボタンで判断する
- **単車操作では対象車 `v` 自身を条件に含めない。** `v` は操縦中なので動くのが正常。`v` のテレメトリが途絶しても警告だけにする
- **対象車以外は `stopped` だけでなく `emergency` も見る。** 止まってはいるが emergency が解除されている状態は、いつ動いてもおかしくないため危険。joy を送っていない車両は5秒で emergency に落ちるはずなので、`emergency == FALSE` が続くこと自体が異常
- **パークでは何もしない。** manager は joy を送っていないので介入手段がない。動いている車両があっても、それは joy で止められない異常（VCU 異常、外力）であり、joy を送り始めることは「パーク = joy 送信なし」の定義を壊す。警告を出し、実車側の物理的な非常停止に委ねる

なお、停止中モードで `emergency == UNKNOWN` を「確認できた」扱いにしてはならない。`TRUE` を確認できるまでパークへ行かない。

> 図: モード遷移図 → [`mode.drawio`](mode.drawio)
>
> 図のオレンジ四角が「遠隔PCからのjoy疎通のモード」で、本ファイルの送信先に対応する。図にある10秒タイムアウトは廃止した（図の吹き出し「いらないかもしれない」に従う）。緊急停止ボタンでいつでもパークに戻せるため、意図せず一斉モードに入った場合の保険としては十分。

### 配置とビルド

`aichallenge-racingkart/remote` 配下に置く。遠隔操作のコマンド類がここに集まっているため。

`docker-compose.yml` は `./remote:/remote` をマウントするだけでこのディレクトリをビルド対象にしていない（`remote/gui_tools.py` も同じ扱い）。したがって **Python（rclpy）で書けばビルド工程が発生せず**、編集後はノードの再起動だけで反映される。

| ファイル | 役割 |
| --- | --- |
| `remote/racing_kart_manager_core.py` | 純粋ロジック。型と3つの純関数。**ROS 非依存** |
| `remote/racing_kart_manager.py` | ROS ノード。購読・`Joy` との変換・publish だけの薄い層 |
| `remote/manager.bash` | 起動スクリプト（`joy.bash` と同じ粒度） |
| `remote/racing_kart_manager_gui.py` | GUI。manager とは別プロセスでトピック接続 |
| `remote/tests/` | L1 の pytest |

core を別ファイルに分けているのは、L1 のテストを ROS なしで回すため。`sensor_msgs/Joy` を直接扱うと import に ROS 環境が要るので、core は `JoyValue`（`axes` / `buttons` / `stamp_ns` を持つ frozen dataclass）で受け渡し、ROS 型との相互変換はノード側に閉じる。

テストの実行にはシステムを汚さない `uv` を使う。

```bash
uv run --with pytest --with hypothesis python -m pytest remote/tests -q
```

起動は `docker-compose.yml` に `rviz2` と同じ `*autoware-base` を継承したサービスを足し、`Makefile` にターゲットを追加する。

キーバインド定数は `racing_kart_interface` が別リポジトリのため参照できず複製するが、必要なのは無操作値で上書きする軸の index（`0, 2, 5, 6, 7`）と配列サイズ（buttons 11 / axes 8）だけ。ボタンは素通しするので定数の複製は不要。

### モードごとの joy の中身

| モード | joy 送信先 | スティック軸 | ボタン | 車両側の状態 |
| --- | --- | --- | --- | --- |
| パーク（全車停止） | なし | publish しない | publish しない | 対象車両すべてが emergency 状態 |
| 一斉 | 対象車両全部 | 受け付けない（無操作値で上書き） | 素通し | 直後は対象車両すべてが emergency 状態。`LSB+RSB` でブレーキを踏んでいない MANUAL、`ButtonY` で AUTONOMOUS |
| 単車操作 | 対象1台のみ | 実値 | 素通し | 車両選択直後は emergency 状態。`LSB+RSB` を受けると MANUAL。他の車両は emergency 状態 |
| 停止中 | 縮める前のまま | 無操作値で上書き | **緊急停止ボタンを強制的に押された状態にする** | emergency 確認待ち |

「停止中」は停止プロトコルの実行中を表す。joy 変換が他と異なる（緊急停止ボタンを押された状態にする）ので、「モード = joy 変換」の定義に従い独立したモードとして扱う。自発フォールバックではオペレータがボタンを押していないため、manager 側で押された状態にする必要がある。緊急停止ボタン起因の場合は既に押されているので、強制しても結果は変わらない（冪等）。

これに伴い、変換関数は送信先だけでなく次の3項目を引数に取る。

| 項目 | 決まり方 |
| --- | --- |
| `destinations` | モードから |
| `suppress_axes` | 単車操作以外なら真（一斉と停止中で上書きする） |
| `force_emergency` | 停止中なら真 |

単車操作中は、去年同様 `ButtonA`（MANUAL）/ `ButtonX`（AUTONOMOUS_STEER_ONLY）/ `ButtonY`（AUTONOMOUS）を任意に切り替えて1台を操作できる。ボタンを素通しするだけなので manager 側の対応は不要。

#### 用語の出典

用語はすべて `racing_kart_interface` に合わせる。日本語の機能名は `src/racing_kart_driver/README.md` のマッピング表（アクセル / ブレーキ / ステアリング / 緊急停止ボタン）を使い、識別子は `keybind/joystick.hpp` と `racing_kart_driver_node.cpp` の名前を使う。

ただし同リポジトリ内で README とコードが食い違う箇所が2つある。**コード側を正とする。**

| 項目 | README の記載 | コードの実装 | 本書 |
| --- | --- | --- | --- |
| 軸名 | `AxisLeftX` / `AxisLT` / `AxisRT` | `LeftStickHorizontal` / `LeftTrigger` / `RightTrigger`（`keybind/joystick.hpp`） | コード側 |
| `ButtonB` | 「アクセルのみ自動モード選択」と記載 | モード選択の分岐に `ButtonB` が無い（`racing_kart_driver_node.cpp:243-249`）。`AUTONOMOUS_VELOCITY_ONLY` は `:349` の判定にのみ登場する | コード側。`ButtonB` は無いものとして扱う |

#### 「無操作値」の実際の数値

`racing_kart_driver` はアクセル・ブレーキを `clamp((1.0 - axes[i]) / 2.0, 0, 1)` で解釈する（`racing_kart_driver_node.cpp:345,367`）。**無操作は `0.0` ではなく `+1.0`** で、ゼロ埋めした `Joy` を送るとアクセル50%・ブレーキ50%を踏んだ扱いになる。

| 要素 | index | 無操作値 |
| --- | --- | --- |
| `Accel`（RightTrigger） | axes[5] | `+1.0` |
| `Brake`（LeftTrigger） | axes[2] | `+1.0` |
| `Steer`（LeftStickHorizontal） | axes[0] | `0.0` |
| ギア（DpadHorizontal / DpadVertical） | axes[6], axes[7] | `0.0` |

ギアの Dpad は AUTONOMOUS 中は無視されるが、一斉モードで `LSB+RSB` 直後の MANUAL 状態では効いてしまうため無操作値で上書きする対象に含める。

また `racing_kart_driver` は `buttons.size() == 11` かつ `axes.size() == 8` を厳密に要求し、違うと停止指令に落ちる（`racing_kart_driver_node.cpp:186-187`）。manager が組み立てる `Joy` は必ずこのサイズにする。

### モード遷移

| 遷移 | 操作 | 前提条件 | manager がやること |
| --- | --- | --- | --- |
| パーク → 一斉 | GUIの一斉発進準備完了ボタン | 対象車両すべての emergency 状態と停止 | 送信先を対象車両全部にする |
| パーク → 単車操作 | GUIで車両選択 | 対象車以外すべての emergency 状態と停止、スティック無操作 | 送信先を対象1台にする |
| 任意 → パーク | ジョイスティックの緊急停止ボタン（`ButtonLB` / `ButtonRB` / `ButtonStart` / `ButtonBack`） | なし（いつでも可） | 停止プロトコルに従う |
| 単車操作 → パーク | 対象車以外のいずれかで `stopped != TRUE` または `emergency != TRUE` | なし（manager が自発的に判定） | 停止プロトコルに従う |
| 一斉 ↔ 単車操作 | 直接は不可 | パークを経由する必要あり | — |
| 単車操作 → 別の単車操作 | 直接は不可 | パークを経由する必要あり | — |

表記載以外のモード遷移は不可。`LSB+RSB` による緊急停止解除と `ButtonY` による自動走行開始はモード遷移ではない。manager はこれらを素通しするだけで解釈せず、結果として車両側の状態が変わる。

### manager が判定しなくてよいこと

`racing_kart_driver` 側の既存挙動で担保されるため、manager 側では実装しない。

- **「対象車両すべての緊急停止が解除済み」の確認**：解除し忘れた車は `is_emergency_` が立ったままなので、`ButtonY` を受けても `MANUAL` のまま停止し続ける（`racing_kart_driver_node.cpp:241`）。安全側に倒れる
- **`/control/command/actuation_cmd` が publish されていることの確認**：車両ローカルの話で manager からは見えない。車両側の責務とする
- **joy 途絶時の緊急停止**：`joy_delay_threshold` 超過で driver 自身が `is_emergency_ = true` かつ `input_` を空にし、以降 `is_joystick_available()` が false になって停止指令を出し続ける（`racing_kart_driver_node.cpp:192-199`）

manager が joy 以外に購読するのは次の3つ。前2つは遷移の判定に使い、3つ目は表示にのみ使う。

| トピック | 型 | 用途 |
| --- | --- | --- |
| `/<VEHICLE_ID>/vehicle/status/velocity_status` | `autoware_auto_vehicle_msgs/VelocityReport` | 停止しているかの判定 |
| `/<VEHICLE_ID>/racing_kart/debug/status` | `racing_kart_msgs/VehicleDebug` | 緊急停止が有効かの判定。停止プロトコルの確認に使う |
| `/<VEHICLE_ID>/vehicle/status/control_mode` | `autoware_auto_vehicle_msgs/ControlModeReport` | 車両1台分の表示のみ。遷移の判定には使わない |

### Status の定義

`status()` は GUI 描画と モード遷移の判定の**両方**が使う唯一の判断材料。GUI が独自に条件式を持たないことで、表示と判定の乖離を構造的に防ぐ。

名前は `racing_kart_interface` に揃える。同リポジトリには `latch` という語が1箇所も存在せず、`is_emergency_`（`racing_kart_driver_node.hpp:93`）と `VehicleDebug.emergency` で統一されている。解除側の語彙も driver の `// Check emergency clear button`（`racing_kart_driver_node.cpp:276`）に合わせて `CLEARED` を使う。

```python
class Tri(Enum):
    """テレメトリ由来の値。UNKNOWN を TRUE にも FALSE にも倒さない。"""
    TRUE = auto()
    FALSE = auto()
    UNKNOWN = auto()

class Mode(Enum):
    PARK = auto()      # 送信先なし
    ALL = auto()       # 送信先は対象車両全部、軸は無操作値
    SINGLE = auto()    # 送信先1台、軸は実値
    STOPPING = auto()  # D は縮める前のまま、軸無操作 + 緊急停止ボタン強制

class BlockerCode(Enum):
    """遷移を許可できない理由。"""
    VEHICLE_MOVING = auto()             # 速度が閾値以上
    VEHICLE_STATE_UNKNOWN = auto()      # テレメトリ途絶で停止 / emergency を判定できない
    VEHICLE_EMERGENCY_CLEARED = auto()  # emergency == false（解除されている）
    STICK_IN_USE = auto()
    JOY_STALE = auto()
    NOT_IN_PARK = auto()                # パーク以外からの遷移は不可

class AlertCode(Enum):
    """進行中の異常。遷移可否とは独立に出す。"""
    EMERGENCY_CONFIRM_TIMEOUT = auto()   # 停止プロトコルで 5 秒たっても emergency を確認できない
    TELEMETRY_LOST = auto()
    JOY_STALE = auto()

@dataclass(frozen=True)
class VehicleStatus:
    vehicle_id: str
    receiving_joy: bool           # D に入っているか
    stopped: Tri                  # velocity_status の longitudinal_velocity から
    emergency: Tri                # debug/status の VehicleDebug.emergency をそのまま
    velocity_age_s: float | None  # None = 一度も受信していない
    debug_age_s: float | None

@dataclass(frozen=True)
class Blocker:
    code: BlockerCode
    vehicles: tuple[str, ...] = ()

@dataclass(frozen=True)
class Alert:
    code: AlertCode
    vehicles: tuple[str, ...] = ()

@dataclass(frozen=True)
class Status:
    mode: Mode
    selected: str | None                # SINGLE のときの対象
    vehicles: tuple[VehicleStatus, ...] # 常に4台分
    alerts: tuple[Alert, ...]
    stopping_elapsed_s: float | None    # STOPPING のときの経過秒

    # モードに入れない理由。空なら入れる
    enter_all_mode_blockers: tuple[Blocker, ...]                   # Mode.ALL へ
    enter_single_mode_blockers: Mapping[str, tuple[Blocker, ...]]  # Mode.SINGLE へ（車両ごと）

    @property
    def can_enter_all_mode(self) -> bool:
        return not self.enter_all_mode_blockers

    def can_enter_single_mode(self, vehicle_id: str) -> bool:
        return not self.enter_single_mode_blockers[vehicle_id]
```

可否は**保持せず blocker から導出する**。`can_enter_all_mode` という真偽値を別に持つと、blocker の判定と食い違ったときに「理由は出ているのにボタンが押せる」状態が作れてしまう。

`Status` を `frozen` にして丸ごと差し替えることで、GUI スレッドが読んでいる途中に joy コールバックが書き換える競合も起きない（観点 E）。

#### blocker の読み方

`*_blockers` は「そのモードに入れない理由」で、空なら入れる。A3 が動いていて A6 のテレメトリが途絶している状況なら、一斉モードについてはこうなる。

```python
status.enter_all_mode_blockers == (
    Blocker(VEHICLE_MOVING, ("A3",)),
    Blocker(VEHICLE_STATE_UNKNOWN, ("A6",)),
)
status.can_enter_all_mode  # False
```

GUI はこの1つのデータから、ボタンを押せなくすることと「A3 が停止していません / A6 の状態が不明です」と理由を出すことの両方をやる。

単車操作だけ車両ごとの辞書になっているのは、前提条件が「**対象車以外がすべて**停止していること」で、どの車を選ぶかによって見る相手が変わるため。A2 だけが動いている状況ではこうなる。

```python
status.enter_single_mode_blockers == {
    "A2": (),                                   # A2 以外(A3,A6,A7)は停止 → 選べる
    "A3": (Blocker(VEHICLE_MOVING, ("A2",)),),  # A3 以外を見ると A2 が動いている → 選べない
    "A6": (Blocker(VEHICLE_MOVING, ("A2",)),),
    "A7": (Blocker(VEHICLE_MOVING, ("A2",)),),
}
```

GUI では A2 のボタンだけが押せて、他はグレーアウトし「A2 が停止していません」と表示される。これは設計上の意図どおりで、**動いてしまっている車をつかまえて操縦する**のが正しい操作になる。対象車自身も前提条件に含めていたら、暴走している A2 を誰も操作できなくなる。

#### Status が満たすべき不変条件

テストはこの不変条件を検証する。個別のメッセージ文言ではなく条件を検証することで、文言を変えてもテストが壊れない。

| # | 不変条件 |
| --- | --- |
| INV-1 | `Tri.UNKNOWN` の車両が1台でもあれば `can_enter_all_mode` は偽 |
| INV-2 | `stopped` が `TRUE` でない車両は、必ずいずれかの blocker の `vehicles` に現れる |
| INV-3 | `can_enter_single_mode(v)` が真なら、`v` 以外のすべてが `stopped == TRUE` かつ `emergency == TRUE` |
| INV-4 | `mode != PARK` なら `can_enter_all_mode` は偽で、全車の `can_enter_single_mode` も偽 |
| INV-5 | `mode == STOPPING` かつ `stopping_elapsed_s >= 5.0` なら、emergency 未確認車両IDを含む `EMERGENCY_CONFIRM_TIMEOUT` が `alerts` にある |
| INV-6 | `blocker.code` が `VEHICLE_*` のとき `vehicles` は非空 |

INV-1 と INV-2 が「危険側の誤表示が無い」（観点 F-4, F-5）の実体。Unknown が必ず blocker を生むので、テレメトリが途絶した状態で「準備OK」と表示されることが型と不変条件の両方で塞がれる。

#### パラメータ

zenoh の `pub_max_frequencies: ["/*=10"]` により、遠隔PCと車両の間の全トピックは 10 Hz に間引かれる。テレメトリ関連の閾値はこれより十分長くとる。

| パラメータ | 用途 | 値 |
| --- | --- | --- |
| `telemetry_timeout_s` | これを超えたら `Tri.UNKNOWN` | 1.0 |
| `stopped_speed_threshold_mps` | `VelocityReport.longitudinal_velocity` の絶対値がこれ未満なら停止 | 0.1 |
| `emergency_confirm_timeout_s` | 停止プロトコルで警告を出すまで | 5.0 |
| `stick_no_input_tolerance` | 単車操作に入る前提のスティック無操作の判定 | ステアリング 0.1、アクセル・ブレーキは 0.9 以上 |

### GUI インタフェース

GUI は manager とは**別プロセス**にし、ROS トピック2本で繋ぐ。`racing_kart_msgs` は別リポジトリで専用 `.msg` を作るとビルドが必要になるため、`std_msgs/String` に JSON を載せてビルド不要を維持する。

別プロセスにする理由は3つ。

- **GUI が落ちても manager は joy を流し続ける。** 同一プロセスにすると GUI のバグで joy が止まり、5秒後に全車が緊急停止する。安全側ではあるが、レース中に起こしたくない
- **rosbag に status と command が残る。** 事故解析で「そのとき GUI に何が表示され、オペレータが何を押したか」を再現できる。観点 F の検証に直接効く
- 緊急停止はジョイスティック側なので、GUI が落ちても止められる

#### トピック

| トピック | 型 | 向き | QoS |
| --- | --- | --- | --- |
| `/racing_kart_manager/status` | `std_msgs/String`（JSON） | manager → GUI | reliable, transient_local, depth 1 |
| `/racing_kart_manager/command` | `std_msgs/String`（JSON） | GUI → manager | reliable, depth 10 |

`transient_local` にするのは、GUI を後から起動しても即座に最新状態が出るようにするため。command を depth 10 にするのは、ボタン押下を取りこぼさないため。

**この2本は遠隔PCローカルに閉じる。** zenoh の allow リストに追加してはならない。車両側は一切関与しない。

#### status のスキーマ

```json
{
  "schema_version": 1,
  "stamp_ns": 1770000000000000000,
  "mode": "SINGLE",
  "selected": "A2",
  "stopping_elapsed_s": null,
  "vehicles": [
    {
      "vehicle_id": "A2",
      "receiving_joy": true,
      "stopped": "FALSE",
      "emergency": "FALSE",
      "velocity_age_s": 0.12,
      "debug_age_s": 0.13,
      "control_mode": "MANUAL",
      "label": "MANUAL / 停止中 / 緊急停止 解除 / joy 送信中"
    }
  ],
  "can_enter_all_mode": false,
  "can_enter_single_mode": { "A2": false, "A3": false, "A6": false, "A7": false },
  "messages": [
    { "level": "warn", "targets": ["all", "A2", "A6", "A7"], "text": "A3 が停止していません" },
    { "level": "error", "targets": [], "text": "A6 のテレメトリが途絶しています" }
  ]
}
```

`stopped` と `emergency` は `Tri` の名前をそのまま文字列にする（`"TRUE"` / `"FALSE"` / `"UNKNOWN"`）。真偽値にすると `UNKNOWN` を表現できず、途絶を停止扱いする事故につながる。

**`messages` の `targets` は、そのメッセージがどの操作を塞いでいるかを表す。** `"all"` は一斉発進準備完了ボタン、車両IDはその車両の選択ボタン、空配列は特定の操作に紐づかない全体の警告。これが無いと GUI が文言を解析して振り分けることになり、観点 F-1（GUI 側にロジックを持たせない）が破れる。

**複数形なのは、1つの理由が複数の操作を同時に塞ぐため。** A3 が動いていると、一斉発進準備完了と A2 / A6 / A7 の選択がまとめて塞がる。操作ごとに1件ずつ作るとメッセージ表示エリアに同じ文が4回並び、GUI 側で重複を除くことになって F-1 が破れる。文言は1件にまとめ、対象を並べる。

GUI は各ボタンの近くに出すときだけ `targets` で絞り、メッセージ表示エリアにはそのまま全件並べる。

`Blocker` と `Alert` は `messages` に一本化する。GUI から見れば「どこに何を出すか」しか要らず、由来を区別する必要がないため。

#### command のスキーマ

```json
{ "schema_version": 1, "command": "enter_single_mode", "vehicle_id": "A2" }
```

`command` は `enter_all_mode` / `enter_single_mode` の2種。`vehicle_id` は `enter_single_mode` のときだけ使う。

- **冪等。** 連打しても `next_state` が現状維持を返すだけで害はない
- **不正な値は破棄する。** 未知の `command`、`VEHICLES` に無い `vehicle_id`、JSON パース失敗はいずれもログを出して何もしない。安全側に倒れる
- **`schema_version` が一致しないものは破棄する。** GUI 側も status の `schema_version` が想定と違えば「非対応バージョン」を表示して全ボタンを非活性にする。片方だけ更新して黙って誤動作するのを防ぐ
- **GUI が複数起動していても先着順に処理する。** どの GUI も同じ status を見ているので状態は割れない

#### 車両1台分の表示

各車両ボタンの下に、その車両の状態を1行で出す。文言は manager 側で作り、GUI は受け取った文字列を描くだけにする（観点 F-1）。

```
<制御モード> / <走行中|停止中|不明> / 緊急停止 <有効|解除|不明> / joy <送信中|送信なし>
```

4項目とも常に出す。開発・調整で使うことを優先し、状態が読み取れることを情報量より上に置かない。

| 例 | 意味 |
| --- | --- |
| `MANUAL / 停止中 / 緊急停止 有効 / joy 送信なし` | パーク中の正常な状態 |
| `MANUAL / 停止中 / 緊急停止 有効 / joy 送信中` | 一斉モードに入った直後。`LSB+RSB` 待ち |
| `MANUAL / 停止中 / 緊急停止 解除 / joy 送信中` | 解除済み。`ButtonY` で発進する |
| `MANUAL / 停止中 / 緊急停止 解除 / joy 送信なし` | **止まっているが、いつでも動きうる。** joy も送っていないので manager から介入できない |
| `AUTONOMOUS / 走行中 / 緊急停止 解除 / joy 送信中` | 自動走行中 |
| `不明 / 不明 / 緊急停止 不明 / joy 送信なし` | テレメトリ途絶 |

制御モードは `autoware_auto_vehicle_msgs/ControlModeReport` の名前をそのまま英語で出す（`MANUAL` / `AUTONOMOUS` / `AUTONOMOUS_STEER_ONLY` など）。Autoware のログや rviz と突き合わせやすいため。

**緊急停止を別項目として省かない理由。** driver は emergency のとき制御モードに `MANUAL` を強制する（`racing_kart_driver_node.cpp:241-242`）。したがって `MANUAL` だけでは「緊急停止で止まっている（安全）」と「解除済みでいつでも動きうる（注意）」が区別できない。後者は単車操作に入れない条件であり、自発フォールバックの発火条件でもあるため、常に出す。

制御モードも `stopped` / `emergency` と同じく、受信からの経過時間が `telemetry_timeout_s` を超えたら「不明」にする。無音を既定値に倒さない。

4項目目の joy だけは車両の状態ではなく **manager 側の情報**（その車を送信先に含めているか）。由来が違うが、「今この車に何が起きているか」を1行で追えるほうが開発中は有用なので同じ行に並べる。

行は長くなるのでボタン下では折り返す。

この表示のために必要な変更。

| 箇所 | 内容 |
| --- | --- |
| `vehicle/zenoh.json5` の `publishers` | `/vehicle/status/control_mode` と `/racing_kart/debug/status` を追加。**現在はどちらも入っておらず、遠隔PCへ流れていない** |
| `remote/zenoh-user.json5.template` の `subscribers` | 同じ2つを追加 |
| manager の購読 | `/<VEHICLE_ID>/vehicle/status/control_mode`（型は遠隔PCのイメージに既にあり、ビルド不要） |
| `VehicleObservation` | 制御モードの値と、受信からの経過時間 |
| `VehicleStatus` と status の JSON | 制御モード名（`control_mode`）と、表示用の1行（`label`） |
| GUI | `Tri` から文言への変換表を削除し、`label` を描くだけにする |

#### 責務の分け方

観点 F-1（GUI 側にロジックを持たせない）を守るため、**表示文言まで manager 側で作って送る**。GUI は受け取ったものを並べるだけにする。

| 担当 | 内容 |
| --- | --- |
| core | `render_messages(status) -> tuple[Message, ...]` で文言を作る。`Message` は `level`（`info` / `warn` / `error`）、`targets`（`"all"` / 車両IDの並び）、`text`。JSON 化も `status_to_json()` / `parse_command()` として core に置き、純関数のままテストする |
| ノード | `status_to_json()` の結果を publish し、受信した文字列を `parse_command()` に通して `Event` にする。ROS 依存はここだけ |
| GUI | JSON を描画するだけ。ボタンの活性・非活性も `can_*` をそのまま使い、メッセージは `targets` で振り分ける |

これにより GUI に条件式が1つも要らなくなる。**例外は2つだけ**、下記の status 途絶検出と `schema_version` 不一致。

#### GUI 側に唯一必要なロジック

manager が落ちたり通信が途絶した場合、GUI には最後の status が残り続ける。これは manager 自身からは送れないので、**GUI が status を最後に受け取ってからの経過時間を見て、`status_timeout_s`（1.0）を超えたら画面全体を「manager と通信できていません」状態にし、全ボタンを非活性にする**。

古い status をそのまま表示し続けるのが最も危険なので、ここだけは GUI 側の責務とする。

判定そのものは core の純関数 `gui_gate(status_age_s, schema_version) -> GuiGate` に置く。tkinter の中に条件式を書くとテストできなくなるため、GUI 本体は結果を描画するだけにする。`schema_version` 不一致も同じ関数で扱う。

#### 押下から反映までの扱い

コマンドを送ってから manager が処理して status が返るまでラグがある。**GUI はボタン押下を楽観的に画面へ反映してはならない。** 表示が変わるのは新しい status を受け取ったときだけとする。押したのに変わらなければ、それは遷移が許可されなかったということで、理由は `messages` に出る。

#### 呼び出しタイミング

`next_state` は **joy コールバックと command 受信でのみ**呼ぶ。テレメトリ購読は観測を溜めるだけにする。joy は 20 Hz 来るので自発フォールバックの判定もそこで間に合い、設計1の「タイマー駆動しない」が保たれる。

`status` の publish だけは 5 Hz のタイマーで回す。joy の送出経路ではないので生存チェーンの原則には抵触しない。

**executor は `rclpy.spin()` の既定である `SingleThreadedExecutor` をそのまま使い、コールバックグループも分けない**（`rclpy/__init__.py:105-106`）。joy・command・テレメトリが単一キューを到着順に流れるため、オペレータの操作順と実行順が一致する。コマンドに順序ガード（シーケンス番号のエコーなど）を持たせていないのはこのため。`MultiThreadedExecutor` へ変えるとこの保証が消えるので、変えない。

#### GUI に置かないもの

**「パークへ戻す」ボタンは置かない。** パークへ戻す手段はジョイスティックの緊急停止ボタンと自発フォールバックだけとする。単車操作を終えて一斉発進に移るときも緊急停止ボタンを押す。

停止プロトコルと処理が同一である以上、GUI に置いても「緊急停止ボタンと同じことをする2つ目のボタン」が増えるだけで、押し分けの判断をオペレータに強いることになる。止める操作の入口をジョイスティック1箇所に集約したほうが、緊急時に迷わない。

#### GUI インタフェースのパラメータ

| パラメータ | 用途 | 値 |
| --- | --- | --- |
| `status_publish_rate_hz` | status の publish 周期 | 5.0 |
| `status_timeout_s` | GUI がこれを超えて status を受け取らなければ全ボタン非活性 | 1.0 |

### 要件との対応

| 要件 | 対応 |
| --- | --- |
| REQ-01 一斉発進 | 設計3 |
| REQ-02 一斉緊急停止 | 設計4 |
| REQ-03 任意の1台を手動操縦（全車停止時のみ） | 設計5 |
| REQ-04 5秒途絶で緊急停止 | 設計1。既存の `joy_delay_threshold: 5.0` をそのまま使う。サブスクリプションコールバック駆動にすることで、ジョイスティック → joy_node → manager → bridge → 車両の全区間が生存チェーンに入る |
| REQ-05 4台同時自動走行 | 設計1（4台へ joy を送り続ける）+ 設計3 |

### 名前空間の付け方

vehicle 側の bridge に `namespace: "/<VEHICLE_ID>"` を設定する。

| 方向 | 経路 |
| --- | --- |
| joy | remote の `/A2/racing_kart/joy` → zenoh キー `A2/racing_kart/joy` → vehicle 側 bridge が剥がして車両ローカルの `/racing_kart/joy` |
| 戻り | 車両ローカルの `/vehicle/status/velocity_status` → zenoh キー `A2/vehicle/status/velocity_status` → remote の `/A2/vehicle/status/velocity_status` |

戻り方向は3トピック（`/vehicle/status/velocity_status`、`/racing_kart/debug/status`、`/vehicle/status/control_mode`）。いずれも同じ経路をとる。

## テスト設計

車両が意図せず動くことを防ぐのが目的。driver 側から逆算すると、車両が動くのは `is_initialized_` かつ `is_joystick_available()`（buttons 11 / axes 8）かつ `is_emergency_ == false` の3条件が揃ったときだけで、踏む量は `input_`（最後に受け取った joy）から決まる。manager が危険を作れるのは、この3条件を立てるべきでないときに立てるか、`input_` に踏んだ値を残すかのどちらか。

### テスト容易性から来る実装要請

観点 A・C・D・F の大半は ROS を起動せずに検証できる。そのために manager を次の純関数に割り、ROS 依存は「購読して呼んで publish する」薄い層だけに閉じる。

| 関数 | 責務 | カバーする観点 |
| --- | --- | --- |
| `transform(joy, spec) -> dict[vehicle_id, JoyValue]` | joy 変換。`spec` は `destinations` / `suppress_axes` / `force_emergency` | A, C |
| `next_state(state, event, observations, joy) -> ManagerState` | モード遷移の決定 | D |
| `status(state, observations, joy) -> Status` | モード・各遷移の可否・不許可の理由・警告 | F |

`status()` は GUI 描画と モード遷移の判定の**両方**が使う。GUI が独自に条件式を書くと判定と表示が乖離し、オペレータが誤った状況認識のまま操作するため。

### テスト観点とテストケース

観点の一覧は [`multi-vehicle-start-stop-test.md`](multi-vehicle-start-stop-test.md) の付録に、ハザード分析・具体的なテストケース・トレーサビリティ・出口基準は同ドキュメント本体にまとめた。

## zenoh 設定を各側1本にまとめる

遠隔側・車両側それぞれ**設定ファイルを1本だけ持ち、全車で共有する**。車両ごとに違う値は
設定ファイルの外から渡す。

> **本ドキュメントの AWSIM リハーサル（`make dev3-remote`）に関する記述は、まだこのリポジトリに
> 入っていない。** リハーサル環境と `racing_kart_sim_adapter` は `feat/racing-kart-sim-adapter`
> ブランチで進行中で、ここでは実機側の設定統一だけを扱う。

設定を分ける動機は元々リハーサルにあった。**リハーサルの目的は実機テストのトラブルを減らすこと**
であり、リハーサルで通る設定と実機で使う設定が違えば目的を達成できない。実際、許可リストに
`racing_kart/debug/status` と `vehicle/status/control_mode` を追加したとき、実機側だけ直して
sim 側が漏れるという乖離が1日で発生した。`pub_priorities` も実機側にしか無く、輻輳時の挙動が
違っていた。そこで sim 専用の設定を別に持たず、リハーサル側も実機と同じ設定を使う方針にした。

車両ごと・環境ごとに本当に違うのは次の3つだけで、いずれも設定ファイルの外で渡せる。

| 違い | 渡し方 |
| --- | --- |
| 接続先ポート | CLI の `-e tls/<host>:<port>` |
| ROS ドメインID | 環境変数 |
| 車両の名前空間 | CLI の `-n /<VEHICLE_ID>`（車両側のみ） |

したがって設定ファイルは各側1本で足りる。

| ファイル | 使う側 |
| --- | --- |
| `vehicle/zenoh.json5` | `vehicle/run_zenoh.bash` |
| `remote/zenoh-user.json5.template` | `remote/connect_zenoh.bash`（車両IDを置換して生成） |

遠隔側の bridge には名前空間を付けず、車両側を `-n /<VEHICLE_ID>` で起動する。この非対称性が
両者を噛み合わせている。遠隔側はトピック名にあらかじめ車両IDが入っており、車両側の bridge が
それを剥がして車両ローカルの `/racing_kart/joy` に戻す。

## racing_kart_manager 以外の変更

### aichallenge-racingkart/remote

- `remote/zenoh-user.json5.template`: 遠隔側の設定を**テンプレート1本**にまとめる。`__VEHICLE_ID__` を車両IDで置換して使う。トピック名は名前空間付き（`/<VEHICLE_ID>/racing_kart/joy` など）で、`subscribers` に `racing_kart/debug/status` と `vehicle/status/control_mode` を含める。
- `remote/connect_zenoh.bash`: 対象車両の数だけこのスクリプトを起動する。1回の起動が1台分の bridge に対応し、テンプレートから自車用の config を生成する。
- GUI（`remote/racing_kart_manager_gui.py`）: 操作対象車両の選択と一斉発進準備完了のボタン。manager とは別プロセスで、接続は「GUI インタフェース」節を参照。準備完了かどうかと前提条件の判定結果を表示して、許可されない理由が分かるようにする。レイアウト案は [`gui.drawio`](gui.drawio)。左に車両選択ボタン（`A2` `A3` / `A6` `A7`）、右に「一斉発進準備完了」ボタン、下にメッセージ表示エリア。

### aichallenge-racingkart/vehicle

- `vehicle/zenoh.json5`: 名前空間は設定ファイルに書かず、`run_zenoh.bash` から **`-n "/<VEHICLE_ID>"` で渡す**。こうすると設定ファイルに車両固有の内容が無くなり、全車で1ファイルを共有できる。`allow` リストは bridge のローカル側から見た名前で照合されるため名前空間なしのままでよいが、`publishers` へ `/racing_kart/debug/status` と `/vehicle/status/control_mode` を追加する。
