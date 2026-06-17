# recovery_supervisor

通常制御の最終段に入る復帰用 control mux。
`/control/command/nominal_control_cmd` を受け、通常時は `/control/command/control_cmd` に pass-through する。
前進指令中に低速状態が続いたら、REVERSE で直進バックして DRIVE に戻す。

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

## Run

通常のMPCにsupervisorを挟む:

```bash
RUN_MODE=awsim-mpc-recovery docker compose up -d autoware
```

別YAMLを使う:

```bash
RECOVERY_SUPERVISOR_PARAM_FILE=/path/to/recovery_supervisor.param.yaml \
RUN_MODE=awsim-mpc-recovery docker compose up -d autoware
```

検証コマンドで一部だけ上書きする:

```bash
make recovery-supervisor-repro \
  RECOVERY_SUPERVISOR_REVERSE_DURATION=3.0 \
  RECOVERY_SUPERVISOR_STUCK_DURATION=1.5
```

検証時は `recovery_supervisor.param.yaml` と `recovery_supervisor_params.env` が出力ディレクトリに保存される。

## Manual smoke

AWSIMを手動操作して壁に当て、自動制御へ戻して復帰を見る場合:

```bash
make down
make recovery-mpc-dev
```

別ターミナルでrosbag記録:

```bash
make recovery-manual-record LOG_DIR=/output/$(date +%Y%m%d-%H%M%S)-manual-recovery
```

別ターミナルで状態監視:

```bash
make recovery-watch
```

`recovery-watch` は `/recovery_supervisor/state` の変化だけを表示する。

手動操作が終わったら `recovery-manual-record` を Ctrl-C で止める。
記録済みbagは既存の analyzer で確認できる。

```bash
make recovery-check \
  BAG=/output/<run-id>-manual-recovery/recovery/rosbag2_recovery \
  EXPECT=recovered
```
