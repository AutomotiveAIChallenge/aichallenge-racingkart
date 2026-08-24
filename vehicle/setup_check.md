# Racing Kart 走行前セットアップ確認

## 概要

このドキュメントは、racing_kart_interface実行前およびrun-full-system実行前の確認項目をまとめています。
過去の実験記録から抽出した問題点を予防的にチェックできます。

## 自動チェックスクリプト

チェックは **起動前（preflight）** と **起動後（runtime）** の2フェーズに分かれています。

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

`Makefile` からの呼び出し:

| ターゲット | フェーズ |
| --- | --- |
| `make autoware-driver-zenoh-rosbag` | 起動前に `--phase preflight`、起動後に `--phase runtime` |
| `make setup-vehicle` | `--phase all`（runtime を含むため **スタック起動中** に実行する） |

preflight が fail すると exit code が非0になり、`docker compose up` に進まず中断します。

## チェック項目

セクション番号は表示順に振られるため、実行したフェーズごとに 1 から連番になります。

### preflight: 1. ハードウェアデバイス確認

#### CANインターフェース
```bash
# 手動確認コマンド
ip link show can0
ip -details link show can0
```

**期待する結果:**
- ✅ `CAN interface can0 is UP`
- ❌ `CAN interface can0 not found` → ハードウェア接続確認
- ❌ `CAN interface can0 exists but not UP` → `sudo ip link set can0 up type can bitrate 1000000`

#### VCU（Vehicle Control Unit）
```bash
# 手動確認コマンド
ls -la /dev/vcu/
test -e /dev/vcu/usb && echo "VCU OK" || echo "VCU NG"
```

**期待する結果:**
- ✅ `VCU directory exists: /dev/vcu`
- ✅ `VCU USB device exists: /dev/vcu/usb`
- ❌ `VCU directory missing` → VCU物理接続確認

#### GNSS・RTK
```bash
# 手動確認コマンド
ls -la /dev/gnss* /dev/ttyACM1* 2>/dev/null
ls -la /dev/gnss/usb
```

**期待する結果:**
- ✅ `GNSS serial devices found`
- ✅ `GNSS symlink exists: /dev/gnss/usb` (optional)
- ⚠️ `No GNSS serial devices found`

---

### preflight: 2. ネットワーク・通信確認

#### インターネット接続
```bash
# 手動確認コマンド
ping -c 3 8.8.8.8
ip route get 8.8.8.8
getent hosts zenoh.dev.aichallenge-board.jsae.or.jp
```

#### Zenohサーバー疎通
```bash
# 手動確認コマンド
export VEHICLE_ID=A6  # ECU-RK-06 の例
timeout 5 bash -c "echo >/dev/tcp/zenoh.dev.aichallenge-board.jsae.or.jp/7450"
nc -zv zenoh.dev.aichallenge-board.jsae.or.jp 7450
```

**期待する結果:**
- ✅ `Internet connectivity (8.8.8.8)`
- ✅ `Internet route available`
- ✅ `DNS resolution works`
- ✅ `Zenoh endpoint connectivity (A6: zenoh.dev.aichallenge-board.jsae.or.jp:7450)`

---

### preflight: 3. Docker・環境確認

#### Docker基本確認
```bash
# 手動確認コマンド
docker ps
docker images
docker compose -f ../docker-compose.yml ps
```

#### Dockerイメージ確認
**期待する結果:**
- ✅ `Racing kart interface image: ghcr.io/tier4/racing_kart_interface:latest-experiment (2025-08-25 10:30:45 +0900 JST)`
- ✅ `Aichallenge dev image: aichallenge-2025-dev-t4tanaka:latest (2025-08-24 15:22:11 +0900 JST)`

#### 環境変数
```bash
# 手動確認コマンド
echo $XAUTHORITY
```

**期待する結果:**
- ✅ `XAUTHORITY is set: /home/user/.Xauthority`
- ⚠️ `XAUTHORITY not set` → `export XAUTHORITY=~/.Xauthority`

---

### preflight: 4. 既知問題予防チェック

#### past_log.mdからの予防項目

**バッテリー管理注意**
- ⚠️ `Remember: Check battery level manually (display values unreliable)`

**GNSS Fix推奨事項**
- ℹ️ `Recommendation: Wait outside for GNSS Fix before driving`
- ℹ️ `Recommendation: Check Fix status reaches ~80% before starting`

---

### preflight: 5. 実行準備確認

#### リポジトリ状態
```bash
# 手動確認コマンド
git rev-parse --show-toplevel
git branch --show-current
```

**期待する結果:**
- ✅ `docker-compose.yml exists at repo root: ...`（存在する場合のみ）
- ℹ️ `Current git branch: experiment`

