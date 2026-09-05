# MPC 走行チューニング TUI

> 設計ドキュメント。**実装は進行中**（この PR で段階的に入れる）。
> 文書運用方針は [docs/README.md](../README.md) を参照。

走行枠のあいだ、MPC のパラメータを**走らせながら**詰めるための設計。
ROS 2 のパラメータ機構（`ParameterDescriptor` と `SetParameters` 系サービス）を界面とし、
その上に端末で動く調整用フロントエンドを載せる。

## 背景と課題

実車の走行枠では路面μ・タイヤ温度・GNSS 遅延が日によって変わるため、
机上で決めた値がそのまま使えない。実際に触りたいのは 2 つに絞られる。

1. **スピード上限** — `v_max`（直線の頭打ち）、`ay_max`（横 G 上限、コーナー速度そのもの）、
   区間ごとに落とすなら `ref_vel/<section>/ref_vel`
2. **コース取りのアグレッシブさ** — `Q0`（横偏差の重み。下げるほどコリドー中心から離れてよい）、
   `Q2`（時間の重み。上げるほどタイム優先）、`width`（車両幅＋安全マージン。下げるほど壁に寄れる）

`config.yaml` のプリセット（低速〜究極）も、速くなるほど `Q0` が下がり `Q2` が上がるという
同じ 2 軸で動いている。`Q1` はほぼ 1e8 固定、`R1` は 0.0 で死んでいる。

ここに 3 つの障害があった。

- **`v_max` の動的更新が壊れていた。** `MPCConfig.v_max` は m/s で保持されるのに、
  param コールバックが km/h の生値を代入していた。`_mpc_cfg.v_max` を直接読む 2 箇所
  （ref_vel との min、制御無効時の減速 clip）が m/s と km/h の比較になり、上限が 3.6 倍に
  緩んで実質無効化されていた。**走行中に速度を絞るという一番やりたい操作が効かなかった。**
- **`width` が動的化されていない。** yaml 編集と再起動が要り、走行枠のあいだに試せない。
- **操作手段が `ros2 param set` の生打ちのみ。** 名前と単位を覚えて打つことになり現在値も
  見えず、コースサイドの数十秒で回す作業に向かない。`rqt_reconfigure` は X 転送が要る。

## 対象と非対象

対象は `multi_purpose_mpc_ros` のパラメータ調整だけである。

| 領域 | 担当 |
|------|------|
| MPC パラメータの動的化と調整 UI | **本 spec** |
| 走行枠のオペレーション（build / 起動 / 片付け） | [vehicle-tui.md](vehicle-tui.md) |
| MPC のアルゴリズムそのもの | [mpc-integration.md](mpc-integration.md) |
| 経路生成（`traj_mincurv.csv`） | 本 spec の対象外 |

## 設計方針

1. **メタデータの正本は ROS 2 の `ParameterDescriptor` に置く。** レンジ・単位・説明・read_only を
   ノード側が宣言し、フロントエンドは読んで描いて送るだけにする。TUI に定義表を持たせると
   二重管理になり、単位の正本が 2 つになる。
2. **安全境界は config に置かない。** レンジは「この車両で安全に設定できる範囲」であって、
   走行ごとに変えるものではない。config.yaml に置くと「レンジを広げてから危険な値を入れる」が
   config 編集だけで通り、descriptor を安全装置として置いた意味が消える。
3. **走行中に効かせる。** 停車を要求しない。そのために重い再計算は事前に潰しておく（後述）。
4. **戻せるようにする。** 即時反映に dry run は無いので、undo で担保する。
5. **純ロジックを分離する。** 増減計算と状態遷移を curses と rclpy から切り離し、
   端末もノードもなしにテストできるようにする（[vehicle-tui.md](vehicle-tui.md) と同じ方針）。

## 界面

