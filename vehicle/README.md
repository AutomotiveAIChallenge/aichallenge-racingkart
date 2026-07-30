# AI Challenge - Vehicle Setup

ECU（MiniPC）自体の初期構築（OS / udev / ネットワーク / ROS 2 / Tailscale）は [ecu-setup.md](./ecu-setup.md) を参照。本書は構築済みの ECU で走らせるための手順。

## 実車両で走らせる場合の注意点

シミュレータと違い、実車両では以下を走行前に必ず確認する。

### 1. `.env` の設定

`.env` は git 管理外なので、`./setup.bash bootstrap`（または `./setup.bash env`）で `.env.example` から生成される。
生成後、**人が手で書き換えないといけない項目**は次の4つ。

| 変数 | `.env.example` の初期値 | 実車で必要な設定 |
| --- | --- | --- |
| `VEHICLE_ID` | `A0` | 走らせる号機（`A1`,`A2`,`A3`,`A5`,`A6`,`A7`,`A8`）。zenoh の接続先ポートがこの値で決まる（`vehicle/run_zenoh.bash`） |
| `NTRIP_USERNAME` | `your_username` | RTK 補正情報配信（NTRIP）のアカウント。未設定だと RTK Fix にならず自己位置の精度が出ない |
| `NTRIP_PASSWORD` | `your_password` | 同上 |
| `RACING_KART_INTERFACE_DIR` | `/home/tier4/racing_kart_interface` | racing_kart_interface の実際の配置先。**絶対パス必須**（`colcon --symlink-install` が絶対 symlink を含むため）。ここが違うと rosbag 記録も失敗する |

`ROS_DOMAIN_ID` は既定 `1` のままでよい。

`HOST_UID` / `HOST_GID` / `HOST_GID_DIALOUT` / `HOST_GID_INPUT` と `COMPOSE_FILE`（GPU 判定）は `./setup.bash env` が実測値で自動設定するので、通常は触らない。

### 2. IMUバイアスの修正

車両ごとに IMU のジャイロバイアスを実測して `imu_corrector` のパラメータを更新する。`/sensing/imu/imu_raw` は driver / autoware が動いていないと流れないため、実測は「3. 車両起動」で一度起動してから行う。

対象ファイル：`aichallenge/workspace/src/aichallenge_submit/imu_corrector/config/imu_corrector.param.yaml`

車両を静止させた状態で `/sensing/imu/imu_raw` の `angular_velocity` を観測し、各軸の平均値を `angular_velocity_offset_x` / `_y` / `_z` にそのまま書く（符号の反転は不要）。`--symlink-install` でビルドしているため再ビルドは不要で、autoware を再起動すれば反映される。

### 3. 車両起動

```bash
# 提出物データを aichallenge/workspace/src/ に取得（認証情報は対話入力）
make download                       # 提出物の一覧を表示して選択
make download SUBMISSION_ID=<id>    # 特定の提出物を指定（一覧をスキップ）

# 取得／持ち込んだコードをビルド
make autoware-build

# rosbagを記録する場合（セットアップ確認込み。実車では基本こちら）
make autoware-driver-zenoh-rosbag

# rosbagを記録しない場合（セットアップ確認は実行されない）
make autoware-driver-zenoh
```

`make autoware-driver-zenoh-rosbag` は以下を順に実行する：

1. `./setup_check.sh --phase preflight`（起動前チェック）
2. `driver` + `autoware` + `rosbag` を起動
3. 15 秒待って `zenoh` を起動
4. `./setup_check.sh --phase runtime`（起動後チェック）

`make autoware-driver-zenoh` は `driver` + `autoware` を起動して 15 秒待ち `zenoh` を起動するだけで、**セットアップ確認は実行されない**。こちらで起動した場合は別途 `make setup-vehicle` で確認する。

rosbag は全トピック（`-a --include-hidden-topics`）を mcap・60 秒分割で
`output/<timestamp>/d<ROS_DOMAIN_ID>/rosbag2_all/` に記録される。記録ログは同ディレクトリの `rosbag.log`。

### 4. 停止 / 状態確認

```bash
make ps      # 稼働中のコンテナ確認
make down    # 全コンテナ停止（rosbag もここで finalize される）
```

---

## セットアップ確認スクリプト / Setup Check Script

`make autoware-driver-zenoh-rosbag` が preflight / runtime の2フェーズを自動で実行するので、通常は個別に叩く必要はない。`make autoware-driver-zenoh` で起動した場合や単独で確認したい場合は `make setup-vehicle` を使う（こちらは両フェーズを実行するので、autoware が起動している状態で叩く）。

**preflight（起動前）で確認される項目**

1. **ハードウェアデバイス確認** - CAN、VCU、GNSS/RTK
2. **ネットワーク・通信確認** - インターネット接続、リバースSSH、Zenohサーバー疎通
3. **Docker・環境確認** - Docker動作、イメージ存在、権限設定
4. **既知問題予防チェック** - 過去の実験から抽出した予防項目
5. **実行準備確認** - リポジトリルート、gitブランチ確認

**runtime（起動後）で確認される項目**

1. **ハードウェア疎通確認** - CAN インターフェースの UP 状態とフレーム流量
2. **Docker サービス確認** - 必要な compose サービスが稼働しているか
3. **GNSS/RTK 状態確認** - `/sensing/gnss/navpvt` の RTK Fix 状態
4. **ROS トピック出力確認** - VCU status / command、Autoware vehicle status

各項目の期待される結果・手動確認コマンド・トラブルシューティング・走行前最終チェックリストは [setup_check.md](./setup_check.md) にまとまっている。
