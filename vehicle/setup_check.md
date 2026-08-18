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

## チェック項目

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

### 6. IMUジャイロバイアス計測（runtime フェーズ）

`--phase runtime` / `--phase all` で、autoware 起動後に **車両が静止している状態の**
ジャイロバイアスを推定し、静止時ノイズが十分小さければ `imu_corrector.param.yaml` の
`angular_velocity_offset_*` を測定値でそのまま上書きします（乖離の大小による閾値判定はなく、
無条件に書き込みます）。imu_corrector はパラメータを起動時に一度だけ読むため、
**書き換えても今動いている autoware には反映されません。次回 autoware を再起動したときから
新しい値が使われます。**

```bash
# runtime フェーズの一部として実行される
./setup_check.sh --phase runtime

# 静止プロンプトを出さずに実行（無人実行 / make 経由）
IMU_BIAS_ASSUME_STATIONARY=y ./setup_check.sh --phase runtime
IMU_BIAS_ASSUME_STATIONARY=y make autoware-driver-zenoh-rosbag

# 単体実行（コンテナ内）
docker compose exec autoware bash -lc \
  "source /opt/ros/humble/setup.bash; source /aichallenge/workspace/install/setup.bash; \
   python3 /vehicle/check_imu_bias.py"
```

対話端末では静止確認の `y/N` プロンプトが出ます（y=計測開始、N=skip）。
`IMU_BIAS_ASSUME_STATIONARY` が設定されていればプロンプトを出さずにその値を使い、
無回答のまま `IMU_BIAS_PROMPT_TIMEOUT_SEC`（既定 5 秒）が経過した場合は
skip（warn）として先へ進むので、起動フローが止まり続けることはありません。

計測中の静止時ノイズ（std）が `IMU_BIAS_STD_THRESHOLD` を超えた場合は、
バイアス推定値が信用できないため param.yaml への書き込みはせず、
「車両に触れないでください」と表示したうえで**再計測してよいか毎回確認します**
（自動では再計測しません）。ここも `y/N` で、`IMU_BIAS_ASSUME_STATIONARY` /
`IMU_BIAS_PROMPT_TIMEOUT_SEC` を流用します。`IMU_BIAS_MAX_RETRIES`
（既定 3 回）まで確認を繰り返し、それでもノイズが収まらない、または
確認で `N`/無回答だった場合はその時点で warn として先へ進みます。

**閾値（環境変数で調整可能）:**

| 環境変数 | 既定値 | 意味 |
| --- | --- | --- |
| `IMU_BIAS_DURATION_SEC` | 5 | サンプリング秒数（warmup を除く） |
| `IMU_BIAS_WARMUP_SEC` | 2 | 開始直後に捨てる秒数 |
| `IMU_BIAS_STD_THRESHOLD` | 0.03 rad/s | 静止時ジャイロ std の警告閾値（暫定値。`imu_corrector.param.yaml` の想定ノイズ既定値に合わせている。実測を踏まえて後で絞り込む） |
| `IMU_BIAS_VELOCITY_THRESHOLD` | 0.05 m/s | これを超えたら「動いた」と判定して測定中止（ノイズとは別扱いでリトライなし） |
| `IMU_BIAS_ASSUME_STATIONARY` | 未設定 | 静止確認プロンプト・再計測確認プロンプト共通の回答（`y` で実行） |
| `IMU_BIAS_PROMPT_TIMEOUT_SEC` | 5 | 各プロンプトの無回答タイムアウト秒数 |
| `IMU_BIAS_MAX_RETRIES` | 3 | ノイズ超過時の再計測確認の上限回数 |

**静止時ノイズが小さい場合（書き込み成功 / 終了コード 0）:**

```text
axis    bias[rad/s]         std  status
---------------------------------------
x     +0.000329  0.002042  OK
y     -0.000762  0.002062  OK
z     +0.001286  0.002076  OK

Updated /aichallenge/workspace/src/aichallenge_submit/imu_corrector/config/imu_corrector.param.yaml:
axis    old[rad/s]    new[rad/s]
--------------------------------
x     +0.000000  +0.000329
y     +0.000000  -0.000762
z     -0.000000  +0.001286

✅ imu_corrector.param.yaml updated.
   This bias will not take effect until autoware is restarted
   (imu_corrector reads the parameter once at startup).
```

`imu_corrector` は `output = raw - angular_velocity_offset` で補正するため、
**測定値を符号そのまま** param.yaml に書きます（+ にずれていれば + を書く）。乖離の大小は
判定せず常に上書きします。書き換え後は autoware を再起動しないと反映されません。

**静止時ノイズが大きい場合（書き込まない / 終了コード 4、再計測確認へ）:**

```text
axis    bias[rad/s]         std  status
---------------------------------------
x     +0.000356  0.020723  WARN(noisy)
y     -0.001511  0.020593  WARN(noisy)
z     +0.002232  0.021002  WARN(noisy)

⚠️  Stationary gyro noise exceeds 0.03 rad/s — do not touch the vehicle.
    The bias estimate above is unreliable while noisy. The vehicle may not
    have been completely stationary (engine/fan vibration, someone
    touching it), or the IMU itself is noisy.
```

この rc=4 を受けて setup_check.sh 側が「Do not touch the vehicle. Re-measure? [y/N]」と
確認し、`y` なら再計測、`N`/無回答/`IMU_BIAS_MAX_RETRIES` 到達なら warn として先へ進みます
（この場合 param.yaml は書き換えません）。

その他の終了コード:

- `3`: 測定不能（サンプリング中に車両が動いた／`/sensing/imu/imu_raw` が来ない／param.yaml を読めなかった。ノイズとは別扱いでリトライなし。param.yaml は書き換えません）

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
