# 起動前の accel/brake map・保存済み IMU バイアス適用

参加者 TUI の `accel brake map and IMU bias` で、共通 accel/brake map と
車両別の保存済み IMU バイアスを、参加者の承認後に適用します。

`check preflight` → `accel brake map and IMU bias` → `autoware-vehicle` → `check runtime`

Autoware が停止している状態で実行します。既に起動している場合は
`autoware-vehicle down` で停止してください。起動中の適用はエラーになります。
提出物は TUI 外で展開・ビルド済みにしておきます。適用後の再ビルドは不要です。

CLI から同じ確認を行う場合は、リポジトリ直下で次を実行します。

```bash
python3 vehicle/apply_calibration.py
make autoware-vehicle
vehicle/setup_check.sh --phase runtime
```

## 確認と適用先

map と IMU は別々に確認します。適用元を検証できた場合は
`Y: 推奨設定を適用する (Recommended)`、`[Y/n]` を表示します。
参加者の承認を確認して Enter または `y` / `yes` で適用します（大文字も可）。
独自補正・調整がある場合は `n` で現在値を保持できます。入力終了（EOF）も保持です。

更新対象は、起動時に読む `aichallenge/workspace/install/` 内の package share です。
標準の isolated install と merged install に対応します。

| 適用元 | 適用先（package share 内） |
| --- | --- |
| `aichallenge_system/aichallenge_awsim_adapter/data/accel_map.csv` | `aichallenge_submit_launch/data/accel_map.csv` |
| `aichallenge_system/aichallenge_awsim_adapter/data/brake_map.csv` | `aichallenge_submit_launch/data/brake_map.csv` |
| `vehicle/.calibration/<VEHICLE_ID>/imu_bias.yaml` | `imu_corrector/config/imu_corrector.param.yaml` |

map の適用元は `aichallenge/workspace/src/` 以下です。全車両共通で、`steer_map.csv` は変更しません。
IMU は現在値・保存値・差分（保存値 − 現在値、rad/s）を表示して確認し、
`angular_velocity_offset_x/y/z` のみを置換します。ノイズ設定・コメント・権限は保持します。
`imu_corrector` は `raw - offset` で補正するため、保存値の符号を反転しません。

symlink install ではリンク先のソース設定を更新し、リンク自体は維持します。
ビルドコンテナの `/aichallenge/` を指す絶対リンクは、ホストの対応するマウント元に解決します。
コピーされた install では実行時のコピーを更新します。後から再ビルドした場合は、起動前に再確認してください。

## 保存済み IMU バイアス

車両 ID は環境変数 → リポジトリ直下の `.env` → ECU ホスト名対応の順に解決します。
別車両やゼロへのフォールバックはありません。保存元のファイルは更新しません。

A2・A3・A6・A7 は [2026-09-14 の静止計測結果](.calibration/measurements/imu_bias_results_20260914_2208.md)、
A4 は [2026-09-16 の静止計測結果](.calibration/measurements/imu_bias_results_a4_20260916_1055.md)
の `proposal.offsets` を小数点以下 6 桁で記録しています。
`test` は計測対象外のため初期値の 0 です。

## 保持・失敗時

保存元が欠落・不正、または車両 ID が未設定の場合は、推奨扱いにせず `[y/N]` を表示します。
Enter・`n`・EOF は保持し、`y` で適用を選ぶと失敗します。
独自構成などで適用先の map や対応する 3 軸の IMU 設定がない場合は警告してスキップします。
ビルド済みワークスペースがない場合は、確認前に失敗します。

両方の確認が終わるまで実行時の設定には書き込みません。
承認待ちの間に更新対象が変更された場合は更新を拒否します。
書き込み失敗時は、それまでに更新した設定を元に戻し、復元にも失敗した場合はエラーを表示します。
プロセス中断を含む複数ファイルの完全なトランザクションではありません。
拒否して現在値を保持した場合も、確認フェーズは正常終了します。

## 展開・runtime・整備ツール

`make submission-extract` は TUI 外のコマンドとして残っています。
このコマンドも展開時に共通 map と保存済み IMU バイアスの適用をそれぞれ確認します。
ビルド済みの提出物には、TUI の `accel brake map and IMU bias` を実行してください。

`setup_check.sh` の runtime / all は生 IMU を含むトピック受信を検査し、
IMU バイアスの計測・上書き・承認確認は行いません。
`check_imu_bias.py` は整備時に明示的に使う単体ツールとして残しています。
