# AI Challenge - ECU Setup

実車に載せる ECU（MiniPC）を、更地の状態から `./setup_check.sh --phase preflight` が通る状態まで仕立てる手順。

対象機材は GMKtec M4 MiniPC。ビルド待ちを含めて半日程度かかる。作業中は `sudo` とインターネット接続が必要。

本書は第1部から第8部まで、上から順に実行すれば ECU が構築できる runbook。

---

## 号機ごとの設定値

**ECU ホスト名の番号と `VEHICLE_ID` の番号は一致しない。** `ECU-RK-00` は `A7`、`ECU-RK-01` は `A2` である。番号から推測せず必ずこの表を引く。

作業前に、担当する号機の行をメモしてから始める。

| ECU ホスト名 | `VEHICLE_ID` | zenoh ポート |
| --- | --- | --- |
| `ECU-RK-00` | `A7` | 7451 |
| `ECU-RK-01` | `A2` | 7448 |
| `ECU-RK-02` | `A3` | 7449 |
| `ECU-RK-06` | `A6` | 7450 |
| 要確認 | `A1` | 7452  |
| 要確認 | `A5` | 7453 |
| 要確認 | `A8` | 7454 |
| 要確認 | `A4` | 未割当 |

「要確認」はホスト名の対応が未記録という意味、「未割当」はそもそも割り当てが存在しないという意味。区別すること。ホスト名が「要確認」の号機を触る場合は、ECU 上で `hostname` を実行してこの表を埋めてから作業する。10 台の内訳や `ECU-RK-03`〜`05` の有無など、資産管理としての全体像は運営のみが把握しており本書では特定できない。

`VEHICLE_ID` は Tailscale（headscale）に参加する際のホスト名（`--hostname`）としても使う（6-2）。

列ごとの出典は次のとおり。**この表を直したらスクリプト側も直す（逆も同様）。**

| 列 | 正となるファイル |
| --- | --- |
| ECU ホスト名 ↔ `VEHICLE_ID` | [../remote/connect_zenoh.bash](../remote/connect_zenoh.bash)（各 `echo` の `(ECU-RK-01)` 等） |
| zenoh ポート | [run_zenoh.bash](./run_zenoh.bash)（`connect_zenoh.bash` と一致必須） |
| 探索用 MAC | [../remote/detail/scan_ip_addr.py](../remote/detail/scan_ip_addr.py) |

なお、ユーザー名・パスワード、NTRIP アカウントは本書には記載しない。運営から別途配布される。

---

# 第1部 OS とユーザー

この部は OS を再インストールしない限り 2 度目は不要。

### 1-1. 用意するもの

