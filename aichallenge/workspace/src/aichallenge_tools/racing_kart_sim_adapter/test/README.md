# test_arbiter_core テスト設計書

`arbiter_core`(joy裁定の状態機械)に対するテスト設計。設計・実装の経緯は[../README.md](../README.md)を参照。

## 目的・スコープ

`racing_kart_driver_node.cpp`の`on_joy()`/`on_timer()`と挙動が一致することを、ROS/AWSIMを起動せずに検証する。

**対象外**(2026-08-13、AWSIM結合テストは重いため明示的に除外する方針):

- AWSIM/Autowareを実際に起動しての結合テスト(`/control/command/control_cmd`のremap配線が正しく効いているか、車両が実際に動くか)
- zenoh経由・namespace分離・複数車両同時実行のテスト
- design doc([multi-vehicle-start-stop.md](../../../../../../multi-vehicle-start-stop.md))のREQ-01〜05シナリオテスト(いずれもAWSIM前提)

これらは`arbiter_core`の実装が固まり、`adapter_node`(ROSラッパー)ができた後の課題として残す。

## テスト観点

| 観点 | 何を保証したいか |
|---|---|
| 初期状態 | joyが一度も来ていない起動直後は安全側(emergency・停止)であること |
| 入力サイズ異常 | 想定外サイズのjoyを受けてもクラッシュせず停止出力になること |
| モード遷移 | A/X/Yの優先順位・持続性(ワンショットでなく直前値を保持)が実車と一致すること |
| 緊急ラッチ/解除とその順序依存 | 4つの緊急ボタンでラッチされること、`LSB+RSB`解除がモード確定より後に効くという実車の順序を再現できていること |
| 軸変換 | 中立値(`+1.0`)・全開(`-1.0`)・クランプ範囲(手動`±0.9`/自動`±0.8`)が実車の式と一致すること |
| ギア裁定 | 手動中はDpad、自動中は`gear_cmd`から選ばれ、無入力時は直前値を保持すること |
| モード別ソース切替の非対称性 | `AUTONOMOUS_STEER_ONLY`はステアだけAutoware側でアクセル/ブレーキは人間側、という実車の非対称な条件式を再現できていること |
| 鮮度タイムアウト | 閾値ちょうどはセーフ、超過で強制停止という境界値が実車の厳密不等号と一致すること |
| 未受信ガード | 実車の潜在バグ(`actuation_cmd_`未着でのUB)を再現せず、安全側(0出力)にフォールバックすること |

## ケース一覧

| # | テストケース | 観点 | 検証内容 |
|---|---|---|---|
| 1 | `InitialState_IsEmergencyAndStopped` | 初期状態 | 起動直後は`is_emergency=true`・`joystick_available=false`・`mode=MANUAL`・停止出力(`brake=1`) |
| 2 | `UndersizedJoy_StaysUnavailableAndStopped` | 入力サイズ異常 | `buttons.size()!=11`でも`joystick_available=false`で停止出力、クラッシュしない |
| 3 | `ValidNeutralJoy_StaysEmergencyUntilChordPressed` | 緊急ラッチ初期値 | サイズ正常な中立joyでも、解除操作なしでは`is_emergency`は既定のtrueのまま |
| 4 | `EmergencyChord_ClearsAndDrivesOnTheSameTick` | 緊急解除の順序 | `LSB+RSB`を押した**同じtick**で`is_emergency=false`になり、その場でjoystick値がそのまま出力される |
| 5 | `EmergencyChord_WithSimultaneousEmergencyButton_StillDrives` | 緊急解除の順序(境界) | 同一tickで緊急ボタンと`LSB+RSB`が同時に立っても、解除チェックが無条件に効いて走行状態になる |
| 6 | `ModeButtons_SwitchMode` | モード遷移 | A/X/Yそれぞれの押下でMANUAL/AUTONOMOUS_STEER_ONLY/AUTONOMOUSに遷移 |
| 7 | `NoModeButton_KeepsPreviousMode` | モード遷移の持続性 | モードボタン未押下のtickでは直前のモードを保持(ワンショットでない) |
| 8 | `EachEmergencyButton_LatchesAndForcesManual` | 緊急ラッチ | LB/RB/Start/Backそれぞれが独立してラッチし、そのtickのモードをMANUALへ強制、ラッチは次tickにも持続(4ボタンをループ検証) |
| 9 | `AccelAxis_NeutralIsZero_FullDeflectionIsOne` | 軸変換(アクセル) | 中立(`+1.0`)で比率0、全開(`-1.0`)で比率1 |
| 10 | `BrakeAxis_NeutralIsZero_FullDeflectionIsOne` | 軸変換(ブレーキ) | 同上をブレーキ軸で |
| 11 | `SteerAxis_ManualClampsToPoint9` | 軸変換(ステア・手動) | `±1.0`の入力が`±0.9`にクランプ |
| 12 | `GearDpad_ManualModeSelectsGear` | ギア裁定(手動) | 横+1→N、縦+1→D、縦-1→R、無入力時は直前値を保持 |
| 13 | `AutonomousMode_UsesAutowareAccelBrakeAndSteer_IgnoresJoystick` | モード別ソース切替 | AUTONOMOUS中はaccel/brake/steerすべてAutoware側の値を使い、joystick軸(全開・全ステア)は無視される |
| 14 | `AutonomousSteerOnly_SteerFromAutoware_AccelBrakeStillFromJoystick` | 非対称性 | STEER_ONLY中はステアだけAutoware側、アクセル/ブレーキは人間側のまま |
| 15 | `AutonomousSteerClampsToPoint8` | 軸変換(ステア・自動) | Autoware側`steer_ratio`が範囲外でも`±0.8`にクランプ(手動`±0.9`とは異なる範囲) |
| 16 | `AutonomousGear_FromAutowareGearCommand` | ギア裁定(自動) | `gear_command`(NEUTRAL/DRIVE/REVERSE)をそのまま反映、未知値はDRIVEにフォールバック |
| 17 | `AutonomousUnavailable_SafeZeroInsteadOfUndefinedBehavior` | 未受信ガード | `available=false`ならaccel/brakeともに0(残留値が混入しない) |
| 18 | `JoyTimeout_JustUnderThreshold_StaysAlive` | 鮮度タイムアウト境界 | 経過5.0秒ちょうど(閾値と同値)はまだemergencyにならない(厳密不等号) |
| 19 | `JoyTimeout_PastThreshold_ForcesEmergencyAndStop` | 鮮度タイムアウト境界 | 5.0001秒経過で`emergency`化・`joystick_available=false`・停止出力 |

joyコントローラの機能割り当てとの対応(どの入力がどのケースでカバーされているか)は[../README.md](../README.md)の表を参照。

## 実行方法

```bash
cd ~/aichallenge-racingkart
CMD='source /opt/ros/humble/setup.bash && source /autoware/install/setup.bash && cd /aichallenge/workspace && \
  colcon build --symlink-install --packages-select racing_kart_sim_adapter --cmake-args -DCMAKE_BUILD_TYPE=Release && \
  colcon test --packages-select racing_kart_sim_adapter --event-handlers console_direct+ && \
  colcon test-result --verbose'
docker compose run --rm --no-deps autoware-command bash -lc "$CMD"
```

AWSIM/Autowareを起動していなくても実行できる(使い捨ての`autoware-command`コンテナのみで完結)。

## 現状

2026-08-13時点、`arbiter_core.cpp`本実装済み。全19ケース green(`colcon test` で確認)。
