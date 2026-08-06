# 遠隔操作環境構築方法・遠隔操作方法

走行中の緊急停止や手動走行は、車両に接続したノートPCに接続されたゲームコントローラを用いて遠隔操作で行う。

ノートPCと自動運転車両間の通信が途絶したり、ゲームコントローラがノートPCから抜けたときには自動運転車両が緊急停止するような安全機能が入っている。

※途絶判定のしきい値が5秒になっているため、ゲームコントローラが抜けた瞬間に車両が止まるわけではないことに注意。

![遠隔操作 zenoh 構成図](docs/remote-topology.svg)

# 第1部 共通セットアップ

### 1-1. ROS 2 Humble

```shell
ros2 topic list   # 動けばOK。
```

### 1-2. リポジトリ取得

```shell
cd $HOME
git clone git@github.com:AutomotiveAIChallenge/aichallenge-racingkart.git
cd aichallenge-racingkart
```

### 1-3. 環境セットアップ & イメージ取得

```shell
./setup.bash bootstrap
```

### 1-4. Zenoh bridge（ホストにインストール）

```shell
sudo dpkg -i vehicle/zenoh-bridge-ros2dds_1.5.0_amd64.deb
apt list --installed zenoh-bridge-ros2dds   # 1.5.0 ならOK
```

### 1-5. ゲームコントローラ用 joy パッケージ

```shell
sudo apt install -y ros-humble-joy
sudo usermod -aG input "$USER"
# 再ログインして反映する
```

### 1-6. TLS 証明書の配置

`remote/tls/` に配布された tls.zip を展開する。

```shell
ls remote/tls          # client  server
ls remote/tls/client   # cert.pem  key.pem(600)
ls remote/tls/server   # minica.pem
```

### 1-7. CycloneDDS / RMW 設定

`~/.bashrc` に以下があること：

```shell
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml
```

`/opt/autoware/cyclonedds.xml` の NetworkInterface に `name="lo"` があること：

```shell
grep -i NetworkInterface ${CYCLONEDDS_URI#file://}
```

### 1-8. `.env` に車両番号を設定

リポジトリ直下の `.env` を開き、`VEHICLE_ID` の行を対象車両に合わせて書き換える(以降Axと記載の部分は、A3, A6など対象車両に合わせて変更する)：

```diff
- VEHICLE_ID=A0
+ VEHICLE_ID=Ax
```

---

# 第2部 ノートPC1台で遠隔操作動作確認

**目的**：PC 1台の中に「車両側」「遠隔側」を両方立て、両者を実EC2に client 接続し、joy 操作が EC2 経由で車両側に届くことを確認する。

### 2-1. 手順

ターミナルウィンドウを5つ（端末A〜E）用意する。

```shell
# 端末A: 車両側 zenoh bridge（domain1 → EC2）
cd ~/aichallenge-racingkart
make zenoh

# 端末B: 車両側の joy 受け手（domain1）。echo 自体が subscriber になり bridge が転送を開始
source /opt/ros/humble/setup.bash
ROS_DOMAIN_ID=1 ros2 topic echo /racing_kart/joy

# 端末C: 遠隔側 zenoh bridge（domain0 → EC2）
cd ~/aichallenge-racingkart/remote
ROS_DOMAIN_ID=0 ./connect_zenoh.bash Ax

# 端末D: 遠隔側で joy を流す（domain0）
source /opt/ros/humble/setup.bash
ROS_DOMAIN_ID=0 ros2 topic pub -r 10 /racing_kart/joy sensor_msgs/msg/Joy \
  '{header: {frame_id: "joy"}, axes: [0.1,0.5,0.0,0.0], buttons: [1,0,0,0]}'
```

### 2-2. 合否判定

```shell
# 端末E
source /opt/ros/humble/setup.bash
ROS_DOMAIN_ID=1 ros2 topic hz /racing_kart/joy     # ~10Hz なら合格
```

### 2-3. 終了手順

