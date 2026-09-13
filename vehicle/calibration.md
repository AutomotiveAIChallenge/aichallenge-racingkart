# 参加者の承認による map・IMU バイアス更新

提出物の設定は、参加者の承認を確認した場合だけ更新します。
map は提出物の展開時、IMU バイアスは Autoware 起動後の runtime 静止計測時に確認します。
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

## runtime の IMU 計測と更新確認

```bash
vehicle/setup_check.sh --phase runtime
```

車両が完全に静止していることを確認してから IMU の角速度を計測します。
移動・サンプル不足では計測失敗とし、ノイズ超過では従来どおり再計測するか確認します。
これらの場合、更新の承認確認は出さず、提出設定と保存元を保持します。

正常な計測後、提出コードの現在値・静止時の実測値・差分（実測値 − 現在値）を表示します。
乖離の閾値による自動更新はありません。

```text
IMUジャイロバイアス [rad/s]
軸    現在値      実測値      差分(実測値−現在値)
x    +0.000000   +0.001000   +0.001000
y    +0.000000   -0.002000   -0.002000
z    +0.001000   +0.003000   +0.002000

実測値で上書きしますか？ 参加者の承認を確認してください。 [y/N]:
```

承認した場合だけ `imu_corrector/config/imu_corrector.param.yaml` の
`angular_velocity_offset_x/y/z` を実測値で置換し、ノイズ設定やコメントを保持します。
拒否・空回答・入力終了では「更新見送り」を記録し、提出設定と車両別保存元を保持します。
独自補正などで対象設定や対応する 3 軸オフセットがない場合は、警告して更新をスキップします。

runtime は測定結果を `vehicle/` 内の一時ファイルに保存し、ホスト側で承認を確認してから
同じ測定結果を適用します。一時ファイルは処理後に削除します。計測後に参加者の設定が
変更された場合は更新を拒否し、再計測を求めます。

単体の `check_imu_bias.py` も、差分表示後に承認を確認します。
内部用の `--proposal-output` は設定を変更せず、`--apply-proposal` は承認後の適用用です。
`--bias-output <path>` を指定した場合だけ、承認後に車両別保存元にも記録します。

**更新した IMU バイアスの反映には Autoware の再起動が必要です。**

## 車両別 IMU バイアスの保存

承認後の保存先は `vehicle/.calibration/<VEHICLE_ID>/imu_bias.yaml` です。
ID は環境変数 → リポジトリ直下の `.env` → 既存のホスト名対応の順で取得します。
未設定・未知の ID では警告して車両別保存だけ省略し、計測と承認確認は実行します。

保存元は Git 管理対象で、`make workspace-clean` の削除範囲外です。
A2・A3・A4・A6・A7・test の同梱値は初期値の 0 であり、実測値ではありません。
承認後に更新すると、その車両の `imu_bias.yaml` にローカル差分が残ります。
保存元は記録用で、次の提出物に自動適用する値ではありません。

各ファイルは一時ファイルから rename して保存します。保存元への書き込みが失敗した場合は
提出設定を元に戻して失敗を報告します。復元にも失敗した場合はそのエラーも明示します。
プロセス中断を含む 2 ファイルの完全なトランザクションではありません。

`imu_bias.yaml` は有限値の 3 キーだけを持つフラットな YAML です。

```yaml
angular_velocity_offset_x: 0.001
angular_velocity_offset_y: -0.002
angular_velocity_offset_z: 0.003
```
