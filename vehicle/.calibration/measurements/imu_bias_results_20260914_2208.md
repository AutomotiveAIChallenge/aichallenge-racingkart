# IMU ジャイロバイアス計測結果

作成日時: 2026-09-14T22:11:33.348347+09:00

a2・a3・a4・a6・a7 の `/sensing/imu/imu_raw` を、同一版の `check_imu_bias.py` で計測しました。単位は rad/s です。

各台でセンサ起動待ち3秒、ウォームアップ2秒、採取5秒。標準偏差の判定閾値は各軸0.03 rad/sです。

全台が静止していることをユーザーに確認済みです。通常サービスは停止していたため、一時コンテナで `serial_driver` と `imu_converter` を起動しました。車速を配信するノードは起動しておらず、静止判定はユーザーの確認に基づきます。

補正設定は読み取り専用で参照し、推定値の適用はしていません。各台で設定ファイルのSHA-256が前後一致し、計測用コンテナの終了を確認しました。

## 一覧

| 車両 | X バイアス | Y バイアス | Z バイアス | 最大標準偏差 | サンプル数 | 結果 |
|---|---:|---:|---:|---:|---:|---|
| a2 | -0.016204 | +0.009572 | -0.002520 | 0.002100 | 373 | OK |
| a3 | +0.023206 | +0.014435 | -0.013185 | 0.002240 | 373 | OK |
| a4 | — | — | — | — | 0 | 計測不可（IMU 0件） |
| a6 | -0.022481 | +0.006758 | -0.004096 | 0.002047 | 373 | OK |
| a7 | -0.018335 | -0.001045 | -0.006849 | 0.002231 | 374 | OK |

a4はシリアルポートのオープンには成功しましたが、計測中のIMUトピック受信は0件、追加の8秒間のシリアル読み取りも0バイトでした。a4のバイアスは推定できていません。車両側（VCU・IMU）の電源状態を確認中です。

a6に置かれていた旧版は計測後に設定を自動更新する実装だったため、全台でこのPCの現行版を一時ディレクトリへコピーして実行しました。車両側のスクリプトは更新していません。

## 車両別の詳細

### a2

計測開始: 2026-09-14T22:08:55.447254+09:00

| 軸 | 現在の補正値 | 推定バイアス | 差分（推定−現在） | 標準偏差 |
|---|---:|---:|---:|---:|
| X | +0.000000 | -0.016204 | -0.016204 | 0.002100 |
| Y | +0.000000 | +0.009572 | +0.009572 | 0.001969 |
| Z | -0.000000 | -0.002520 | -0.002520 | 0.001961 |

実行出力:

```text
1789391338.604798 [173]    python3: selected interface "lo" is not multicast-capable: disabling multicast
==== IMU gyro bias check ====
imu topic      : /sensing/imu/imu_raw
sampling       : 5.0s (after 2.0s warmup)
Keep the vehicle completely stationary during sampling.
stationary     : velocity_status not received on /vehicle/status/velocity_status; relying on manual confirmation
samples        : 373

axis    bias[rad/s]         std  status
---------------------------------------
x     -0.016204  0.002100  OK
y     +0.009572  0.001969  OK
z     -0.002520  0.001961  OK

IMUジャイロバイアス [rad/s]: /imu_corrector.param.yaml
軸              現在値           実測値         差分(実測値−現在値)
----------------------------------------------------
x     +0.000000  -0.016204  -0.016204
y     +0.000000  +0.009572  +0.009572
z     -0.000000  -0.002520  -0.002520

IMU measurement complete; settings have not been changed.
```

### a3

計測開始: 2026-09-14T22:09:35.357642+09:00

| 軸 | 現在の補正値 | 推定バイアス | 差分（推定−現在） | 標準偏差 |
|---|---:|---:|---:|---:|
| X | +0.022117 | +0.023206 | +0.001089 | 0.001978 |
| Y | +0.014236 | +0.014435 | +0.000199 | 0.002240 |
| Z | -0.013560 | -0.013185 | +0.000375 | 0.002157 |

実行出力:

