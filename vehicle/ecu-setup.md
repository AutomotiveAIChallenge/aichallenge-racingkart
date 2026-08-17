# AI Challenge - ECU Setup

実車に載せるECUを、Ubuntuのインストールから初めて`./setup_check.sh --phase preflight`が通る状態にする。

---

# 第1部 OSとユーザー

### 1-1. 用意するもの

- MiniPC
- Ubuntuインストール用のUSBメモリ
- dockerイメージインストール用のSSD（容量が大きいのでSSD推奨）
- インターネット接続のある有線LAN

### 1-2. USBメディアを作成

[Ubuntu 22.04のisoファイル](https://releases.ubuntu.com/22.04/)を、[公式チュートリアル](https://ubuntu.com/tutorials/create-a-usb-stick-on-ubuntu#1-overview)を参考にUSBメモリへ書き込む。

LANケーブルを挿してインターネット接続を確保した上で、以下の選択でUSBインストールを進める。

| 項目 | 設定値 |
| --- | --- |
| 言語 | English（インストール後に日本語へ切り替える） |
| キーボードレイアウト | Japanese / Japanese |
| インストール種別 | Normal Installation＋Download updates while installing Ubuntu |
| ディスク | Erase disk and install Ubuntu |
| タイムゾーン | Tokyo |
| Your computer's name | 「号機ごとの設定値」表のECUホスト名（例`ECU-RK-01`） |
| Your name / username | 管理ユーザー（別途配布） |

インストール後、`Settings > Region & Language`で表示言語を日本語に変更する。

### 1-4. 管理ユーザーの初期設定

以降のコマンドはすべて管理ユーザーで実行する。参加者アカウントは第6部で別に追加する。

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```
---

# 第2部 リポジトリとDocker環境

### 2-1. リポジトリの取得とsetup.bash bootstrap

```bash
cd "$HOME"
git clone https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git
cd aichallenge-racingkart
./setup.bash bootstrap
# 必要であれば実験用ブランチに切り替える
# git checkout experiment
```

`bootstrap`は以下以外のステップで全てyを選択。

| プロンプト | ECUでの回答 | 理由 |
| --- | --- | --- |
| `Download AWSIM.zip and extract` | n | ECUではAWSIMを起動しない（約数GBの節約） |
| `Run make dev (ROS_DOMAIN_ID from .env)` | n | AWSIMを使うシミュレータ起動なので不要 |

VCUとGNSSのシリアルデバイスを開くために手動で以下を実行する。

```bash
sudo usermod -aG dialout "$USER"
```

反映させるために再ログインして以下コマンドで反映されているか確認。

```bash
groups            # docker と dialout が含まれること
```


### 2-2. racing_kart_interfaceイメージのインストール


共有された`racing_kart_interface_latest-experiment.tar.gz`と`.sha256`を、作業用PCで外部SSDにコピーしておく。

外部SSDをECUに挿し、デバイス名を確認してから固定パスへマウントする。

```bash
lsblk -f                                       # 外部SSDのデバイス名（例: sdb1）を確認
sudo mkdir -p /mnt/racing_kart_image_transfer
sudo mount /dev/sdX1 /mnt/racing_kart_image_transfer
```

`/dev/sdX1`は`lsblk -f`で確認した実際のデバイス名に置き換える。

tarファイルが壊れていないことを確認する。

```bash
cd /mnt/racing_kart_image_transfer
sha256sum -c racing_kart_interface_latest-experiment.tar.gz.sha256
```

`racing_kart_interface_latest-experiment.tar.gz: OK`と表示されれば正常。

イメージをloadする。`docker load`はgzip圧縮のまま読めるので、展開は不要。

```bash
docker load -i /mnt/racing_kart_image_transfer/racing_kart_interface_latest-experiment.tar.gz
```

参照するタグが入っていることを確認する。

```bash
docker image inspect ghcr.io/tier4/racing_kart_interface:latest-experiment --format '{{.RepoTags}} {{.Id}} {{.Created}}'
```

`ghcr.io/tier4/racing_kart_interface:latest-experiment`が表示されればOK。

SSDをアンマウントする。

```bash
cd ~
sudo umount /mnt/racing_kart_image_transfer
```

---

# 第3部 udevルールの設定

VCU・GNSSはttyUSB*/ttyACM*の番号が挿抜のたびに変わるので、固定名のudevルールを作る。

```bash
sudo vim /etc/udev/rules.d/89-vcu.rules
```

```
KERNEL=="ttyUSB[0-9]*", ENV{ID_MODEL}=="CP2102N_USB_to_UART_Bridge_Controller", SYMLINK+="vcu/usb", MODE="0666"
```


```bash
sudo vim /etc/udev/rules.d/90-gnss.rules
```

```
SUBSYSTEM=="tty", KERNEL=="ttyACM*", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a9", SYMLINK+="gnss/usb", MODE="0660", GROUP="dialout"
```

作成した2つのルールを反映する。

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

VCU・GNSSを接続してsymlinkと所有グループを確認する。

```bash
ls -l /dev/vcu/usb /dev/gnss/usb
ls -lL /dev/gnss/usb   # dialoutグループになっていること
```

`setup_check.sh`の`candump`チェック用に、CANのツールも入れておく。

```bash
sudo apt install -y can-utils
```

# 第4部 ネットワーク

ECUはネットワーク接続にWi-Fiを使わず、有線接続のみで運用する。内蔵Wi-Fiを有効なまま残すと、意図せず何らかのネットワークへ接続し、有線接続とデフォルトルートを争うことがある。使わない機能なので先に無効化する。

MACアドレスは筐体ごとに異なるので、対象ECU上で実測する。

```bash
ip link show                                  # wlp* / wlan* の内蔵 IF 名を特定
cat /sys/class/net/<内蔵IF名>/address          # 例: c0:4b:24:c1:03:de
```

udevでリンクをdownさせる。

```bash
sudo vim /etc/udev/rules.d/10-disable-internal-wifi.rules
```

```
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c0:4b:24:c1:03:de", RUN+="/usr/bin/ip link set %k down"
```

udevでdownさせてもNetworkManagerが再度上げてしまうため、NetworkManager側でも管理対象から外す。

```bash
sudo vim /etc/NetworkManager/conf.d/99-unmanage-internal-wifi.conf
```

```ini
[keyfile]
unmanaged-devices=mac:c0:4b:24:c1:03:de
```

上記のMACは例。実測値に置き換える。反映して確認する。

```bash
sudo systemctl restart NetworkManager
nmcli device status            # 内蔵 Wi-Fi が unmanaged / down になっていること
```

ファイアウォールを無効化する。

```bash
sudo ufw disable
```

SSHで接続できるようにしておく。

```bash
sudo apt install -y openssh-server
systemctl is-enabled ssh      # enabled
```

---

# 第5部 ホストのROS 2環境

ホストからもros2コマンドが使えるように、ros2のセットアップもしておく。

### 5-1. ROS 2 Humbleとビルドツール

[公式手順](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html)に従って`ros-humble-desktop`を入れる。

### 5-2. CycloneDDS（/opt/autoware/cyclonedds.xml）

ホスト側の設定ファイルはリポジトリ同梱のものをコピーして使う（コンテナ側は`docker-compose.yml`が`vehicle/cyclonedds.xml`をマウントするので別物）。

```bash
sudo apt install -y ros-humble-rmw-cyclonedds-cpp
sudo mkdir -p /opt/autoware
sudo cp "$HOME/aichallenge-racingkart/vehicle/cyclonedds.xml" /opt/autoware/cyclonedds.xml
grep -i NetworkInterface /opt/autoware/cyclonedds.xml    # name="lo" があること
```

### 5-3. ~/.bashrcに入れるもの

`~/.bashrc`に追記する。ROS 2の環境変数に加えて、dockerコンテナ内で起動したGUIアプリ（RVizなど）をホストの画面に描画するための設定もここでまとめて入れる。

```bash
export PATH=$HOME/.local/bin:$PATH
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml
export XAUTHORITY=$HOME/.Xauthority
xhost +SI:localuser:root >/dev/null 2>&1
```

---

# 第6部 動作確認

VCU・GNSS・PCAN-USBをすべてUSBに接続した状態で実行する。繋がっていないとハードウェアのチェックがFAILになる。

ここまでの手順が終わったら、FAILが無いことを確認する。

```bash
cd ~/aichallenge-racingkart/vehicle
./setup_check.sh --phase preflight
```

ここまででECUの構築は完了。実車両を走らせる手順は [README.md](./README.md) を参照。