---

### runtime: 1. 起動後ハードウェア通信確認

CANが実際に通信しているかを見ます。エラーフレームが出ていれば配線・終端・ビットレート・モータ電源を疑います。

```bash
# 手動確認コマンド
ip link show can0
ip -details -statistics link show can0
candump -ta -e can0
```

**期待する結果:**
- ✅ `CAN interface can0 is UP`
- ✅ `CAN interface can0 state is ERROR-ACTIVE`
- ✅ `CAN traffic observed: 1234 frames, 12 IDs, no error frames`
- ❌ `CAN error frames observed during sample: ...` → モータ/コントローラ電源、CAN-H/CAN-L配線、終端、ビットレート確認
- ❌ `No CAN traffic observed in 3s` → VCU状態とモータ/コントローラ電源確認

環境変数 `CAN_IFACE` / `CAN_SAMPLE_SEC` / `CAN_MIN_FRAMES` で対象と閾値を変更できます。

---

### runtime: 2. 起動後Dockerサービス確認

```bash
# 手動確認コマンド
docker compose -f ../docker-compose.yml ps --services --filter status=running
```

**期待する結果:**
- ✅ `Required compose services are running: driver autoware rosbag zenoh`
- ❌ `Required compose services not running: zenoh` → 該当サービスのログを確認

---

### runtime: 3. GNSS/RTK状態確認

`driver` コンテナ内で `/sensing/gnss/navpvt` の `flags` を読み、RTKの状態を判定します。
`ros-humble-ublox-msgs` が入っていないと型解決に失敗するため `packages.txt` に含めています。

```bash
# 手動確認コマンド
docker compose -f ../docker-compose.yml exec -T driver bash -lc \
  'source /opt/ros/humble/setup.bash && source /workspace/install/setup.bash &&
   ros2 topic echo /sensing/gnss/navpvt --once --field flags'
```

**期待する結果:**
- ✅ `GNSS RTK fixed: NavPVT flags=131`
- ⚠️ `GNSS RTK float: NavPVT flags=67` → ichimilのアカウント/状態、補正データ接続、上空視界を確認。fixedになるまで待つ
- ❌ それ以外 → **fixedを確認するまで自動走行を開始しない**

待ち時間は `GNSS_NAVPVT_TIMEOUT_SEC` で変更できます。

---

### runtime: 4. ROS topic出力確認

`driver` / `autoware` の各コンテナで主要トピックにメッセージが出ているかを確認します。
`docker compose up` 直後はまだ出ていないことがあるため、1トピックあたり `ROS_TOPIC_RETRY` 回まで再試行します。

**確認するトピック:**

| コンテナ | トピック |
| --- | --- |
| `driver` | `/racing_kart/vcu/status`, `/racing_kart/steer/status`, `/racing_kart/brake/status`, `/racing_kart/joy` |
| `driver` | `/racing_kart/vcu/command`, `/racing_kart/steer/command`, `/racing_kart/brake/command` |
| `autoware` | `/vehicle/status/velocity_status`, `/vehicle/status/steering_status`, `/vehicle/status/gear_status`, `/vehicle/status/actuation_status` |
| `autoware` | `/control/command/control_cmd`, `/control/command/actuation_cmd` |

**期待する結果:**
- ✅ `VCU status: /racing_kart/vcu/status`
- ❌ `Control command: no message on /control/command/control_cmd within 4s x 2` → autowareの起動状況とログを確認

`ROS_TOPIC_TIMEOUT_SEC`（1回あたりの待ち秒数）と `ROS_TOPIC_RETRY`（試行回数）で調整できます。

---

## 出力例

### preflight

