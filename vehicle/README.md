# AI Challenge 2025 - Vehicle Setup

## セットアップ確認スクリプト / Setup Check Script

走行前の車両環境確認用スクリプトが利用可能です：

```bash
# 基本実行（推奨）
./setup_check.sh

# ログファイル出力付き実行
./setup_check.sh --log

# ヘルプ表示
./setup_check.sh --help
```

このスクリプトでは以下の項目をチェックします：
1. **ハードウェアデバイス確認** - CAN、VCU、GNSS/RTK
2. **ネットワーク・通信確認** - インターネット接続、リバースSSH、Zenohサーバー疎通
3. **システムサービス確認** - RTK関連サービスなど
4. **Docker・環境確認** - Docker動作、イメージ存在、権限設定
5. **既知問題予防チェック** - 過去の実験から抽出した予防項目
6. **実行準備確認** - docker-compose.yml、gitブランチ確認

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

### 可視化 / 記録

```bash
# RViz2（前回を止めてから起動）
make rviz2

# rosbag（手動。対象 domain を指定）
CMD="env ROS_DOMAIN_ID=1 /aichallenge/utils/record_rosbag.bash" \
docker compose -f docker-compose.yml run --rm --no-deps autoware-command
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
