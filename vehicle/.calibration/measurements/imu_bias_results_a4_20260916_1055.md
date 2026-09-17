# A4 IMU ジャイロバイアス計測結果

計測開始: 2026-09-16T10:55:39.736087+09:00

[2026-09-14 の計測](imu_bias_results_20260914_2208.md)では IMU 受信 0 件だった A4 を再計測しました。
今回は `/sensing/imu/imu_raw` を 372 件取得し、全軸の標準偏差が 0.03 rad/s 以下でした。

## 計測条件

- 対象: `tier4@a4`（ホスト名 `ECU-RK-97`）。
- ユーザーが完全な静止と計測中に車体へ触れないことを確認しました。
- 起動済みの driver・Autoware・zenoh コンテナを維持し、Autoware コンテナ内から既存の IMU トピックを購読しました。
- ROS domain は 1、DDS の通信インターフェースは loopback です。IMU の配信元は `imu_converter` 1 ノードでした。
- ドライバは起動済みのため追加の起動待ちは設けず、ウォームアップ 2 秒、採取 5 秒で計測しました。
- `/vehicle/status/velocity_status` を受信し、計測中の最大絶対車速は 0.000 m/s（判定閾値 0.05 m/s）でした。
- 9 月 14 日と同じ `check_imu_bias.py` を `--proposal-output` モードで実行しました。
  計測ラッパーでノードへの参照を保持し、同じ `stats()` から丸め前の標準偏差も保存しています。
- 補正設定の SHA-256 は計測前後で一致しました。設定の適用と Autoware の再起動は行っていません。
- 計測前後で 3 コンテナの ID・イメージ・起動時刻・再起動回数・実行状態が一致し、一時 proposal ファイルは削除済みです。

## 計測値

単位は rad/s です。JSON の `proposal.offsets` を小数点以下 6 桁に丸め、
`vehicle/.calibration/A4/imu_bias.yaml` に保存します。
保存値は raw IMU の推定バイアスそのもので、`imu_corrector` は `raw - offset` で補正します。

| 軸 | 現在の補正値 | 推定バイアス | 差分（推定−現在） | 標準偏差 |
|---|---:|---:|---:|---:|
| X | +0.000000 | +0.007298 | +0.007298 | 0.002140 |
| Y | +0.000000 | -0.004994 | -0.004994 | 0.001890 |
| Z | -0.000000 | -0.008207 | -0.008207 | 0.002115 |

## 実行出力

```text
==== IMU gyro bias check ====
imu topic      : /sensing/imu/imu_raw
sampling       : 5.0s (after 2.0s warmup)
Keep the vehicle completely stationary during sampling.
stationary     : OK (max |velocity|=0.000 m/s)
samples        : 372

axis    bias[rad/s]         std  status
---------------------------------------
x     +0.007298  0.002140  OK
y     -0.004994  0.001890  OK
z     -0.008207  0.002115  OK

IMUジャイロバイアス [rad/s]: /aichallenge/workspace/src/aichallenge_submit/imu_corrector/config/imu_corrector.param.yaml
軸              現在値           実測値         差分(実測値−現在値)
----------------------------------------------------
x     +0.000000  +0.007298  +0.007298
y     +0.000000  -0.004994  -0.004994
z     -0.000000  -0.008207  -0.008207

IMU measurement complete; settings have not been changed.
```

## 保存ファイル

- [imu_bias_results_a4_20260916_1055.csv](imu_bias_results_a4_20260916_1055.csv): 軸ごとの丸め前の計測値とサンプル数。
- [imu_bias_results_a4_20260916_1055.json](imu_bias_results_a4_20260916_1055.json): 実行条件、出力、proposal、設定ハッシュ、コンテナ状態。

計測スクリプト SHA-256: `5f5b8d879e875e9d2483a84e8898f9e1f1f4e63635bf0991e5e5101bf62b717a`。
補正設定 SHA-256（計測前後共通）: `0070010e620771e324f980c0154915a2ba6cbead82a9a30e724132ee2ebc2706`。