```
  MPC ノード                                    フロントエンド (TUI / ros2 param / rqt)
  declare_parameter(name, value, descriptor)
    ├ description   "target lateral accel"     DescribeParameters ─► 表示名・単位・レンジ
    ├ FloatingPointRange(3.0, 14.0)            GetParameters      ─► 現在値
    └ read_only     起動時のみの param          SetParameters      ─► 送信
         ▲                                                │
         └────── 値の意味と安全な範囲の正本 ──────────────────┘
```

この形にすると、手書きのレンジ検証が不要になる（rclpy が set 時に弾く）。
`ros2 param describe` がそのままドキュメントになり、TUI が無い環境でも単位が読める。

### `step` は付けない

`FloatingPointRange.step` は**使わない**（全て `0.0` = 連続）。理由は 2 つある。

- rclpy は **declare 経路でも** range / step を検証し、`declare_parameters` は
  `raise_on_failure=True` で呼ぶため、初期値が step の格子に乗っていないと
  `InvalidParameterValueException` で**ノードが起動しない**。実際
  `steering_tire_angle_gain_var = 1.639` は `step=0.01` の格子に乗らない
  （最近傍 1.64、相対誤差 6.1e-4 > `PARAM_REL_TOL = 1e-6`）
- step を付けると**直接入力で中間値を入れられなくなる**（走行中に「6.7 にしたい」ができない）

したがって界面はこう切る。

| 項目 | 正本 |
|---|---|
| レンジ（上下限）・説明・単位・read_only | **descriptor** |
| 増減の刻み | **フロントエンド** |

### レンジ

`config.yaml` のプリセット実績（低速 15 km/h 〜 究極 30 km/h）を包む幅にする。
`mpc_controller.py` のモジュール定数として持つ（設計方針 2）。

| param | range | フロントエンドの刻み |
|---|---|---|
| `v_max` | 5.0 .. 35.0 km/h | 1.0 |
| `ay_max` | 3.0 .. 14.0 m/s² | 0.5 |
| `Q0` `Q1` `Q2` | 1e4 .. 1e9 | 対数（×1.2 / ÷1.2） |
| `R0` `R1` `QN0-2` | 1e-3 .. 1e9 | 対数 |
| `width` | **`real_width`** .. 3.50 m | 0.05 |
| `steering_tire_angle_gain_var` | 0.5 .. 3.0 | 0.01 |
| `accel/steer_low_pass_gain` | 0.05 .. 1.0 | 0.05 |
| `wp_id_offset` | 0 .. 10（`IntegerRange`） | 1 |
| `ref_vel/<s>/ref_vel` | 5.0 .. 35.0 km/h | 1.0 |
| `ref_vel/<s>/wp_id` | 0 .. n_waypoints | （UI には出さない） |

`R0` / `QN` の下限を `1e-3` にしたのは、レンジ比で対数刻みを判定するフロントエンド側の
ルールが `from_value = 0` でゼロ除算するため。重み 0 は事実上の無効化で、
それは `R1: 0.0` のように config で行う。

### `width` の下限は車両の物理量から導く

`bicycle_model` に **`real_width: 1.45`** を追加し、`width` の下限をそこから取る。
`safety_margin = width / √2` なので:

| width | margin | 車体半幅 0.725 m に対する余裕 |
|---|---|---|
| 2.30（現在の設定） | 1.63 m | 0.90 m |
| **1.45（下限）** | 1.03 m | **0.30 m** |
| 1.03 | 0.73 m | 0.00 m — 車体端がコリドー境界に接する |

descriptor のレンジは**安全に設定できる範囲**であって、物理的にはみ出す点ではない。
残る 0.30 m が GNSS/RTK の測位誤差・MPC の追従誤差 `e_y`・occupancy grid の解像度誤差の
取り分になる。「徐々に攻める」の伸びしろは 2.30 → 1.45 の 0.85 m 分（margin で 0.60 m）。

下限をハードコードしないのは、車両が変われば変わる値だからである。

