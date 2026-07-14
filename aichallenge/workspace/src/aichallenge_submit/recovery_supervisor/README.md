# recovery_supervisor

AWSIMの通常制御の最終段に入る復帰用 control mux（C++ / rclcpp）。
`/control/command/nominal_control_cmd` を受け、通常時は `/control/command/control_cmd` へ
そのまま（フルレートで）pass-through する。前進指令中に低速状態が続いたら、REVERSE で直進バック
して DRIVE に戻す。

`nominal_control_cmd` が来ていない間（別のコントローラが動作中など）は何も publish せず、
最終指令トピックには一切触れない。

## Launch

`reference.launch.xml` の最終段で常時起動される。MPC は出力を
`/control/command/nominal_control_cmd` に流し、supervisor がそれを最終 `control_cmd` へ
mux する。通常の起動フロー（`make dev` など）でそのまま有効になる。

## Parameters

デフォルト値は `config/recovery_supervisor.param.yaml` に置く。

主な調整対象:

- `stuck_speed_threshold`
- `stuck_duration`
- `command_speed_threshold`
- `command_accel_threshold`
- `moving_speed_threshold`
- `reverse_speed`
- `reverse_accel`
- `reverse_duration`
- `drive_settle_duration`
- `cooldown_duration`
- `nominal_timeout_sec`
- `velocity_timeout_sec`
- `timer_hz`