```text
1789391378.541125 [173]    python3: selected interface "lo" is not multicast-capable: disabling multicast
==== IMU gyro bias check ====
imu topic      : /sensing/imu/imu_raw
sampling       : 5.0s (after 2.0s warmup)
Keep the vehicle completely stationary during sampling.
stationary     : velocity_status not received on /vehicle/status/velocity_status; relying on manual confirmation
samples        : 373

axis    bias[rad/s]         std  status
---------------------------------------
x     +0.023206  0.001978  OK
y     +0.014435  0.002240  OK
z     -0.013185  0.002157  OK

IMUジャイロバイアス [rad/s]: /imu_corrector.param.yaml
軸              現在値           実測値         差分(実測値−現在値)
----------------------------------------------------
x     +0.022117  +0.023206  +0.001089
y     +0.014236  +0.014435  +0.000199
z     -0.013560  -0.013185  +0.000375

IMU measurement complete; settings have not been changed.
```

### a4

計測開始: 2026-09-14T22:09:51.066205+09:00

| 軸 | 現在の補正値 | 推定バイアス | 差分（推定−現在） | 標準偏差 |
|---|---:|---:|---:|---:|

実行出力:

```text
1789391394.227739 [173]    python3: selected interface "lo" is not multicast-capable: disabling multicast
==== IMU gyro bias check ====
imu topic      : /sensing/imu/imu_raw
sampling       : 5.0s (after 2.0s warmup)
Keep the vehicle completely stationary during sampling.
  ❌ Too few messages on /sensing/imu/imu_raw (0 < 10). Is the IMU driver up and publishing at a reasonable rate?
```

### a6

計測開始: 2026-09-14T22:10:13.627543+09:00

| 軸 | 現在の補正値 | 推定バイアス | 差分（推定−現在） | 標準偏差 |
|---|---:|---:|---:|---:|
| X | +0.000000 | -0.022481 | -0.022481 | 0.002047 |
| Y | +0.000000 | +0.006758 | +0.006758 | 0.001997 |
| Z | -0.000000 | -0.004096 | -0.004096 | 0.001972 |

実行出力:

```text
1789391416.801626 [173]    python3: selected interface "lo" is not multicast-capable: disabling multicast
==== IMU gyro bias check ====
imu topic      : /sensing/imu/imu_raw
sampling       : 5.0s (after 2.0s warmup)
Keep the vehicle completely stationary during sampling.
stationary     : velocity_status not received on /vehicle/status/velocity_status; relying on manual confirmation
samples        : 373

axis    bias[rad/s]         std  status
---------------------------------------
x     -0.022481  0.002047  OK
y     +0.006758  0.001997  OK
z     -0.004096  0.001972  OK

IMUジャイロバイアス [rad/s]: /imu_corrector.param.yaml
軸              現在値           実測値         差分(実測値−現在値)
----------------------------------------------------
x     +0.000000  -0.022481  -0.022481
y     +0.000000  +0.006758  +0.006758
z     -0.000000  -0.004096  -0.004096

IMU measurement complete; settings have not been changed.
```

### a7

計測開始: 2026-09-14T22:10:29.607462+09:00

| 軸 | 現在の補正値 | 推定バイアス | 差分（推定−現在） | 標準偏差 |
|---|---:|---:|---:|---:|
| X | +0.000000 | -0.018335 | -0.018335 | 0.002231 |
| Y | +0.000000 | -0.001045 | -0.001045 | 0.002094 |
| Z | -0.000000 | -0.006849 | -0.006849 | 0.001922 |

実行出力:

```text
1789391432.773894 [173]    python3: selected interface "lo" is not multicast-capable: disabling multicast
==== IMU gyro bias check ====
imu topic      : /sensing/imu/imu_raw
sampling       : 5.0s (after 2.0s warmup)
Keep the vehicle completely stationary during sampling.
stationary     : velocity_status not received on /vehicle/status/velocity_status; relying on manual confirmation
samples        : 374

axis    bias[rad/s]         std  status
---------------------------------------
x     -0.018335  0.002231  OK
y     -0.001045  0.002094  OK
z     -0.006849  0.001922  OK

IMUジャイロバイアス [rad/s]: /imu_corrector.param.yaml
軸              現在値           実測値         差分(実測値−現在値)
----------------------------------------------------
x     +0.000000  -0.018335  -0.018335
y     +0.000000  -0.001045  -0.001045
z     -0.000000  -0.006849  -0.006849

IMU measurement complete; settings have not been changed.
```

## 保存ファイル

- `imu_bias_results_20260914_2208.csv`: 車両・軸ごとの数値一覧。
- `imu_bias_results_20260914_2208.json`: 計測出力、推定値、設定値、ハッシュ、センサ起動ログを含む詳細データ。

初回a2では計測用コンテナのDDS設定が競合してセンサノードの初期化に失敗しました。設定を修正後、全台を同じ条件で計測しています。初回の診断ログもJSONに保存しています。
