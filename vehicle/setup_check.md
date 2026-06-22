# Racing Kart 走行前セットアップ確認

## 概要

このドキュメントは、racing_kart_interface実行前およびrun-full-system実行前の確認項目をまとめています。
過去の実験記録から抽出した問題点を予防的にチェックできます。

## 自動チェックスクリプト

```bash
# 基本実行（推奨）
./setup_check.sh

# ログファイル出力付き実行
./setup_check.sh --log

# ヘルプ表示
./setup_check.sh --help
```

## チェック項目（5段階）

### 1. ハードウェアデバイス確認

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

### 2. ネットワーク・通信確認

#### インターネット接続
```bash
# 手動確認コマンド
ping -c 3 8.8.8.8
ip route get 8.8.8.8
getent hosts zenoh.dev.aichallenge-board.jsae.or.jp
```

#### リバースSSH
```bash
# 手動確認コマンド
systemctl is-active --quiet reverse-ssh.service
sudo systemctl status reverse-ssh.service
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
- ✅ `reverse-ssh.service is active (running)`
- ⚠️ `reverse-ssh.service is not active`

---

### 3. Docker・環境確認

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
- ✅ `Required compose services are running: driver autoware rosbag zenoh`

#### 環境変数・権限
```bash
# 手動確認コマンド
echo $XAUTHORITY
groups $USER
```

**期待する結果:**
- ✅ `XAUTHORITY is set: /home/user/.Xauthority`
- ✅ `User t4tanaka in dialout group`

---

### 4. 既知問題予防チェック

#### past_log.mdからの予防項目

**バッテリー管理注意**
- ⚠️ `Remember: Check battery level manually (display values unreliable)`
- ⚠️ `Remember: Avoid direct sunlight exposure for batteries`

**GNSS Fix推奨事項**
- ℹ️ `Recommendation: Wait outside for GNSS Fix before driving`
- ℹ️ `Recommendation: Check Fix status reaches ~80% before starting`

---

### 5. 実行準備確認

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

## 出力例

```bash
$ ./setup_check.sh

========================================
Racing Kart Setup Check
Mode: vehicle
Time: 2025年  8月 25日 月曜日 22:54:19 JST
========================================

ℹ️ 1. Hardware Device Check
----------------------------------------
❌ CAN interface can0 not found
   Fix: Check CAN hardware connection
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
⚠️ reverse-ssh.service is not active
   Fix: sudo systemctl start reverse-ssh.service
✅ Zenoh endpoint connectivity (A6: zenoh.dev.aichallenge-board.jsae.or.jp:7450)

ℹ️ 3. Docker & Environment Check
----------------------------------------
✅ Docker command available
✅ Docker daemon is running
✅ Racing kart interface image: ghcr.io/tier4/racing_kart_interface:latest-experiment (2025-08-25 10:30:45 +0900 JST)
✅ ai-challenge dev image: aichallenge-2025-dev-t4tanaka:latest (2025-08-24 15:22:11 +0900 JST)
✅ XAUTHORITY is set: $USER/.Xauthority
✅ User t4tanaka in dialout group

ℹ️ 4. Known Issues Prevention Check
----------------------------------------
⚠️ Remember: Check battery level manually (display values unreliable)
⚠️ Remember: Avoid direct sunlight exposure for batteries
ℹ️ Recommendation: Wait outside for GNSS Fix before driving
ℹ️ Recommendation: Check Fix status reaches ~80% before starting

ℹ️ 5. Execution Readiness Check (Vehicle Mode)
----------------------------------------
✅ docker-compose.yml exists at repo root: /path/to/aichallenge-racingkart/docker-compose.yml
ℹ️ Current git branch: experiment

========================================
📊 Check Results Summary
========================================
Total checks: 18
✅ Passed: 12
⚠️ Warnings: 3
❌ Failed: 3

❌ Critical issues found! Fix failures before running vehicle mode.

Recommended actions:
1. Address all failed checks above
2. Re-run this script
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