- MiniPC 本体と電源アダプタ
- Ubuntu 22.04 インストール USB（[isoファイル](https://releases.ubuntu.com/22.04/)。USB メディアの作成手順は本書の範囲外）
- 有線 LAN ケーブル（インストール中の更新取得に使う）またはテザリング
- キーボード / マウス / HDMI ディスプレイ
- VCU（白い箱。USB 給電で動き、USB で ECU に接続する）
- GNSS 受信機、PCAN、ECU に接続するルータ（機種は問わない。有線 LAN で接続する）

別途配布される情報：管理ユーザー名 / パスワード、参加者アカウント一覧、NTRIP アカウント。

### 1-2. Ubuntu 22.04 のインストール

USB から起動してインストーラを進める。選択肢は次のとおり。

| 項目 | 設定値 |
| --- | --- |
| 言語 | English（インストール後に日本語へ切り替える） |
| キーボードレイアウト | Japanese / Japanese |
| インストール種別 | Normal Installation ＋ Download updates while installing Ubuntu |
| ディスク | Erase disk and install Ubuntu |
| タイムゾーン | Tokyo |
| Your computer's name | 「号機ごとの設定値」表の ECU ホスト名（例 `ECU-RK-01`） |
| Your name / username | 管理ユーザー（別途配布） |

`Download updates while installing` を選ぶので、**インストール前に LAN ケーブルを挿しておく**。

インストール後、`Settings > Region & Language` で表示言語を日本語に変更する（任意）。

### 1-3. 管理ユーザーの初期設定

以降のコマンドはすべて管理ユーザーで実行する。参加者アカウントは第8部で別に追加する。

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

### 1-4. 一時的にインターネットへ接続する

この時点では有線 LAN か手元のテザリングでよい。第2部の `bootstrap` が Docker イメージや AWSIM をダウンロードするため、安定した回線で始めたい。本番のルータへの接続は第4部で行う。

---

# 第2部 リポジトリと Docker 環境

### 2-1. リポジトリの取得と setup.bash bootstrap

```bash
cd "$HOME"
git clone https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git
cd aichallenge-racingkart
./setup.bash bootstrap
```

`bootstrap` は各ステップを y/N で確認しながら進む。これ 1 本で次が終わる。

- 基本パッケージ（`git` / `curl` / `make` / `python3` など）の導入
- Docker の導入と `docker` グループへの追加
- **DDS ホストチューニングの永続化** — `/etc/sysctl.d/10-cyclone-max-receive-buffer-size.conf`（`net.core.rmem_max`）と `/etc/systemd/system/multicast-lo.service`（`lo` のマルチキャスト有効化）を作成し enable する
- `.env` の生成（GPU / CPU 自動判定、`HOST_UID` / `HOST_GID` / `HOST_GID_DIALOUT` / `HOST_GID_INPUT` の実測）
- Autoware ベースイメージの pull
- dev イメージのビルド（`./docker_build.sh dev`）と `make autoware-build`

各ステップの詳細は [../docs/spec/how-to-setup.md](../docs/spec/how-to-setup.md) を参照。

### 2-2. ECU では bootstrap のどのステップを飛ばすか

ECU は AWSIM を起動しないので、対話プロンプトで次のように答える。

| プロンプト | ECU での回答 | 理由 |
| --- | --- | --- |
| `Install base packages (apt)` | y | |
| `Install Docker (if missing)` | y | |
| `Install rocker (pip)` | n | rocker 経路は廃止済み。起動は docker compose |
| `Add user to docker group (recommended)` | y | |
| `Configure host DDS tuning (rmem_max + lo multicast, sudo)` | **必ず y** | 4-3 の手作業が不要になる。ここで飛ばすと後で手作業が必要になる |
| `Clone/update repository` | y | |
| `Run repo doctor` | y | |
| `Pull Autoware base image` | y | |
| `Download AWSIM.zip and extract` | **n** | ECU では AWSIM を起動しない（約数 GB の節約） |
| `Build dev image: ./docker_build.sh dev` | y | |
| `Run make autoware-build (this can take a while)` | y | |
| `Run make dev (ROS_DOMAIN_ID from .env)` | **n** | AWSIM を使うシミュレータ起動なので不要 |

### 2-3. bootstrap が面倒を見ない設定

`setup.bash` は `dialout` グループの GID を読んで `.env` の `HOST_GID_DIALOUT` に書くだけで、**ユーザー自身を `dialout` に入れることはしない**。VCU と GNSS のシリアルデバイスを開くために手動で追加する。

```bash
sudo usermod -aG dialout "$USER"
```

`docker` と `dialout` はどちらも**再ログインしないと反映されない**。ここで一度ログアウト / ログインし、反映を確認する。

```bash
groups            # docker と dialout が含まれること
```

`.env` の `HOST_GID_DIALOUT` は `./setup.bash env` を実行した時点の実測値。グループ構成を変えた場合は `./setup.bash env` をやり直す。この GID が `docker-compose.yml` の `group_add` に渡り、コンテナ内から `/dev/gnss/usb` を開けるようになる（設計は [../docs/spec/host-uid-containers.md](../docs/spec/host-uid-containers.md)）。

### 2-4. driver イメージの取得

`docker-compose.yml` の driver サービスは `pull_policy: never` なので、**自動では取得されない**。取得を忘れると `docker compose up` が即座に失敗する。タグの正は `docker-compose.yml` の `x-racing_kart_interface-base` の `image:`（`ghcr.io/tier4/racing_kart_interface:latest-experiment`）である。ここが変わったら本書も直す。

`racing_kart_interface` は private リポジトリ（[github.com/tier4/racing_kart_interface](https://github.com/tier4/racing_kart_interface)）由来のイメージなので匿名 pull はできない。実験場では GHCR への直接アクセスを前提とせず、USB 外部 SSD で搬入した tar を `docker load` する。

USB 外部 SSD を実験場 PC に挿し、固定パスへマウントする。

```bash
sudo mkdir -p /mnt/racing_kart_image_transfer
sudo mount -t ntfs3 /dev/disk/by-uuid/4AF69B46F69B30E5 /mnt/racing_kart_image_transfer
```

別の SSD を使う場合は UUID を `lsblk -f` や `blkid` で確認して置き換える。

tar ファイルが壊れていないことを確認する。

```bash
cd /mnt/racing_kart_image_transfer
sha256sum -c racing_kart_interface_latest-experiment.tar.sha256
```

`racing_kart_interface_latest-experiment.tar: OK` と表示されれば正常。

イメージを load する。

```bash
docker load -i /mnt/racing_kart_image_transfer/racing_kart_interface_latest-experiment.tar
```

`make driver` が参照するタグが入っていることを確認する。

```bash
docker image inspect ghcr.io/tier4/racing_kart_interface:latest-experiment --format '{{.RepoTags}} {{.Id}} {{.Created}}'
```

`ghcr.io/tier4/racing_kart_interface:latest-experiment` が表示されれば OK。**ここではまだ `make driver` を実行しない**（第3部の udev ルールより先に driver コンテナを起動すると `/dev/vcu` / `/dev/gnss` が root 所有の空ディレクトリになる）。イメージを差し替えた場合の `make down && make driver` による再作成・起動確認は、udev 設定後の実際の車両起動（[README.md](./README.md)）で行う。

SSD を取り外す。`Powered off` の表示を確認してから抜く。別の SSD の場合は `ls /dev/disk/by-id/ | grep usb` で対応するパスを確認する。

```bash
cd ~
sudo umount /mnt/racing_kart_image_transfer
udisksctl power-off -b /dev/disk/by-id/usb-BUFFALO_SSD-PUT_N_0040578530733098-0:0
```

zenoh サービスは dev イメージ（`aichallenge-2025-dev`）で動くので、追加の取得は不要。

GHCR に直接ネットワーク到達でき、認証情報も配布されている場合（実験場外での構築など）は `docker pull` でもよい。

```bash
docker pull ghcr.io/tier4/racing_kart_interface:latest-experiment
docker images | grep racing_kart_interface
```

`unauthorized` になる場合は運営から GHCR 用の認証情報（Personal Access Token 等）の配布を受けて `docker login ghcr.io` する。この配布・権限管理自体は運営のみが対応できる。

---

# 第3部 デバイスの永続化（udev / CAN）

**udev ルールを作る前に `docker compose up`（`make driver` 等）を実行しない。** 先に実行すると Docker が `/dev/vcu` と `/dev/gnss` を root 所有の空ディレクトリとして作ってしまい、udev が symlink を張れなくなる。順序を守る：**udev ルール作成 → 再起動または再挿抜 → はじめてコンテナ起動。**

### 3-1. VCU の udev ルール

VCU は CP2102N の USB-UART ブリッジとして見える。挿抜のたびに `ttyUSB*` の番号が変わるので、固定名 `/dev/vcu/usb` を張る。

```bash
sudo vim /etc/udev/rules.d/89-vcu.rules
```

```
KERNEL=="ttyUSB[0-9]*", ENV{ID_MODEL}=="CP2102N_USB_to_UART_Bridge_Controller", SYMLINK+="vcu/usb", MODE="0666"
```

`ID_MODEL` が違うハードウェアを引いた場合は実測して書き換える。

```bash
udevadm info -a -n /dev/ttyUSB0 | grep -m1 ID_MODEL
```

### 3-2. GNSS の udev ルール

```bash
sudo vim /etc/udev/rules.d/90-gnss.rules
```

```
SUBSYSTEM=="tty", KERNEL=="ttyACM*", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a9", SYMLINK+="gnss/usb", MODE="0660", GROUP="dialout"
```

`GROUP="dialout"` と 2-3 の `usermod -aG dialout`、`docker-compose.yml` の `group_add: ["${HOST_GID_DIALOUT}"]` は 3 点セット。どれか 1 つ欠けるとコンテナ内から GNSS を開けない。

### 3-3. ルールの適用と確認

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

VCU と GNSS を接続した状態で symlink を確認する。`ls /dev | grep vcu` ではなく `-l` を付けて symlink 先まで見る。

```bash
ls -l /dev/vcu/usb /dev/gnss/usb
# lrwxrwxrwx ... /dev/vcu/usb -> ../ttyUSB0
# lrwxrwxrwx ... /dev/gnss/usb -> ../ttyACM1
```

所有者とグループも確認する。`/dev/gnss/usb` の実体が `dialout` グループになっていること。

```bash
ls -lL /dev/gnss/usb
```

`/dev/vcu` や `/dev/gnss` が root 所有の空ディレクトリになってしまっている場合は次で回復する。

```bash
sudo rm -rf /dev/vcu /dev/gnss
sudo udevadm control --reload-rules && sudo udevadm trigger
# それでも出てこない場合は VCU / GNSS を USB から抜き差しする
```

### 3-4. CAN（PCAN）

PCAN-USB アダプタを ECU に USB 接続する。この物理接続がなければ `can0` はそもそも現れない。接続した状態で `can0` が見えることを確認する。

```bash
ip link show can0
```

`can0` の bring-up は driver コンテナ（`racing_kart_interface`）が起動時に自動で行う。`docker/entrypoint.sh` が `vehicle` / `bench` / `free` モードで `utils/detail/pcan.bash` を呼び、その中で `sudo ip link set can0 type can bitrate 1000000` → `sudo ip link set can0 up` を実行している。ホスト側やこの ECU 構築の時点で手動 bring-up する必要はない。カーネル側の PCAN ドライバ（`peak_usb`）も Ubuntu 22.04 の標準カーネルに含まれており、別途インストールは不要。

以下はトラブルシュート用の手動コマンド。

```bash
sudo ip link set can0 up type can bitrate 1000000
ip -details -statistics link show can0    # ERROR-ACTIVE なら正常
```

`can0` が見つからない場合はドライバの認識から確認する。

```bash
lsusb | grep -i can
dmesg | grep -i can
```

---

# 第4部 ネットワーク

ECU はネットワーク接続に Wi-Fi を使わず、有線接続のみで運用する。ルータは機種を問わず、有線 LAN で ECU に直結すれば NetworkManager が自動で DHCP アドレスを取得するので、接続自体に追加設定は不要。

### 4-1. 内蔵 Wi-Fi を無効化する

内蔵 Wi-Fi を有効なまま残すと、意図せず何らかのネットワークへ接続し、有線接続とデフォルトルートを争うことがある。使わない機能なので先に無効化する。

MAC アドレスは筐体ごとに異なるので、対象 ECU 上で実測する。

```bash
ip link show                                  # wlp* / wlan* の内蔵 IF 名を特定
cat /sys/class/net/<内蔵IF名>/address          # 例: c0:4b:24:c1:03:de
```

udev でリンクを down させる。

```bash
sudo vim /etc/udev/rules.d/10-disable-internal-wifi.rules
```

```
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c0:4b:24:c1:03:de", RUN+="/usr/bin/ip link set %k down"
```

udev で down させても NetworkManager が再度上げてしまうため、NetworkManager 側でも管理対象から外す。

```bash
sudo vim /etc/NetworkManager/conf.d/99-unmanage-internal-wifi.conf
```

```ini
[keyfile]
unmanaged-devices=mac:c0:4b:24:c1:03:de
```

上記の MAC は例。**実測値に置き換える。** 反映して確認する。

```bash
sudo systemctl restart NetworkManager
nmcli device status            # 内蔵 Wi-Fi が unmanaged / down になっていること
```

### 4-2. ファイアウォール

```bash
sudo ufw disable
```

必要な通信は zenoh の発信（TCP 7448-7454）と Tailscale の発信（UDP 41641、DERP 経由になった場合は TCP 443）だけなので、本来は必要なポートだけ許可するのが正しい。`ufw disable` はその手間を省いた乱暴な近道であり、会場ネットワーク内でのみ許容している。

### 4-3. DDS ホストチューニング（bootstrap で実施済み）

2-1 の `bootstrap` で必ず実行され、`/etc/sysctl.d/10-cyclone-max-receive-buffer-size.conf` と `multicast-lo.service` として永続化される。ここでは確認だけ行う。

```bash
sysctl net.core.rmem_max                       # 2147483647
systemctl is-enabled multicast-lo.service      # enabled
ip -d link show lo | grep -i multicast         # MULTICAST があること
```

---

# 第5部 ホストの ROS 2 環境

`./setup_check.sh` は `docker compose exec` 経由で `ros2` を叩くので、チェックを通すだけならホストの ROS 2 は不要。ただしホストから直接トピックを覗けると調査が段違いに速いため、ECU にも入れる。

### 5-1. ROS 2 Humble とビルドツール

[公式手順](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html)に従って `ros-humble-desktop` を入れたあと、ツール類を追加する。

```bash
sudo apt install -y python3-colcon-common-extensions python3-vcstool python3-rosdep
sudo rosdep init
rosdep update
sudo apt install -y ros-humble-rmw-cyclonedds-cpp
```

### 5-2. CycloneDDS（/opt/autoware/cyclonedds.xml）

ホスト側の設定ファイルはリポジトリ同梱のものをコピーして使う（コンテナ側は `docker-compose.yml` が `vehicle/cyclonedds.xml` をマウントするので別物）。

```bash
sudo mkdir -p /opt/autoware
sudo cp vehicle/cyclonedds.xml /opt/autoware/cyclonedds.xml
grep -i NetworkInterface /opt/autoware/cyclonedds.xml    # name="lo" があること
```

### 5-3. ~/.bashrc に入れるもの

`~/.bashrc` に追記する。

```bash
export PATH=$HOME/.local/bin:$PATH
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml
export XAUTHORITY=$HOME/.Xauthority
xhost +SI:localuser:root >/dev/null 2>&1
```

`XAUTHORITY` は `setup_check.sh` が warn で見る項目。`xhost +SI:localuser:root` は root で動くコンテナから X に描画させるため。参加者ユーザーを追加したら、そのユーザーにも `xhost +SI:localuser:<user>` を許可する。

### 5-4. racing_kart_interface（ホスト側ビルド済みツリー）の配置

rosbag 記録は `${RACING_KART_INTERFACE_DIR}/install/setup.bash` を source して custom message の型定義を読む（`aichallenge/utils/record_all_rosbag.bash`）。そのためホスト側にビルド済みツリーが必要で、`docker-compose.yml` が read-only で bind mount する。

取得元は [github.com/tier4/racing_kart_interface](https://github.com/tier4/racing_kart_interface)（private。運営からアクセス権限が付与される）。ROS 2 Humble と `python3-colcon-common-extensions` / `python3-vcstool` / `python3-rosdep`（5-1 で導入済み）を使う。

```bash
git clone git@github.com:tier4/racing_kart_interface.git
cd racing_kart_interface
mkdir -p depends
vcs import --shallow --input depends.repos depends
source /opt/ros/humble/setup.bash
rosdep install --ignore-src --from-paths "$(colcon list --paths-only --packages-up-to racing_kart_launch)"
colcon build --symlink-install --packages-up-to racing_kart_launch
```

**`utils/initialize_workspace.bash` は実行しない。** 依存取得（`vcs import` の行）は上と同じだが、それ以外に `geographiclib-tools` / `rtklib` の apt install と `rtk_str2str.service`（RTKLIB `str2str` でホストから直接 NTRIP 補正を GNSS へ流す systemd unit）の有効化も行う。`racing_kart_interface` の `src/` 配下のどのパッケージも geographiclib を参照しておらず、また現行の `racing_kart_launch`（`gnss.launch.xml`）はコンテナ内の `ntrip_client` ノードで NTRIP 補正を行っている（`NTRIP_USERNAME` / `NTRIP_PASSWORD` は本リポジトリの `.env` と同じ変数名）。`rtk_str2str.service` は 2025-08-28 の実験用コミット以降更新がなく、有効化すると同じ GNSS へ二重に補正を書き込みかねない。

配置したら `.env` にパスを書く。`colcon --symlink-install` が絶対 symlink を含むため、**絶対パス必須**。相対パスにすると rosbag 記録が失敗する。

```bash
ls "$HOME/racing_kart_interface/install/setup.bash"    # 存在すること
```

---

# 第6部 遠隔アクセス

### 6-1. sshd

```bash
sudo apt install -y openssh-server
systemctl is-enabled ssh      # enabled
```

### 6-2. Tailscale（headscale）への参加

ECU を自前の headscale サーバー（`vpn.dev.aichallenge-board.jsae.or.jp`）が管理する Tailnet に参加させる。参加すると、遠隔 PC は EC2 経由のポートフォワードなしに ECU のホスト名へ直接 SSH できる。

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale down
sudo tailscale up \
  --reset \
  --force-reauth \
  --login-server=https://vpn.dev.aichallenge-board.jsae.or.jp \
  --auth-key=<AUTH_KEY> \
  --hostname=<VEHICLE_ID>
tailscale status
```

`<AUTH_KEY>`（`hskey-auth-...` 形式）は運営から別途配布される。`<VEHICLE_ID>` は「号機ごとの設定値」表の値（例 `A2`）に置き換える。

```bash
tailscale status      # 自分の ECU が表示され、Online になっていること
```

遠隔 PC 側も同じ headscale サーバーへ参加させておけば、以降は `ssh <ユーザー名>@<VEHICLE_ID>` で直接入れる。

---

# 第7部 号機固有の設定と仕上げ

### 7-1. ホスト名

インストーラで設定していない、または作り直した場合。

```bash
sudo hostnamectl set-hostname ECU-RK-01
hostname
```

ホスト名は運用上の識別子で、`VEHICLE_ID` とは別物。**番号は一致しない**（`ECU-RK-01` = `A2`）。「号機ごとの設定値」表を引く。

### 7-2. .env

`.env` は `bootstrap` が生成済み。ECU 構築時に触るのは `VEHICLE_ID` だけ。

```bash
vim .env
```

```diff
- VEHICLE_ID=A0
+ VEHICLE_ID=A2
```

この値で zenoh の接続先ポートが決まる（`vehicle/run_zenoh.bash`）。残る `NTRIP_USERNAME` / `NTRIP_PASSWORD` / `RACING_KART_INTERFACE_DIR` と `HOST_*` / `COMPOSE_FILE` の扱いは [README.md](./README.md) の表が正。`ROS_DOMAIN_ID` は既定の `1` のままにする。

### 7-3. 便利ツール（任意）

```bash
sudo apt install -y tmux fzf
sudo snap install code --classic
```

`terminator` は `packages.txt` 経由でコンテナ側に入る。ホストにも欲しい場合は `sudo apt install -y terminator`。

---

# 第8部 参加者アカウントの追加

参加者を迎える前の事前準備として実行する。ユーザー名とパスワードは運営から別途配布される。

管理ユーザーで実行する。

```bash
sudo adduser <user>                     # Full Name 以降は Enter 連打でよい
sudo usermod -aG dialout,docker <user>
```

追加したユーザーでログインし、`~/.bashrc` に 5-3 の内容を追記してリポジトリを clone する。

```bash
cd "$HOME"
git clone https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git
cd aichallenge-racingkart
./setup.bash env      # このユーザーの UID/GID で .env を生成
```

管理ユーザー側で X の利用を許可する。

```bash
xhost +SI:localuser:<user>
```

追加した全ユーザーで実際にログインでき、`groups` に `docker` と `dialout` が出ることを確認する。

以降のビルドと走行手順は [README.md](./README.md) を参照。