端末B・C・D・E はフォアグラウンドのホストプロセスなので、`make down` では止まらない。先に各端末で Ctrl+C する。

```shell
# 1) 端末B(echo) / 端末C(zenoh bridge) / 端末D(topic pub) / 端末E(hz) をそれぞれ Ctrl+C

# 2) 端末A で起動した zenoh コンテナを停止
cd ~/aichallenge-racingkart
make down

# 3) 残存していないことを確認
docker compose ps                  # 何も残っていないこと
pgrep -af zenoh-bridge-ros2dds     # 何も出ないこと
```

---

# 第3部 実機構成（実車両＋遠隔PC）

実車両と遠隔PC を EC2 経由でつなぐ本番の遠隔操作。
遠隔PC は EC2 経由で車両側と zenoh 接続するため、**遠隔PC 側にインターネット接続が必須**。

### 3-1. ゲームコントローラの接続

USBケーブルでゲームコントローラ（ロジクールF310）を遠隔PCに接続する。

### 3-2. 遠隔操作の流れ（遠隔PC 側）

ターミナルウィンドウを3つ用意する。

遠隔PC では `.env` の `ROS_DOMAIN_ID` を `0` に設定しておく。

```diff
- ROS_DOMAIN_ID=1
+ ROS_DOMAIN_ID=0
```

なお第2部の1台構成では、車両側 zenoh を domain 1 で起動する必要があるため `.env` は `ROS_DOMAIN_ID=1` のままにする。

```shell
# 端末A: joy_node（コントローラ入力 → /racing_kart/joy）
cd ~/aichallenge-racingkart/remote
ROS_DOMAIN_ID=0 ./joy.bash

# 端末B: 車両と zenoh 接続（EC2 へ client 接続 / TLS）
cd ~/aichallenge-racingkart/remote
ROS_DOMAIN_ID=0 ./connect_zenoh.bash Ax

# 端末C: RViz（遠隔可視化スタック）
cd ~/aichallenge-racingkart/remote
./rviz.bash
```

`rviz.bash` は `make rviz2` のラッパで、rviz2 をコンテナとして起動する（`./rviz.bash restart` で開き直し、`./rviz.bash down` で停止）。

### 3-3. 車両側 ECU の起動

車両側 ECU では別途 `make autoware-driver-zenoh-rosbag` で driver/autoware/rosbag/zenoh を起動する。`.env` の設定や IMU バイアスの調整を含む実車側の手順は [vehicle/README.md](../vehicle/README.md)、ECU 自体の初期構築（OS / udev / ネットワーク / Tailscale）は [vehicle/ecu-setup.md](../vehicle/ecu-setup.md) にまとまっている。

### 3-4. 終了手順

端末A の `joy.bash`（joy_node）と端末B の `connect_zenoh.bash`（zenoh bridge）はホスト上のフォアグラウンドプロセスであり、`make down` は Docker Compose のサービスしか停止しない。逆に端末C の rviz2 はコンテナなので Ctrl+C では止まらず、`make down`（または `./rviz.bash down`）が必要。

```shell
# 1) 端末A(joy.bash) と 端末B(connect_zenoh.bash) をそれぞれ Ctrl+C で停止

# 2) コンテナを停止（rviz2 はここで止まる）
cd ~/aichallenge-racingkart
make down

# 3) 何も残っていないことを確認
docker compose ps                  # 何も残っていないこと
pgrep -af zenoh-bridge-ros2dds     # 何も出ないこと
pgrep -af joy_node                 # 何も出ないこと
```

---

# 第4部 ゲームコントローラの使い方

ロジクールF310を使用して遠隔操作する。

https://gaming.logicool.co.jp/ja-jp/products/gamepads/f310-gamepad.940-000137.html

![ロジクール F310](docs/f310-controller.png)

ゲームコントローラの各ボタンの機能は以下の図の通り。

![F310 ジョイスティックマッピング（ボタン/軸割当）](docs/f310-button-mapping.png)
