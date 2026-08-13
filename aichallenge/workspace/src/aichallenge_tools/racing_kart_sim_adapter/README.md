# racing_kart_sim_adapter

`racing_kart_manager`([../../../../multi-vehicle-start-stop.md](../../../../../multi-vehicle-start-stop.md))を、実車無しでAWSIM(`make dev4-remote`)相手にテストするためのアダプター。

## 背景

- `make dev4`の各Autowareは`/control/command/control_cmd`+`/control/command/gear_cmd`をAWSIMへ直接publishしていて、実車の`racing_kart_driver`(joyを受けてMANUAL/AUTONOMOUSを裁定するノード)はsimの経路に一切存在しない
- `/vehicle/status/{control_mode,velocity_status,gear_status,steering_status}`は`awsim_d<N>`ノードが実機確認済みで自前publishしている(2026-08-13、`make dev`実行して`ros2 topic info -v`で確認)。VCUステータス→Autoware標準レポートへの変換は**AWSIMが肩代わり済み**
- 足りないのは「joyを受けて`racing_kart_driver`と同じ裁定をし、その結果を`/control/command/control_cmd`+`gear_cmd`として出す」入力側だけ

## ソースオブトゥルース

このパッケージは`racing_kart_interface`(別リポジトリ)の
`src/racing_kart_driver/src/racing_kart_driver_node.cpp`の`on_joy()`/`on_timer()`をロジック面で複製する。挙動の食い違いに気づいたら、まずこのファイルを正として直す。

## joyコントローラの機能割り当て(2026-08-13、実際の参照箇所を突き合わせ済み)

`racing_kart_driver_node.cpp`が実際に読んでいるボタン/軸は以下のみ。それ以外(`ButtonB`, index 8, `LeftStickVertical`, `RightStickHorizontal`, `RightStickVertical`)は上流も読んでおらず、意図的に未対応。

| 入力 | 機能 | 参照箇所(racing_kart_driver_node.cpp) |
|---|---|---|
| `ButtonA` | MANUALへ | on_timer():243-244 |
| `ButtonX` | AUTONOMOUS_STEER_ONLYへ | on_timer():245-246 |
| `ButtonY` | AUTONOMOUSへ | on_timer():247-248 |
| `ButtonLB`/`ButtonRB`/`ButtonStart`/`ButtonBack` | 緊急停止ラッチ | on_timer():227-238 |
| `ButtonLeftStick`+`ButtonRightStick`同時 | 緊急解除 | on_timer():277-281 |
| `Accel`軸(RightTrigger) | アクセル比率、中立`+1.0` | publish_vcu_command():345 |
| `Brake`軸(LeftTrigger) | ブレーキ比率、中立`+1.0` | publish_brake_command():367 |
| `Steer`軸(LeftStickHorizontal) | ステア比率、手動`±0.9`clamp | publish_steer_command():387 |
| `DpadHorizontal`/`DpadVertical` | MANUAL/STEER_ONLY中のギア選択 | on_timer():263-270 |

## 実車ドライバとの意図的な差分

| 項目 | 実車ドライバ | このアダプター |
|---|---|---|
| 出力単位 | VCU固有(throttleステップ数、brake permil、steer位置) | 正規化比率(accel/brake∈[0,1]、steer∈[-1,1])。AckermannControlCommandへの物理単位変換はノード側(未実装)の別レイヤーに切り出し。sim車両のスケール定数が未確定なため |
| VCUステータス→レポート変換 | 自分でVelocityReport等をpublish | やらない。AWSIMが自前でpublish済み(上記背景の通り) |
| ステア初期位置キャリブレーション(`is_initialized_`) | あり(実ハードのエンコーダ基準出し) | 実装しない。実センサが無いsimには意味がない |
| AUTONOMOUS中に`actuation_cmd_`が一度も届いていない場合 | `std::optional`を無条件参照(未定義動作の潜在バグ) | `AutowareCommand.available==false`なら安全側(0出力)にフォールバック。意図的な逸脱 |

## 見つけた挙動の細部(テストで固定済み)

- モード決定→(ControlModeReport相当を確定)→`LSB+RSB`解除チェック、という順序(on_timer():241-281)。つまり**緊急解除した同じtickでもう走行できる**(モードは解除チェックの前に確定するが、出力のstop/drive判定は解除チェックの後)
- `AUTONOMOUS_STEER_ONLY`はステアだけAutoware側、アクセル/ブレーキは人間側のまま(publish_vcu_command():347-351とpublish_brake_command():368の条件が非対称)
- 鮮度判定は`joy_delay_threshold_ < joy_delay`の**厳密不等号**(on_timer():196)。ちょうど閾値ぴったりはセーフ
- タイムアウト時は`input_`を空のJoyにリセットする(on_timer():198)ため、次tickは`is_joystick_available()`にも引っかかる

## 構成

```
include/racing_kart_sim_adapter/
  keybind.hpp      racing_kart_interfaceのjoystick.hpp/mapping.hppの複製(別リポジトリなのでビルド依存にできない)
  arbiter_core.hpp  ROS非依存の状態機械。JoyFrame+AutowareCommand -> ArbiterOutput
src/arbiter_core.cpp
test/test_arbiter_core.cpp  gtest。ロジックのみ検証、AWSIM結合テストは意図的にやらない(重いため)
```

`adapter_node`(rclcppラッパー、`/racing_kart/joy`購読・`/control/command/control_cmd`+`gear_cmd`発行・既存control_methodの出力remap)は未実装。

## 現状(2026-08-13時点)

- [x] `arbiter_core`のインターフェース確定、gtest 19ケース作成
- [x] ビルド・実行確認(スタブ実装に対して赤信号を確認)
- [x] `arbiter_core.cpp`の本実装(19 tests, 0 failures)
- [ ] `adapter_node`(ROSラッパー、remap配線)
- [ ] 比率→AckermannControlCommand物理単位への変換定数
- [ ] namespace付きzenoh設定・`dev4-remote`Makefileターゲット
