# 参加者の承認による map・IMU バイアス更新

提出物の設定は、参加者の承認を確認した場合だけ更新します。
map は提出物の展開時、IMU バイアスは Autoware 起動後の runtime で保存値の適用を確認します。
拒否・空回答・入力終了では参加者の設定を保持します。

## 提出物の展開と map の確認

```bash
make submission-extract SUBMISSION_ID=<id>
```

参加者 TUI の `extract` も同じ処理です。ZIP を一時ディレクトリへ展開した後に確認します。

```text
提出物の accel/brake map を AWSIM adapter の共通mapで上書きしますか？
参加者の承認を確認してください。 [y/N]:
```

明示的に `y` または `yes` と回答した場合だけ、以下のファイルをコピーします。
大文字の回答も受け付けます。拒否した場合も、提出物の map を保持して展開を完了します。

| コピー元 | 提出物内の適用先 |
| --- | --- |
| `aichallenge_awsim_adapter/data/accel_map.csv` | `aichallenge_submit_launch/data/accel_map.csv` |
| `aichallenge_awsim_adapter/data/brake_map.csv` | `aichallenge_submit_launch/data/brake_map.csv` |

コピー元の実際のディレクトリは
`aichallenge/workspace/src/aichallenge_system/aichallenge_awsim_adapter/data/` です。
全車両で共通のファイルを使い、車両別ディレクトリに map を複製しません。
`steer_map.csv` は変更しません。

提出物側に対象ファイルがない場合は、警告してそのファイルだけスキップします。
両方ない場合は map の確認を省略します。独自の構成でも提出物を展開できますが、
[参加者インターフェース契約](../docs/interface/participant-interface.md) は引き続き必要です。

承認後、コピー元の `default` ヘッダ、長方形の数値テーブル、有限値、速度・ペダル軸の
昇順を検証します。両方の検証を終えてから一時ファイル経由で置換し、元の権限を保持します。
読み取り専用の map も更新できます。検証・コピーに失敗した場合は既存の提出コードを残します。
拒否した場合はコピー元の map や車両別の校正値がなくても展開できます。

入れ替えの最後は既存処理と同じ削除→rename であり、その間のプロセス中断からの
復旧は保証しません。ビルド成果物の削除は引き続き `workspace-clean` の責務です。
展開時に IMU 設定は変更せず、車両別保存元のバイアスも自動適用しません。
展開には `VEHICLE_ID` や車両別の `imu_bias.yaml` は不要です。

## runtime の保存済み IMU バイアス適用

```bash
vehicle/setup_check.sh --phase runtime
```

当日のバイアス計測は行いません。`vehicle/.calibration/<VEHICLE_ID>/imu_bias.yaml` を読み、
提出コードの現在値・保存値・差分（保存値 − 現在値）を表示して参加者の承認を確認します。
ID は環境変数 → リポジトリ直下の `.env` → 既存のホスト名対応の順で取得します。

```text
IMU 角速度バイアス [rad/s] / VEHICLE_ID=A2
軸    現在値        保存値        差分(保存値−現在値)
x  +0.000000  +0.001000  +0.001000
y  +0.000000  -0.002000  -0.002000
z  +0.001000  +0.003000  +0.002000
提出物の IMU 角速度バイアスを車両 A2 の保存値で上書きしますか？
参加者の承認を確認してください。 [y/N]:
```

承認した場合だけ `imu_corrector/config/imu_corrector.param.yaml` の
`angular_velocity_offset_x/y/z` を保存値で置換し、ノイズ設定・コメント・ファイル権限を保持します。
保存元の `imu_bias.yaml` は変更しません。拒否・空回答・入力終了は警告として更新を見送ります。
対象ファイルや対応する 3 軸オフセットがない独自構成も警告してスキップします。

ID 未設定、保存ファイルなし、不正な保存値の場合は警告を表示します。
そのまま更新を拒否して提出物の値を保持できますが、承認した場合は失敗として報告します。
別車両やゼロへのフォールバックは行いません。承認待ちの間に提出設定が変更された場合も更新を拒否します。

この処理はホストの Python 標準ライブラリだけで実行します。
単独実行は `python3 vehicle/apply_imu_bias.py`（成功 `0`、更新見送り `5`、失敗 `3`）です。
raw IMU の受信は runtime の `/sensing/imu/imu_raw` topic チェックで別途確認します。

**この段階では更新タイミングは runtime のままです。反映には Autoware の再起動が必要です。**

## 車両別 IMU バイアスの保存元

保存元は Git 管理対象の `vehicle/.calibration/<VEHICLE_ID>/imu_bias.yaml` で、
`make workspace-clean` の削除範囲外です。実測値の記録は [PR #346](https://github.com/AutomotiveAIChallenge/aichallenge-racingkart/pull/346) です。
同 PR の反映前の初期値 0 を実測値として使わず、運用前に保存元の実測値が配備されていることを確認してください。

`imu_bias.yaml` は有限値の 3 キーだけを持つフラットな YAML です。

```yaml
angular_velocity_offset_x: 0.001
angular_velocity_offset_y: -0.002
angular_velocity_offset_z: 0.003
```

整備時に再計測するための `check_imu_bias.py` は単体ツールとして残します。
通常の runtime からは呼びません。実行時は操作者が完全な静止を確認し、ROS 環境を用意してください。
`--proposal-output` は測定結果の保存、`--apply-proposal` は承認済み結果の適用に使えます。
`--bias-output <path>` を指定した場合だけ、承認後に保存元にも記録します。
