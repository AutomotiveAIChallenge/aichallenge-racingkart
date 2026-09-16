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
2. **ネットワーク・通信確認** - インターネット接続、Zenohサーバー疎通
3. **Docker・環境確認** - Docker動作、イメージ存在、環境変数
4. **既知問題予防チェック** - 過去の実験から抽出した予防項目
5. **実行準備確認** - リポジトリルート、gitブランチ確認

runtime（起動後）でチェックする項目：
1. **ハードウェア通信確認** - CANのリンク状態とトラフィック／エラーフレーム
2. **Dockerサービス確認** - `driver` / `autoware` / `zenoh` の稼働（`rosbag` は必須にしない）
3. **GNSS/RTK状態確認** - `/sensing/gnss/navpvt` の RTK fixed / float 判定
4. **ROS topic出力確認** - 生IMU・車両status・最終指令・autoware制御指令の出力

`make autoware-driver-zenoh-rosbag` は起動前に preflight を自動実行します。runtime は起動後に別途実行します。
`make setup-vehicle` は `--phase all` 相当なので、**スタック起動中** に実行してください（停止中に叩くと runtime 系が一斉に fail します）。

参加者 TUI は `check preflight` → `accel brake map and IMU bias` → `autoware-vehicle`
→ `check runtime` → `autoware-vehicle down` の順です。
`accel brake map and IMU bias` は、Autoware 停止中に共通 map と保存済み IMU バイアスを
それぞれ確認し、承認後にビルド済みの設定へ適用します。Enter / y で推奨値を適用し、n で保持します。
runtime での計測・設定更新はありません。詳細は [設定の適用手順](calibration.md) を参照してください。

詳細な確認項目と手動コマンドについては [setup_check.md](./setup_check.md) を参照してください。

## 起動/停止（Makefile / docker compose）

起動・停止はリポジトリルートの `Makefile` と `docker-compose.yml` を使います。

### 起動（例）

```bash
# ビルド済み提出物の map・IMU バイアスを承認後に適用して起動
python3 vehicle/apply_calibration.py
make autoware-vehicle

# Racing Kart ドライバー
make driver

# Zenoh bridge
make zenoh

# まとめて起動（Autoware + Driver + Zenoh）
make autoware-driver-zenoh
```

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