### 起動時のみ有効な param は `read_only` で示す

`use_boost_acceleration` / `use_obstacle_avoidance` / `use_stats` は declare されているのに
`param_cb` が名前を見ておらず、`ros2 param set` すると**成功したように見えて何も起きない**。
`read_only=True` を付ければ rclpy が拒否する。

launch の override は壊れない。rclpy が **declare 経路でだけ read_only チェックを外している**ため:

```python
result = self._apply_descriptors(parameter_list, descriptors, False)       # declare 経路
result = self._apply_descriptors(parameter_list, self._descriptors, True)  # 通常の set
```

## 走行中に `width` を変えるための前提

`use_obstacle_avoidance=false`（既定）では `MPC.__init__` が
`update_simple_path_constraints()` で事前計算したコリドーを `_init_problem` が毎周期読む。
`width` を変えるとこれが古くなるので作り直す必要がある。

### 別スレッドに逃がすだけでは足りない

param コールバックは executor スレッドで走り、制御ループは main スレッドだが
（`scripts/mpc_controller` が `MultiThreadedExecutor` を別スレッドで spin し main で
`node.run()`）、**それだけでは解決しない**。`update_simple_path_constraints()` は
純 Python の二重ループで、各要素が `np.mod` / `np.cos` のスカラー呼び出しであり、
**GIL を保持し続ける**。別スレッドで走らせても main スレッドの制御ループは待たされる。

実測（waypoint 数を振ったマイクロベンチ、N=20）:

```
n_waypoints=  400  loop     28.7 ms   vec   0.53 ms   x    55   identical=True
n_waypoints=  800  loop     55.8 ms   vec   0.44 ms   x   128   identical=True
n_waypoints= 1600  loop    114.8 ms   vec   0.90 ms   x   128   identical=True
```

40 Hz = 25 ms 周期に対し 56 ms は 2 周期分の欠落で、走行中にやることではない。

### 解決: waypoint ごとに 1 回だけ計算する

現行実装は**同じ waypoint を最大 N=20 回重複計算している**。`wp_id` と `n` の二重ループだが、
計算内容は `get_waypoint(wp_id+n)` が返す waypoint にしか依存しない。
waypoint ごとに 1 回計算し、インデックス行列で `(n_waypoints-1, N)` に展開すれば同じ配列が出る。

```python
raw = np.arange(n_waypoints-1)[:, None] + np.arange(N)[None, :]
idx = raw % n_waypoints if circular else np.minimum(raw, n_waypoints-1)  # get_waypoint と同じ意味論
upper_bounds = ub_sm[idx]
```

0.44 ms なら制御周期の 2% で、GIL を握っても実質無視できる。副産物として起動時の初期化も
同じだけ速くなる。

書き換えるのは `update_simple_path_constraints()` のみ。
`update_simple_path_constraints_horizon()` は N 個だけで軽いので触らない。
`update_path_constraints()`（障害物回避 ON のとき毎周期呼ばれる別関数）も**触らない** —
今回のデフォルトは OFF で対象外だが、ON にする際は同じ検討が要る。

### 事前計算済みコリドーが破壊される既存バグ

```python
ub = self.model.reference_path.path_constraints[0][ref_wp_id]   # 2D ndarray の view
...
ub -= safety_margin_diff                                        # 元配列を in-place で破壊
```

`path_constraints[0]` は 2D `ndarray` なので行インデックスは view であり、`-=` が
事前計算済みコリドーを恒久的に書き換える。`get_control()` の緩和リトライで発火し、
その waypoint のコリドーが走行のたびに狭まっていく。`.copy()` を取る。

`width` を往復させて元の配列に戻ることを確認するには、この修正が前提になる。

### 差し替えの粒度