```bash
$ ./setup_check.sh --phase preflight

========================================
Racing Kart Setup Check
Mode: vehicle
Phase: preflight
Time: 2025年  8月 25日 月曜日 22:54:19 JST
========================================

ℹ️ 1. Hardware Device Check
----------------------------------------
❌ CAN interface can0 not found
   Fix: Check CAN hardware connection
✅ candump (can-utils) command available
❌ VCU directory missing: /dev/vcu
❌ VCU USB device missing: /dev/vcu/usb
✅ GNSS serial devices found
⚠️ GNSS symlink missing (optional): /dev/gnss/usb

ℹ️ 2. Network & Communication Check
----------------------------------------
✅ Internet connectivity (8.8.8.8)
✅ Internet route available
   Route: 8.8.8.8 via 192.168.x.x dev wlan0 src 192.168.x.x uid 1000
✅ DNS resolution works
ℹ️ Active NetworkManager connections: ...
✅ Zenoh endpoint connectivity (A6: zenoh.dev.aichallenge-board.jsae.or.jp:7450)

ℹ️ 3. Docker & Environment Check
----------------------------------------
✅ Docker command available
✅ Docker daemon is running
✅ Racing kart interface image: ghcr.io/tier4/racing_kart_interface:latest-experiment (2025-08-25 10:30:45 +0900 JST)
✅ Aichallenge dev image: aichallenge-2025-dev:latest (2025-08-24 15:22:11 +0900 JST)
✅ XAUTHORITY is set: /home/user/.Xauthority

ℹ️ 4. Known Issues Prevention Check
----------------------------------------
⚠️ Remember: Check battery level manually (display values unreliable)
ℹ️ Recommendation: Wait outside for GNSS Fix before driving
ℹ️ Recommendation: Check Fix status reaches ~80% before starting

ℹ️ 5. Execution Readiness Check (Vehicle Mode)
----------------------------------------
✅ docker-compose.yml exists at repo root: /path/to/aichallenge-racingkart/docker-compose.yml
ℹ️ Current git branch: experiment

========================================
📊 Check Results Summary
========================================
Total checks: 15
✅ Passed: 10
⚠️ Warnings: 2
❌ Failed: 3

❌ Critical issues found! Fix failures before running vehicle mode.

Recommended actions:
1. Address all failed checks above
2. Re-run this script
```

### runtime

`--phase runtime` では番号が改めて 1 から振られます。

```bash
$ ./setup_check.sh --phase runtime

========================================
Racing Kart Setup Check
Mode: vehicle
Phase: runtime
Time: 2025年  8月 25日 月曜日 23:10:02 JST
========================================

ℹ️ 1. Runtime Hardware Communication Check
----------------------------------------
✅ CAN interface can0 is UP
ℹ️ CAN traffic sample (can0, 3s)
✅ CAN interface can0 state is ERROR-ACTIVE
ℹ️ CAN berr-counter tx 0 rx 0
✅ CAN traffic observed: 4821 frames, 14 IDs, no error frames

ℹ️ 2. Runtime Docker Service Check
----------------------------------------
✅ Required compose services are running: driver autoware rosbag zenoh

ℹ️ 3. GNSS/RTK Status Check
----------------------------------------
✅ GNSS RTK fixed: NavPVT flags=131

ℹ️ 4. Runtime ROS Topic Output Check
----------------------------------------
ℹ️ Racing kart hardware/status topics
✅ VCU status: /racing_kart/vcu/status
✅ Steer status: /racing_kart/steer/status
...
ℹ️ Autoware downstream control command topics
✅ Control command: /control/command/control_cmd
✅ Actuation command: /control/command/actuation_cmd

========================================
📊 Check Results Summary
========================================
Total checks: 18
✅ Passed: 18
⚠️ Warnings: 0
❌ Failed: 0

✅ All checks passed! System ready for vehicle mode.
```

## 手動確認が必要な項目

### GNSS/RTK詳細確認
```bash
# ROS2でのGNSS状態確認（システム起動後）
ros2 topic echo /sensing/gnss/nav_sat_fix --field status.status
ros2 topic echo /sensing/gnss/nav_sat_fix --field covariance
ros2 topic hz /sensing/gnss/nav_sat_fix

# GNSS詳細監視
ros2 topic echo /sensing/gnss/monhw
ros2 topic echo /sensing/gnss/navpvt
```

### VCU・車両制御確認
```bash
# システム起動後の確認
ros2 topic echo /racing_kart/vcu/status
ros2 run joy joy_node --ros-args -r __ns:=/racing_kart
```

### ログ・記録確認
```bash
# mcap形式での記録
ros2 bag record -a --storage mcap
```

## トラブルシューティング

### よくある問題と対処法

1. **CAN interface not found**
   ```bash
   # CAN デバイス確認
   lsusb | grep -i can
   dmesg | grep -i can
   ```

2. **VCU device missing**
   ```bash
   # USB デバイス確認
   lsusb
   ls -la /dev/ttyACM*
   ```

3. **Docker permission denied**
   ```bash
   sudo usermod -aG docker $USER
   # ログアウト・ログインが必要
   ```

4. **X11 forwarding issues**
   ```bash
   export XAUTHORITY=~/.Xauthority
   xhost +local:docker
   ```

## 走行前最終チェックリスト

- [ ] setup_check.sh で全チェック通過
- [ ] バッテリー実測確認（表示値不正確）
- [ ] 直射日光下バッテリー放置回避
- [ ] GNSS Fix状態確認（外で一定時間待機）
- [ ] 車両各部の物理接続確認
- [ ] 適切なブランチにチェックアウト
- [ ] ルーター電源確認

このチェックリストと自動スクリプトにより、過去の実験で発生した問題を効果的に予防し、安定した車両システム運用が可能になります。
