# AI Challenge 2025 - Vehicle Setup

## セットアップ確認スクリプト / Setup Check Script

走行前の車両環境確認用スクリプトが利用可能です。チェックは **起動前（preflight）** と **起動後（runtime）** の2フェーズに分かれています。

```bash
# 起動前チェックのみ（driver/autoware を起動する前に実行）
./setup_check.sh --phase preflight

# 起動後チェックのみ（スタックが起動している状態で実行）
./setup_check.sh --phase runtime

# 全チェック（既定。runtime を含むためスタック起動中に実行する）
./setup_check.sh

# ログファイル出力付き実行
./setup_check.sh --log

# ヘルプ表示
./setup_check.sh --help
```

preflight（起動前）でチェックする項目：
1. **ハードウェアデバイス確認** - CAN、VCU、GNSS/RTK
2. **ネットワーク・通信確認** - インターネット接続、リバースSSH、Zenohサーバー疎通
3. **Docker・環境確認** - Docker動作、イメージ存在、環境変数
4. **既知問題予防チェック** - 過去の実験から抽出した予防項目
5. **実行準備確認** - リポジトリルート、gitブランチ確認

runtime（起動後）でチェックする項目：
1. **ハードウェア通信確認** - CANのリンク状態とトラフィック／エラーフレーム
2. **Dockerサービス確認** - `driver` / `autoware` / `rosbag` / `zenoh` の稼働
3. **GNSS/RTK状態確認** - `/sensing/gnss/navpvt` の RTK fixed / float 判定
4. **ROS topic出力確認** - 車両status・最終指令・autoware制御指令の出力
5. **IMUジャイロバイアス計測** - 静止時バイアスを測って `imu_corrector.param.yaml` を更新

`make autoware-driver-zenoh-rosbag` は起動前に preflight、起動後に runtime を自動実行します。
`make setup-vehicle` は `--phase all` 相当なので、**スタック起動中** に実行してください（停止中に叩くと runtime 系が一斉に fail します）。

IMU ジャイロバイアス計測は、静止状態のバイアスを測って `imu_corrector.param.yaml` の
`angular_velocity_offset_*` を測定値で上書きします（次回 autoware 再起動時から反映。
今動いているプロセスには効きません）。対話端末では静止確認の `y/N` プロンプトが出るため、
無人で通したい場合は `IMU_BIAS_ASSUME_STATIONARY=y` を渡してください（未回答のまま 5 秒
経過した場合も skip(warn) として先に進みます）。計測中の静止時ノイズが大きいときは書き込まず、
「車両に触れないでください」→再計測してよいか `y/N` の確認が入ります（`y` の間は上限なく
再計測。無人実行時は確認せず1回で終えます）。

```bash
# 静止プロンプトを出さずに runtime チェックまで通す
IMU_BIAS_ASSUME_STATIONARY=y ./setup_check.sh --phase runtime
IMU_BIAS_ASSUME_STATIONARY=y make autoware-driver-zenoh-rosbag
```

詳細な確認項目と手動コマンドについては [setup_check.md](./setup_check.md) を参照してください。

## 起動/停止（Makefile / docker compose）

起動・停止はリポジトリルートの `Makefile` と `docker-compose.yml` を使います。

### 起動（例）

```bash
# Autoware（vehicle mode）
make autoware-vehicle

# Racing Kart ドライバー
make driver

# Zenoh bridge
make zenoh

# まとめて起動（Autoware + Driver + Zenoh）
make autoware-driver-zenoh
```

### ドライバーのパラメータを現地で振る

`DRIVER_LAUNCH_ARGS` に書いた文字列は、そのまま driver の `ros2 launch` へ引数として渡ります。
イメージを焼き直さずに値を変えられるので、現地での調整はこれを使ってください。

```bash
# 例: スロットル上限を 0.35 にして起動
DRIVER_LAUNCH_ARGS="max_accel_ratio:=0.35" make autoware-driver-zenoh-rosbag
```

基準値は `.env` の `DRIVER_LAUNCH_ARGS` に書いておけます（コマンドラインで渡した値が優先されます）。

**値が実際に入ったかは毎回確認してください。** 引数名を間違えても ROS はエラーも警告も出さず、黙って無視します。

```bash
CMD="ros2 param get /racing_kart_driver max_accel_ratio" docker compose run --rm --no-deps autoware-command
```

渡せるのは racing_kart_interface 側の launch が `<arg>` として宣言しているパラメータだけです。

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