コールバックスレッドで新しい配列を作りきってから参照を差し替える。制御ループが見るのは
古い配列か新しい配列のどちらかで、作りかけを見ることはない。
`update_simple_path_constraints()` は `set_path_constraints()` と `set_border_cells()` を
続けて呼ぶため 2 つの代入の間に 1 周期だけ不整合が入りうるが、border_cells は描画用途なので
実害はない。

## 現在値の保存

`ref_vel` 側は既存の `ref_vel/save` をそのまま使う（バックアップ + `yaml.dump` で上書き）。

MPC 側は `save_config` bool param を新設し、`config.tuned.<YYYYmmdd_HHMMSS>.yaml` を
書き出す。**元の `config.yaml` は上書きしない** — あちらは速度域別プリセットを大量の
コメントで持っており、`yaml.dump` で書き戻すと全部消える。走行後に diff を見て人間が取り込む。

保存はフロントエンドではなくノード側の仕事にする。フロントエンドが書くと share ディレクトリの
パス解決が漏れる。

保存対象は明示的に列挙する。「declare した param を全部」にすると read_only な launch 由来の
param が混ざり、`config.yaml` に無いキーが生える。

```yaml
mpc:
  v_max: ...      # km/h
  ay_max: ...
  Q:  [Q0, Q1, Q2]
  R:  [R0, R1]
  QN: [QN0, QN1, QN2]
  steering_tire_angle_gain_var: ...
  accel_low_pass_gain: ...
  steer_low_pass_gain: ...
  wp_id_offset: ...
bicycle_model:
  width: ...
```

## TUI

