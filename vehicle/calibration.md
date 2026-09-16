# 起動前の accel/brake map・保存済み IMU バイアス適用

Autoware 停止中に提出物を展開し、参加者の承認後に設定を上書きします。
その後にビルド・起動するため、最初の起動から更新した設定が使われます。

`check preflight` → `extract`（map・保存済み IMU バイアス適用）→ `build` → `autoware-vehicle` → `check runtime`

runtime の IMU キャリブレーションはありません。`setup_check.sh` はどのフェーズでも
IMU バイアスを計測・上書きせず、runtime では生 IMU のトピック受信だけを確認します。

## 提出物の展開

```bash
make submission-extract SUBMISSION_ID=<id>
make autoware-build
make autoware-vehicle
vehicle/setup_check.sh --phase runtime
```

参加者 TUI の `extract` も同じ処理です。前の Autoware が動いている場合は、
先に `autoware-vehicle down` で止めてから提出物を入れ替えてください。

ZIP を一時ディレクトリへ展開した後、map と IMU をそれぞれ確認します。
拒否・空回答・入力終了では、その項目の参加者設定を保持します。
明示的に `y` / `yes` と回答した場合だけ更新します（大文字も可）。

## accel/brake map

```text
提出物の accel/brake map を AWSIM adapter の共通mapで上書きしますか？
参加者の承認を確認してください。 [y/N]:
```

| コピー元 | 提出物内の適用先 |
| --- | --- |
| `aichallenge_awsim_adapter/data/accel_map.csv` | `aichallenge_submit_launch/data/accel_map.csv` |
| `aichallenge_awsim_adapter/data/brake_map.csv` | `aichallenge_submit_launch/data/brake_map.csv` |

コピー元は `aichallenge/workspace/src/aichallenge_system/` 以下です。
全車両で共通のファイルを使います。`steer_map.csv` は変更しません。
コピー前に数値テーブル・有限値・速度とペダルの軸の昇順を検証します。

## IMU 角速度バイアス

保存元は `vehicle/.calibration/<VEHICLE_ID>/imu_bias.yaml` です。
ID は環境変数 → リポジトリ直下の `.env` → 既存の ECU ホスト名対応の順に解決します。
別車両やゼロへのフォールバックはありません。

現在値・保存値・差分（保存値 − 現在値、rad/s）を表示して確認します。

```text
提出物の IMU 角速度バイアスを車両 A2 の保存値で上書きしますか？
参加者の承認を確認してください。 [y/N]:
```

承認後だけ、提出物の `imu_corrector/config/imu_corrector.param.yaml` にある
`angular_velocity_offset_x/y/z` を保存値で置換します。ノイズ設定・コメント・元の権限は保持します。
`imu_corrector` は `raw - offset` で補正するため、保存値の符号を反転しません。
保存元のファイルは変更しません。承認待ちの間に適用先が変更された場合は更新を拒否します。

保存元は有限値の 3 キーだけを持つ YAML です。

```yaml
angular_velocity_offset_x: 0.001
angular_velocity_offset_y: -0.002
angular_velocity_offset_z: 0.003
```

保存元は Git 管理対象で、`make workspace-clean` の削除範囲外です。
A2・A3・A6・A7 の同梱値は、[2026-09-14 の静止計測結果](.calibration/measurements/imu_bias_results_20260914_2208.md)
に基づく実測値です。A4 は [2026-09-16 の静止計測結果](.calibration/measurements/imu_bias_results_a4_20260916_1055.md)
に基づく実測値です。JSON の `proposal.offsets` を既存の保存形式に合わせて小数点以下 6 桁で記録しています。
計測結果の CSV・JSON・Markdown は `.calibration/measurements/` に保持します。
`test` は計測対象外のため初期値の 0 を保持しており、実測値ではありません。

## スキップ・失敗時

提出物に対象 map や対応する 3 軸の IMU 設定がない場合は、警告してその項目をスキップします。
独自補正の提出物に新しい設定ファイルは作りません。

保存元が欠落・不正、または車両 ID が未設定の場合は警告します。IMU 更新を拒否すれば
提出物の値を保持して展開できます。更新を承認した場合は失敗し、既存の提出物を残します。
map の検証・書き込み失敗も同様です。一時展開先で map と IMU の処理が両方完了するまで、
現在の `aichallenge_submit/` を入れ替えません。

最後の入れ替えは従来どおり削除→rename で、その間のプロセス中断からの復旧は保証しません。
ビルド成果物の削除は `workspace-clean` の責務です。

既に展開済みの提出物へ保存値だけを適用する場合は、Autoware 停止中に
`python3 vehicle/apply_imu_bias.py` を実行し、その後にビルド・起動します。
通常の展開フローでは同じ適用処理が呼ばれるため、この単独実行は不要です。

`check_imu_bias.py` は整備時に明示的に使う単体ツールとして残しています。
通常の提出物展開・TUI・runtime からは呼びません。
