# AI Challenge 2025 - Vehicle Setup

## セットアップ確認スクリプト / Setup Check Script

走行前の車両環境確認用スクリプトが利用可能です。チェックは **起動前（preflight）** と **起動後（runtime）** の2フェーズに分かれています。
設定を更新する **IMU 校正（calibrate）** は、提出物の展開後・ビルド前に別途実行します。

```bash
# 起動前チェックのみ（driver/autoware を起動する前に実行）
./setup_check.sh --phase preflight

# IMU 校正（driver 起動済み・Autoware 停止中、提出物の展開後・ビルド前）
./setup_check.sh --phase calibrate

# 起動後チェックのみ（スタックが起動している状態で実行）
./setup_check.sh --phase runtime

# 全チェック（既定。スタック起動中に実行。校正・設定変更は含まない）
./setup_check.sh

# ログファイル出力付き実行
./setup_check.sh --log

# ヘルプ表示
./setup_check.sh --help
```

preflight（起動前）でチェックする項目：
1. **ハードウェアデバイス確認** - CAN、VCU、GNSS/RTK
2. **ネットワーク・通信確認** - インターネット接続、Zenohサーバー疎通
3. **Docker・環境確認** - Docker動作、イメージ存在、環境変数
4. **既知問題予防チェック** - 過去の実験から抽出した予防項目
5. **実行準備確認** - リポジトリルート、gitブランチ確認

runtime（起動後）でチェックする項目：
1. **ハードウェア通信確認** - CANのリンク状態とトラフィック／エラーフレーム
2. **Dockerサービス確認** - `driver` / `autoware` / `zenoh` の稼働（`rosbag` は必須にしない）
3. **GNSS/RTK状態確認** - `/sensing/gnss/navpvt` の RTK fixed / float 判定
4. **ROS topic出力確認** - 生IMU・車両status・最終指令・autoware制御指令の出力

`make autoware-driver-zenoh-rosbag` は起動前に preflight を自動実行します。起動後の runtime は別途実行します。
`make setup-vehicle` は `--phase all` 相当なので、**スタック起動中** に実行してください（停止中に叩くと runtime 系が一斉に fail します）。

`make calibrate-imu` は、静止状態のバイアスを測り、現在値・実測値・差分を表示して
参加者の承認後だけ `imu_corrector.param.yaml` の `angular_velocity_offset_*` を更新します。
提出物のビルドや Autoware の起動は不要です。driver が停止中、Autoware が起動中、
またはサービス状態が取得できない場合は失敗します。更新後のビルド・起動で反映します。
計測前に静止確認の `y/N` プロンプトが出ます。
誤って走行中に測ると誤ったバイアスを書き込むため、タイムアウトは設けておらず、回答するまで
待ちます（`y` 以外は skip(warn) として先へ進みます）。計測中の静止時ノイズが大きいときは
書き込まず、「車両に触れないでください」→再計測してよいか `y/N` の確認が入ります
（`y` の間は上限なく再計測）。

詳細な確認項目と手動コマンドについては [setup_check.md](./setup_check.md) を参照してください。

## 起動/停止（Makefile / docker compose）

起動・停止はリポジトリルートの `Makefile` と `docker-compose.yml` を使います。

### 起動（例）

```bash
# 運営: 土台を起動
make driver
make zenoh

# 参加者: Autoware 停止中に提出物を展開し、校正してから起動
make submission-extract  # accel/brake map の更新を確認
make calibrate-imu       # 静止計測・参加者の承認後に IMU 設定を更新
make autoware-build
make autoware-vehicle
vehicle/setup_check.sh --phase runtime
```

TUI でも `extract` → `calibrate IMU` → `build` → `autoware-vehicle` → `check runtime`
の順に実行します。詳細は [校正手順](calibration.md) を参照してください。

### 可視化 / 記録

```bash
# RViz2（前回を止めてから起動）
make rviz2

# rosbag（手動。対象 domain を指定）
CMD="env ROS_DOMAIN_ID=1 /aichallenge/utils/record_rosbag.bash" \
docker compose run --rm --no-deps autoware-command
```

### 停止 / 状態確認

```bash
make ps
make down

# 個別に止めたい場合（rosbag は起動したターミナルで Ctrl+C）
```

### ビルド / データ取得

```bash
# Autoware overlay ビルド
make autoware-build

# 提出物データのダウンロード
make download
make download SUBMISSION_ID=<id>
```
