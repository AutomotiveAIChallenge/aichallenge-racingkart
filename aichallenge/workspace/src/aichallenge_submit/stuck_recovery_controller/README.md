# stuck_recovery_controller

`/control/command/nominal_control_cmd` と`/vehicle/status/velocity_status` をsubscribeし、スタックを検知したら直進で後退する機能。
スタックを検知していない時は、`/control/command/nominal_control_cmd`を`/control/command/control_cmd`にそのままpublishする。