[monospace-design-tui](https://github.com/coreyt/monospace-design-tui) の規範と
[clig.dev](https://clig.dev/) に従う。monospace でいう **Configuration アーキタイプ**
（カテゴリ + 設定フォーム、カテゴリ間を自由に移動）。

### 画面

```
 mpc tuner  /mpc_controller              v 18.4   cmd 20.0 km/h   steer -4.2 deg
 [ speed ]   line   ref_vel
────────────────────────────────────────────────────────────────────────────────
 > v_max                     20.0  km/h    5.0 ────|─────────  35.0   +-1.0
   ay_max                     7.0  m/s^2   3.0 ───|──────────  14.0   +-0.5  *
────────────────────────────────────────────────────────────────────────────────
 set ay_max 6.5 -> 7.0  ok
 ?Help  <>Adjust  PgUp/PgDn x10  Enter Edit  Tab Pane  ^Z Undo  r Reload  s Save  q Quit
```

- **フッターのキーストリップは常時表示**（規範: MUST）
- ヘッダに実速度と指令値。値をいじった結果がその場で見えることが主目的
- ペインはタブバー 1 行。カテゴリが 3 つなので左サイドバー（8-16 桁）より桁が浮く
- focus は reverse video。**常に 1 つが focus**（focus invariant）
- 起動時の値から動いた項目に `*`。**色を唯一の意味にしない**（規範: MUST NOT）
- 最小端末 80x24、標準 120x40。下回れば警告して終了

**`ref_vel` ペインには `ref_vel` だけを 10 行出し、`wp_id` は出さない。**
`wp_id` はコースの区間定義であって、走行中に動かすものではない。descriptor は付けるが
UI には載せない。これでスクロールが不要になる。

### キー割り当て（Tier 1 準拠）

| キー | 動作 |
|---|---|
| `?` / `F1` | ヘルプ |
| `↑` `↓` | 項目選択 |
| `←` `→` | 1 step 増減して即送信 |
| `PgUp` `PgDn` | 10 step 分 |
| `Enter` | 直接入力。**入力中は単一文字キーを抑止**（`q` が終了せず文字として入る）。`Esc` で取消 |
| `Tab` `[` `]` | ペイン切替（`[` `]` は Configuration アーキタイプの規約） |
| `Ctrl+Z` | 直前の 1 変更を取り消す |
| `Ctrl+R` | 起動時スナップショットに全戻し |
| `r` / `F5` | ノードから再読込 |
| `s` | 保存（`save_config` と `ref_vel/save` の**両方**。片方が失敗しても他方は実行し、結果を並べる） |
| `q` / `Esc` | 終了 |

単一文字キーは case-insensitive（規範: MUST）。

### undo

clig.dev は状態変更を 3 段階に分け、中程度以上には dry run か誤爆しにくさを求めるが、
走行中の即時反映に dry run は原理的に無い。代わりに**戻せること**で担保する。
実車では「今の 1 手で挙動が悪化した、すぐ戻したい」が必ず起き、元の値を思い出しながら
逆キーを数えるのは危険である。

- **拒否された変更はスタックに積まない。** 状態を変えていない set を積むと `Ctrl+Z` が
  「起きていない変更」を戻して値がずれる
- **`Ctrl+R` は `SetParametersAtomically` を使う。** `SetParameters` に複数 param を渡しても
  アトミックにならない（rclpy の docstring が「once for each parameter」と明記）。
  全戻しの途中の状態で車が走ることになる
- **`v_max` の undo は実効上限を戻すとは限らない。** 実効上限は `min(ref_vel, v_max)` なので、
  ref_vel 側が低ければ挙動は変わらない。この非対称は status 行に出す

### 対数刻み

`Q0` / `Q2` は 1e4 .. 1e9 のレンジで、線形の刻みでは扱えない。フロントエンドが汎用ルールで
判定する: **`from_value > 0` かつ `to_value / from_value >= 100` なら対数刻み
（×1.2 / ÷1.2）、それ以外は線形。** param 名で分岐しない。

`from_value > 0` のガードが要るのは、下限 0 のレンジでゼロ除算するため。

### ノードとの通信

**humble には `rclpy.parameter_client.AsyncParameterClient` が無い**（iron 以降の API）。
`ros2param.api` の `call_*` ヘルパーも使わない — 毎回クライアントを作り直し、
`wait_for_service(timeout_sec=5.0)` と `spin_until_future_complete` でブロックするため、
矢印キー 1 回ごとに呼ぶとクライアントがリークし描画が止まる。

`rcl_interfaces` の 5 サービスのクライアントを**起動時に 1 回だけ作る**。

| サービス | 用途 |
|---|---|
| `ListParameters` | 起動時に実在する param を列挙（`ref_vel/<section>` もここから拾う） |
| `DescribeParameters` | 表示名・単位・レンジ・read_only |
| `GetParameters` | 現在値。`r` での再読込 |
| `SetParameters` | 1 項目の増減 |
| `SetParametersAtomically` | `Ctrl+R` の全戻し |

**curses と rclpy は単一スレッドで同居させる。** `stdscr.timeout(125)`（8 Hz）で
`getch` に待ち時間を持たせ、同じループから `rclpy.spin_once(node, timeout_sec=0)` と
future の完了チェックを行う。

[vehicle-tui.md](vehicle-tui.md) がスレッドを使っているのは subprocess の stdout が
ブロッキング read で避けられなかったからで、rclpy は非ブロッキング API を持つため
スレッドを持ち込む理由がない。状態が 1 スレッドに閉じてロックが不要になり、
`getch` と `spin_once` をモックすればループ 1 本としてテストできる。

### telemetry

`/localization/kinematic_state`（実速度）と `/control/command/control_cmd`
（指令速度・舵角・加速度）を購読する。

**QoS は購読前に `get_publishers_info_by_topic()` で publisher 側を見て合わせる。**
MPC 本体は `/planning/scenario_planning/trajectory` で BEST_EFFORT のミスマッチを踏み、
明示的に合わせたコメントを残している。同じ罠がある。合わせられなければ「no telemetry」と
出して縮退する（本体機能は param 操作なので、telemetry が無くても使えなければならない）。

現在の wp_id と走行中セクションは**出さない**。MPC が publish しておらず、
odom から逆算すると MPC のロジックを再実装することになる。
将来 MPC 側に薄い診断トピックを足す案として残す。

## 実装

| ファイル | 役割 |
|----------|------|
| `multi_purpose_mpc_ros/tools/mpc_tuner_core.py` | 表示グループの定義（順序と見出しのみ）、増減計算（線形/対数の判定・クランプ・丸め）、undo スタック、カーソルとペインの状態遷移。rclpy と curses に依存しない |
| `multi_purpose_mpc_ros/tools/mpc_tuner_tui.py` | サービスクライアント、telemetry 購読、curses の描画とキー処理 |
| `scripts/mpc_tuner` | エントリポイント。**venv は使わない**（必要なのは rclpy と curses だけで、どちらも system python3 にある。既存の `run_*.bash` が activate する venv は MPC 本体の numpy/osqp 用） |
| `test/test_mpc_tuner_core.py` | core の単体テスト |
| `test/test_mpc_tuner_tui.py` | 表示ヘルパとレイアウト計算の単体テスト |

**core が持たないもの**: レンジ・単位・説明。すべて実行時に descriptor から入る。
core は「descriptor と現在値を受け取り、増減後の値を返す」純関数群になる。

対象ノードは `--node`（既定 `/mpc_controller`）で変えられるようにする。

### 起動

`Makefile` の `tui` で起動する。autoware コンテナ内で動かすのは、rclpy・`ROS_DOMAIN_ID`・
`CYCLONEDDS_URI`（`lo` 限定）をスタックと確実に揃えるため。
`vehicle-tui` と同じく tmux で包み、ssh が切れても作業が残るようにする。

```makefile
tui:
	tmux new -A -s aic-mpc-tui \
	  "CMD='ros2 run multi_purpose_mpc_ros mpc_tuner' docker compose run --rm --no-deps autoware-command"
```

複数ドメインで動かしているときは `make tui ROS_DOMAIN_ID=2`。

`tui` は [makefile-target-naming.md](makefile-target-naming.md) の `<service>-<command>` 形式から
外れる。打鍵頻度を優先した意図的な例外である。

## エラーハンドリング

- **レンジ外**: rclpy が `SetParametersResult(successful=False)` を返す。
  `reason` は**フロントエンドで解釈せずそのまま出す**（解釈するとノード側の検証ロジックが漏れる）
- **ノード不在**: 「waiting for /mpc_controller」を出して待つ。例外にしない。
  MPC を再起動しても TUI は生き残り、再検出して値を読み直す
- **`width` の再生成中**: 100 ms を超えたらスピナーを出す（規範: >100ms の操作に
  視覚的フィードバックは MUST）
- **`Ctrl+C`**: 即座に応答して抜ける

## テスト方針

`pytest test/` で走る。

| 観点 |
|------|
| `update_simple_path_constraints()` のベクトル化が旧実装と数値的に等価（`np.allclose`）。circular / 非 circular、`n_waypoints < N` の境界を含む |
| 再生成が 40 Hz の 1 周期（25 ms）に収まること |
| `width` を往復させたとき `path_constraints` が元の配列と一致すること（コリドー破壊の検出） |
| 線形/対数の判定境界と `from_value <= 0` のガード |
| クランプと丸め |
| undo スタックの往復、拒否された set を積まないこと |
| カーソル移動の境界、ペイン切替でのカーソル保持 |
| descriptor が無い param のフォールバック |

ベクトル化の等価性テストは、`x` / `y` / `psi` / `ub` / `lb` だけを持つ軽量な fake waypoint を
並べ、旧実装のループを参照実装としてテスト側に写して比較する
（`test/test_v2x_vehicle_tracker.py` が ROS メッセージのスタンドインを作っているのと同じ手法）。

curses の描画そのものと実車での疎通は手動確認とする。

## スコープ外

- `a_min` / `a_max` の動的化 — `ref_vel_configulator` が毎周期 `set_v_ref()` で全 waypoint を
  上書きするため、`compute_speed_profile()` の出力は起動直後に捨てられている。
  動的変更は acc クリップにしか効かず、今回の目的（速度上限とライン取り）には要らない
- `delta_max_deg` / `steer_rate_max` / `use_max_kappa_pred` の動的化 — 同上。必要になれば
  `_init_problem()` が毎周期読み直す構造なので setter を足すだけで足りる
- `N`（ホライズン）と `control_rate` — 再初期化に近く、走行中の変更に向かない
- 障害物回避 ON のときの `update_path_constraints()` の高速化
- MPC の診断トピック（現在の wp_id / セクション）

## 実機で確認した事実

ドキュメントではなく `/opt/ros/humble` の実物と実測から取った。設計の前提なので、
humble 以外へ移すときは取り直すこと。

| 事実 | 確認方法 | どこに効くか |
|---|---|---|
| `rclpy.parameter_client` が存在しない | `import` して `ModuleNotFoundError` | 生サービスクライアントにした |
| `ros2param.api.call_*` は毎回クライアントを作り `spin_until_future_complete` でブロックする | `inspect.getsource` | 同上。TUI から呼ばない |
| rclpy は **declare 経路でも** range / step を検証し、`declare_parameters` は `raise_on_failure=True` | `rclpy/node.py:1054-1069` と declare 実装 | `step` を全て 0.0 にした |
| `PARAM_REL_TOL = 1e-6`。`steering_tire_angle_gain_var = 1.639` は `step=0.01` の格子に乗らない | `rclpy/node.py:110` + 再現計算 | 同上（付けるとノードが起動しない） |
| declare 経路だけ read_only チェックを外す | `rclpy/node.py` の `_set_parameters_atomically` | read_only を使ってよい根拠 |
| `SetParameters` は param ごとに逐次適用（「once for each parameter」） | `set_parameters` の docstring | `Ctrl+R` を `SetParametersAtomically` にした |
| コリドー再生成は 800 waypoint で 55.8 ms、ベクトル化で 0.44 ms（出力一致） | マイクロベンチ | 走行中の `width` 変更が成立する根拠 |

## 参照した資料

- [monospace-design-tui](https://github.com/coreyt/monospace-design-tui) —
  TUI の設計標準（MUST / MUST NOT）。Configuration アーキタイプ、フッターキーストリップ、
  Tier 1 キー、色を唯一の意味にしない、focus invariant、>100ms の視覚フィードバック、
  即時コミット操作の undo をここから採った。規範本文はリポジトリ内の
  `monospace-tui-design-standard.md` にあり、ランディングページには載っていない
- [Command Line Interface Guidelines (clig.dev)](https://clig.dev/) —
  状態を変えたら何が起きたか説明する、危険度に応じた誤爆しにくさ、100ms 以内の応答、
  `Ctrl-C` の即応答
- [The Complete Guide to Terminal User Interfaces](https://gist.github.com/MangaD/cd8b8ab9b4f119ac5214fa4f3424ccd7) —
  ちらつき対策、Unicode 文字幅、端末機能の差
- [rcl_interfaces/msg/ParameterDescriptor](https://docs.ros2.org/foxy/api/rcl_interfaces/msg/ParameterDescriptor.html) —
  界面の定義
- [rqt_reconfigure issue #116](https://github.com/ros-visualization/rqt_reconfigure/issues/116) —
  humble の rqt_reconfigure は `FloatingPointRange` で
  `setSingleStep(self, int): argument 1 has unexpected type 'float'` を出す既知の不具合がある。
  **descriptor 化の動機には数えない**（動機は `ros2 param describe` と rclpy の自動検証）
- [Hector UI](https://arxiv.org/pdf/2504.19728) —
  センサ値を safe / warning / dangerous の 3 段階で提示する定石。起動時の値から大きく
  離れたときの警告表示はここから
