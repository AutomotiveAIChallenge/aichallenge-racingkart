# Zenoh Router Setup Memo

## 目的

EC2 上に車両ごとの Zenoh Router を立て、遠隔 PC と車両側 PC の
`zenoh-bridge-ros2dds` を router 経由で接続する。

まず TCP で topic 疎通を確認し、その後 TLS/mTLS と systemd 常駐化へ進める。

## 車両ごとのポート

| 車両 | Port |
| --- | --- |
| 既存 | 7447 |
| A2 | 7448 |
| A3 | 7449 |
| A6 | 7450 |
| A7 | 7451 |
| A1 | 7452 |
| A5 | 7453 |
| A8 | 7454 |

## 現在のリポジトリ側の使われ方

遠隔 PC 側:

```bash
remote/connect_zenoh.bash
zenoh-bridge-ros2dds client -e <endpoint> -c zenoh-user.json5
```

車両側:

```bash
docker-compose.yml
zenoh-bridge-ros2dds client -e <endpoint> -c /vehicle/zenoh.json5
```

`remote/zenoh-user.json5` と `vehicle/zenoh.json5` は router 用ではなく、
両端の `zenoh-bridge-ros2dds` client 用の設定。

主な役割:

- publish/subscriber の allow list
- topic の frequency 制限
- topic priority
- TLS/mTLS の client 側証明書設定
- `mode: "client"`

Router 側は基本的に ROS 2 topic の allow list を持たず、Zenoh の中継と
listen endpoint、TLS/mTLS の server 側設定を持つ。

## 作業の流れ

- 手順1. TCP + 手動 `zenohd` + `/zenoh_test` 疎通
- 手順2. TCP + systemd 化
- 手順3. TLS/mTLS 設定
- 手順4. TLS + systemd 設定に差し替え
- 手順5. 車両側/遠隔側の接続先を正式反映

## 手順1. TCP + 手動 zenohd + /zenoh_test 疎通

基本方針:

- `zenohd` router は EC2 上で起動する。
- `/zenoh_test` までの疎通確認は、検証用 PC 1 台で実施する。
- 1 台の PC 上で remote 側と vehicle 側を別環境として起動し、ROS 2 DDS の直接疎通と Zenoh 経由の疎通が混ざらないようにする。
- `/racing_kart/joy` は、まず遠隔 PC と車両側 PC の 2 台構成で bridge 単体確認を行う。
- bridge 単体で通った後に、車両側の `driver`、`autoware`、`zenoh` をまとめて起動して実運用相当の最終確認を行う。

### 手順1-1. EC2 側

まず A2 の 7448 だけで確認する。

```bash
zenohd --listen tcp/0.0.0.0:7448
```

別ターミナルから TCP の到達性を確認する。

```bash
nc -vz 13.231.141.103 7448
```

### 手順1-2. 検証 PC 1 台で remote/vehicle を分ける

1 台 PC で確認する場合、remote 側と vehicle 側を同じ ROS_DOMAIN_ID で起動すると、
ローカル DDS で直接 topic が見えてしまい、Zenoh router 経由かどうかが曖昧になる。

そのため、`ROS_DOMAIN_ID` を分ける。

```text
remote 側:  ROS_DOMAIN_ID=10
vehicle 側: ROS_DOMAIN_ID=11
```

`/zenoh_test` の確認では既存の `remote/zenoh-user.json5` と `vehicle/zenoh.json5` を変更せず、
検証用 config `zenoh_tmp/zenoh-test.json5` を使う。

remote 側 bridge:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml \
ROS_DOMAIN_ID=10 \
zenoh-bridge-ros2dds client \
  -e tcp/<EC2_PUBLIC_IP>:7448 \
  -c zenoh_tmp/zenoh-test.json5
```

vehicle 側 bridge:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml \
ROS_DOMAIN_ID=11 \
zenoh-bridge-ros2dds client \
  -e tcp/<EC2_PUBLIC_IP>:7448 \
  -c zenoh_tmp/zenoh-test.json5
```

### 手順1-3. /zenoh_test 疎通

`zenoh_tmp/zenoh-test.json5` は `/zenoh_test` の publish/subscribe だけを許可する。

remote 側 domain で publish:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml \
ROS_DOMAIN_ID=10 \
ros2 topic pub /zenoh_test std_msgs/msg/String "{data: hello}" -r 1
```

vehicle 側 domain で echo:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml \
ROS_DOMAIN_ID=11 \
ros2 topic echo /zenoh_test
```

逆方向も確認する。

vehicle 側 domain で publish:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml \
ROS_DOMAIN_ID=11 \
ros2 topic pub /zenoh_test std_msgs/msg/String "{data: hello-from-vehicle}" -r 1
```

remote 側 domain で echo:

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml \
ROS_DOMAIN_ID=10 \
ros2 topic echo /zenoh_test
```

### 手順1-4. 2 台構成で bridge 単体の joy topic 疎通

`driver` と `autoware` は起動せず、Zenoh bridge だけで `/racing_kart/joy` の疎通を確認する。
TLS/mTLS 設定前なので、接続先は `tcp/13.231.141.103:<port>` を使う。

対象ポート:

| 車両 | TCP endpoint |
| --- | --- |
| A2 | `tcp/13.231.141.103:7448` |
| A3 | `tcp/13.231.141.103:7449` |
| A6 | `tcp/13.231.141.103:7450` |
| A7 | `tcp/13.231.141.103:7451` |
| A1 | `tcp/13.231.141.103:7452` |
| A5 | `tcp/13.231.141.103:7453` |
| A8 | `tcp/13.231.141.103:7454` |

#### 手順1-4-1. EC2 側 router を TCP で起動する

EC2 側で実行する。

```bash
zenohd --listen tcp/0.0.0.0:7448
```

#### 手順1-4-2. 車両側 PC で vehicle 側 bridge だけを起動する

車両側 PC で実行する。

```bash
zenoh-bridge-ros2dds client \
  -e tcp/13.231.141.103:7448 \
  -c vehicle/zenoh.json5
```

別車両を確認する場合は、対象車両に合わせて port を変更する。

#### 手順1-4-3. 遠隔 PC 側で remote 側 bridge を起動する

遠隔 PC 側で実行する。

```bash
cd remote
ZENOH_LOCAL_ENDPOINT=tcp/13.231.141.103:7448 ./connect_zenoh.bash test-remote
```

別車両を確認する場合は、対象車両に合わせて `ZENOH_LOCAL_ENDPOINT` の port を変更する。

#### 手順1-4-4. 遠隔 PC 側で joy topic を publish する

遠隔 PC 側で実行する。

```bash
cd remote
./joy.bash
```

別ターミナルで遠隔 PC 側で確認する。

```bash
ros2 topic list | grep joy
ros2 topic echo /racing_kart/joy
```

joy コントローラが手元にない場合は、疎通確認だけなら遠隔 PC 側で以下を一時的に publish する。

```bash
ros2 topic pub /racing_kart/joy sensor_msgs/msg/Joy \
  "{header: {frame_id: joy}, axes: [0.0, 0.0, 0.0, 0.0], buttons: [0, 0, 0, 0]}" -r 1
```

#### 手順1-4-5. 車両側 PC で `/racing_kart/joy` を確認する

車両側 PC 側で実行する。

```bash
ros2 topic list | grep joy
ros2 topic echo /racing_kart/joy
```

期待:

- 遠隔 PC で joy コントローラ操作に応じて `/racing_kart/joy` が publish される
- 車両側 PC でも同じ `/racing_kart/joy` が echo できる

### 手順1-5. 実運用相当の車両 stack 起動確認

手順1-4で bridge 単体の `/racing_kart/joy` 疎通が確認できたら、
次に車両側の `driver`、`autoware`、`zenoh` をまとめて起動し、実運用相当で確認する。

TLS/mTLS 設定前に行うため、車両側 [`docker-compose.yml`](../docker-compose.yml) の `zenoh` service を
一時的に TCP endpoint へ差し替えてから実行する。

また、`driver` service にも `ROS_DOMAIN_ID` が渡るように、
[`docker-compose.yml`](../docker-compose.yml) の `x-racing_kart_interface-base` に以下を追加する。

```yaml
- ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-1}
```

変更前:

```bash
zenoh-bridge-ros2dds client -e tls/zenoh.dev.aichallenge-board.jsae.or.jp:$$PORT -c /vehicle/zenoh.json5
```

手順1-5の TCP 確認中だけ、変更後:

```bash
zenoh-bridge-ros2dds client -e tcp/13.231.141.103:$$PORT -c /vehicle/zenoh.json5
```

A2 車両側 PC で起動する。

```bash
VEHICLE_ID=A2 make autoware-driver-zenoh
```

遠隔 PC 側で RViz を起動する。

```bash
make rviz2
```

遠隔 PC 側で Zenoh bridge を起動する。

```bash
cd remote
ZENOH_LOCAL_ENDPOINT=tcp/13.231.141.103:7448 ./connect_zenoh.bash test-remote
```

遠隔 PC 側で joy node を起動する。

```bash
cd remote
./joy.bash
```

joy コントローラが手元にない場合は、遠隔 PC 側でダミーの `/racing_kart/joy` を publish する。

```bash
ros2 topic pub /racing_kart/joy sensor_msgs/msg/Joy \
  "{header: {frame_id: joy}, axes: [0.0, 0.0, 0.0, 0.0], buttons: [0, 0, 0, 0]}" -r 1
```

車両側 PC で `/racing_kart/joy` を確認する。

```bash
ROS_DOMAIN_ID=1 ros2 topic echo /racing_kart/joy
ROS_DOMAIN_ID=1 ros2 topic info -v /racing_kart/joy
```

`topic info` で `racing_kart_driver` が subscriber として表示されることを確認する。

別車両を確認する場合は、対象車両に合わせて `VEHICLE_ID` と port を変更する。
ただし [`docker-compose.yml`](../docker-compose.yml) の `zenoh` service に対象車両の `case` が追加済みであることを確認する。

確認後は、手順1-5で一時的に変更した [`docker-compose.yml`](../docker-compose.yml) の endpoint を
本番用の `tls/zenoh.dev.aichallenge-board.jsae.or.jp:$$PORT` に戻す。

## 手順2. TCP + systemd 化

TCP で topic 疎通できたら、まず TLS なしのまま A2 用 router を systemd 化する。

今年は zenohd を apt でインストールしているため、実体は `/usr/bin/zenohd` にある。
systemd の `ExecStart` も `/usr/bin/zenohd` をそのまま使う。

```bash
sudo tee /etc/systemd/system/zenoh-router-a2.service >/dev/null <<'EOF'
[Unit]
Description=zenoh router A2
After=network-online.target
Wants=network-online.target

[Service]
User=root
ExecStart=/usr/bin/zenohd --listen tcp/0.0.0.0:7448
Restart=always
RestartSec=2s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
```

起動:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zenoh-router-a2
sudo systemctl status zenoh-router-a2
```

ログ:

```bash
journalctl -u zenoh-router-a2 -f
```

## 手順3. TLS/mTLS 設定

Router 側には router 用 config を用意する。

### 手順3-1. mTLS 用 minica を用意する

mTLS では client 側証明書も TLS client authentication 用途を持つ必要がある。
minica 標準の leaf 証明書は `ServerAuth` のみなので、証明書生成前に `ClientAuth` も入るように minica を修正してビルドする。

minica を取得する。

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/jsha/minica.git
cd minica
```

`main.go` の `sign()` 内にある `ExtKeyUsage` を変更する。

変更前:

```go
ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
```

変更後:

```go
ExtKeyUsage: []x509.ExtKeyUsage{
    x509.ExtKeyUsageServerAuth,
    x509.ExtKeyUsageClientAuth,
},
```

修正版 minica をビルドする。

```bash
go build -o minica
./minica --help
```

以降の証明書作成では、この修正版 minica を使う。
既に作成済みの証明書には反映されないため、minica 修正後に証明書を作り直す。

### 手順3-2. 暫定方針: IP アドレス直指定

明日の検証では DNS を使わず、今回作成した EC2 の Public IP を直接使う。

```text
13.231.141.103
```

client 側 endpoint は以下のようにする。

```bash
-e tls/13.231.141.103:7448
```

複数車両の場合:

```bash
-e tls/13.231.141.103:7448  # A2
-e tls/13.231.141.103:7449  # A3
-e tls/13.231.141.103:7450  # A6
-e tls/13.231.141.103:7451  # A7
-e tls/13.231.141.103:7452  # A1
-e tls/13.231.141.103:7453  # A5
-e tls/13.231.141.103:7454  # A8
```

注意:

- TLS では接続先と server 証明書の SAN が一致している必要がある。
- IP 直指定で接続する場合、server 証明書の SAN に `IP:13.231.141.103` を入れる。
- 既存 router の `57.180.63.135` 用証明書は今回の EC2 には流用しない。
- EC2 の Public IP が変わると証明書と接続先がずれるため、可能なら Elastic IP 化してから証明書を作る。
- 後で DNS 化する場合は、DNS 名を SAN に入れた server 証明書を作り直す。

今回の IP 直指定検証で使う配置:

```text
/etc/zenohd/tls/server/13.231.141.103/key.pem
/etc/zenohd/tls/server/13.231.141.103/cert.pem
/etc/zenohd/tls/client/minica.pem
```

将来 DNS 化した場合に使う配置:

```text
/etc/zenohd/tls/server/<ZENOH_ROUTER_DNS_NAME>/key.pem
/etc/zenohd/tls/server/<ZENOH_ROUTER_DNS_NAME>/cert.pem
/etc/zenohd/tls/client/minica.pem
```

### 手順3-3. 証明書作成

Zenoh 公式ドキュメントでは、TLS 証明書作成に `minica` を使う手順が案内されている。
そのため、この検証でも `minica` を本線にする。
ここでは手順3-1でビルドした修正版 minica を使う。

参考:

```text
https://zenoh.io/docs/manual/tls/
```

#### server 側証明書

IP アドレス直指定で接続するため、server 証明書は `13.231.141.103` の IP SAN 入りで作成する。

作業ディレクトリ:

```bash
mkdir -p ~/zenoh_tls/server
cd ~/zenoh_tls/server
```

server 証明書作成:

```bash
~/src/minica/minica --ip-addresses 13.231.141.103
```

生成物:

```text
~/zenoh_tls/server/
  13.231.141.103/
    cert.pem
    key.pem
  minica.pem
  minica-key.pem
```

EC2 router 側へ配置:

```bash
sudo mkdir -p /etc/zenohd/tls/server/13.231.141.103
sudo cp ~/zenoh_tls/server/13.231.141.103/key.pem /etc/zenohd/tls/server/13.231.141.103/key.pem
sudo cp ~/zenoh_tls/server/13.231.141.103/cert.pem /etc/zenohd/tls/server/13.231.141.103/cert.pem
```

client 側へ配布する server CA:

```text
~/zenoh_tls/server/minica.pem
```

この `minica.pem` を `remote/zenoh-user.json5` と `vehicle/zenoh.json5` の
`root_ca_certificate` から参照できる場所へ配置する。

#### client 側証明書

mTLS では router が client 証明書を検証するため、client 用の証明書も作成する。

作業ディレクトリ:

```bash
mkdir -p ~/zenoh_tls/client
cd ~/zenoh_tls/client
```

client 証明書作成:

```bash
~/src/minica/minica --domains aichallenge-zenoh-client
```

生成物:

```text
~/zenoh_tls/client/
  aichallenge-zenoh-client/
    cert.pem
    key.pem
  minica.pem
  minica-key.pem
```

remote/vehicle client 側へ配置するファイル:

```text
~/zenoh_tls/client/aichallenge-zenoh-client/key.pem
~/zenoh_tls/client/aichallenge-zenoh-client/cert.pem
```

EC2 router 側へ配置する client CA:

```bash
sudo mkdir -p /etc/zenohd/tls/client
sudo cp ~/zenoh_tls/client/minica.pem /etc/zenohd/tls/client/minica.pem
```

#### 証明書の対応

router 側:

```json5
"tls": {
  "root_ca_certificate": "/etc/zenohd/tls/client/minica.pem",
  "enable_mtls": true,
  "listen_private_key": "/etc/zenohd/tls/server/13.231.141.103/key.pem",
  "listen_certificate": "/etc/zenohd/tls/server/13.231.141.103/cert.pem"
}
```

client 側:

```json5
"tls": {
  "root_ca_certificate": "<server/minica.pem の配置先>",
  "enable_mtls": true,
  "connect_private_key": "<client/key.pem の配置先>",
  "connect_certificate": "<client/cert.pem の配置先>"
}
```

#### 確認

server 証明書に IP SAN が入っていることを確認する。

```bash
openssl x509 -in ~/zenoh_tls/server/13.231.141.103/cert.pem -noout -text \
  | grep -A2 "Subject Alternative Name"
```

期待:

```text
IP Address:13.231.141.103
```

証明書チェーン確認:

```bash
openssl verify -CAfile ~/zenoh_tls/server/minica.pem \
  ~/zenoh_tls/server/13.231.141.103/cert.pem

openssl verify -CAfile ~/zenoh_tls/client/minica.pem \
  ~/zenoh_tls/client/aichallenge-zenoh-client/cert.pem
```

期待:

```text
OK
```

作成する router config:

| 車両 | config | listen endpoint |
| --- | --- | --- |
| A2 | `/etc/zenoh/routers/router-a2.json5` | `tls/0.0.0.0:7448` |
| A3 | `/etc/zenoh/routers/router-a3.json5` | `tls/0.0.0.0:7449` |
| A6 | `/etc/zenoh/routers/router-a6.json5` | `tls/0.0.0.0:7450` |
| A7 | `/etc/zenoh/routers/router-a7.json5` | `tls/0.0.0.0:7451` |
| A1 | `/etc/zenoh/routers/router-a1.json5` | `tls/0.0.0.0:7452` |
| A5 | `/etc/zenoh/routers/router-a5.json5` | `tls/0.0.0.0:7453` |
| A8 | `/etc/zenoh/routers/router-a8.json5` | `tls/0.0.0.0:7454` |

各 config は listen endpoint のポートだけを車両ごとに変え、TLS 証明書設定は同じにする。

A2 用 `/etc/zenoh/routers/router-a2.json5`:

```json5
{
  "mode": "router",
  "scouting": {
    "multicast": { "enabled": false },
    "gossip": { "enabled": false }
  },
  "listen": {
    "endpoints": ["tls/0.0.0.0:7448"]
  },
  "connect": {
    "endpoints": []
  },
  "transport": {
    "unicast": {
      "compression": { "enabled": true }
    },
    "multicast": {
      "compression": { "enabled": true }
    },
    "link": {
      "tls": {
        "root_ca_certificate": "/etc/zenohd/tls/client/minica.pem",
        "enable_mtls": true,
        "listen_private_key": "/etc/zenohd/tls/server/13.231.141.103/key.pem",
        "listen_certificate": "/etc/zenohd/tls/server/13.231.141.103/cert.pem"
      }
    }
  },
  "adminspace": {
    "permissions": {
      "read": true,
      "write": true
    }
  }
}
```

注意:

- `listen_private_key` と `listen_certificate` は router/server 側の証明書。
- `root_ca_certificate` は client 証明書を検証する CA。
- client 側の `remote/zenoh-user.json5` と `vehicle/zenoh.json5` には、
  `connect_private_key` と `connect_certificate` を設定する。
- IP アドレス直指定の場合、server 証明書の SAN に `IP:13.231.141.103` が必要。
- 後日 DNS に移行する場合、client endpoint を `tls/<ZENOH_ROUTER_DNS_NAME>:<port>` に変更し、
  server 証明書も DNS 名 SAN 入りで作り直す。

### 手順3-4. TLS router を手動起動して topic 疎通確認

systemd に差し替える前に、EC2 上で A2 の TLS router を手動起動して topic 疎通を確認する。
手順2で起動している TCP service は停止してから確認する。

EC2 側で実行する。

```bash
sudo systemctl stop zenoh-router-a2
sudo /usr/bin/zenohd -c /etc/zenoh/routers/router-a2.json5
```

PC 1 台で remote 側 bridge を起動する。

```bash
ROS_DOMAIN_ID=10 \
zenoh-bridge-ros2dds client \
  -e tls/13.231.141.103:7448 \
  -c remote/zenoh-user.json5
```

同じ PC の別ターミナルで vehicle 側 bridge を起動する。

```bash
ROS_DOMAIN_ID=11 \
zenoh-bridge-ros2dds client \
  -e tls/13.231.141.103:7448 \
  -c vehicle/zenoh.json5
```

remote 側 domain で `/racing_kart/joy` を publish する。

```bash
ROS_DOMAIN_ID=10 \
ros2 topic pub /racing_kart/joy sensor_msgs/msg/Joy \
  "{header: {frame_id: joy}, axes: [0.0, 0.0, 0.0, 0.0], buttons: [0, 0, 0, 0]}" -r 1
```

vehicle 側 domain で `/racing_kart/joy` を確認する。

```bash
ROS_DOMAIN_ID=11 ros2 topic echo /racing_kart/joy
```

期待:

- TLS/mTLS の証明書エラーが出ない
- vehicle 側 domain で `/racing_kart/joy` が echo できる

確認後、手動起動した `zenohd` は `Ctrl+C` で停止する。
手順4に進む場合、A2 は systemd の TLS config 起動に切り替える。

## 手順4. TLS + systemd 設定に差し替え

手順2で作成した A2 の TCP service を、TLS router config を使う設定に差し替える。
A3/A6/A7/A1/A5/A8 は、同じ命名規則で新規作成する。
今年は zenohd を apt でインストールしているため、実体は `/usr/bin/zenohd` にある。
systemd の `ExecStart` も `/usr/bin/zenohd` をそのまま使う。

| 車両 | service | config |
| --- | --- | --- |
| A2 | `zenoh-router-a2.service` | `/etc/zenoh/routers/router-a2.json5` |
| A3 | `zenoh-router-a3.service` | `/etc/zenoh/routers/router-a3.json5` |
| A6 | `zenoh-router-a6.service` | `/etc/zenoh/routers/router-a6.json5` |
| A7 | `zenoh-router-a7.service` | `/etc/zenoh/routers/router-a7.json5` |
| A1 | `zenoh-router-a1.service` | `/etc/zenoh/routers/router-a1.json5` |
| A5 | `zenoh-router-a5.service` | `/etc/zenoh/routers/router-a5.json5` |
| A8 | `zenoh-router-a8.service` | `/etc/zenoh/routers/router-a8.json5` |

A2 用 service は、手順2で作成済みの `/etc/systemd/system/zenoh-router-a2.service` を以下の内容に置き換える。
変更する箇所は `ExecStart` で、TCP listen 直指定から TLS router config 指定に変える。

変更前:

```ini
ExecStart=/usr/bin/zenohd --listen tcp/0.0.0.0:7448
```

変更後:

```ini
ExecStart=/usr/bin/zenohd -c /etc/zenoh/routers/router-a2.json5
```

```bash
sudo tee /etc/systemd/system/zenoh-router-a2.service >/dev/null <<'EOF'
[Unit]
Description=zenoh router A2
After=network-online.target
Wants=network-online.target

[Service]
User=root
ExecStart=/usr/bin/zenohd -c /etc/zenoh/routers/router-a2.json5
Restart=always
RestartSec=2s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
```

A3/A6/A7/A1/A5/A8 は、対応表どおりの service 名と config 名で新規作成する。

全 router を有効化:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now \
  zenoh-router-a2 \
  zenoh-router-a3 \
  zenoh-router-a6 \
  zenoh-router-a7 \
  zenoh-router-a1 \
  zenoh-router-a5 \
  zenoh-router-a8

sudo systemctl status zenoh-router-a2
```

ログ:

```bash
journalctl -u zenoh-router-a2 -f
```

### 手順4-1. TLS systemd 起動後の topic 疎通確認

手順4で TLS systemd に差し替えた後、PC 1 台で再度 topic 疎通を確認する。
手順3-4と同じ確認を、systemd 管理の `zenoh-router-a2` に対して実施する。
`driver` と `autoware` は起動しない。

remote 側 bridge を起動する。

```bash
ROS_DOMAIN_ID=10 \
zenoh-bridge-ros2dds client \
  -e tls/13.231.141.103:7448 \
  -c remote/zenoh-user.json5
```

vehicle 側 bridge を起動する。

```bash
ROS_DOMAIN_ID=11 \
zenoh-bridge-ros2dds client \
  -e tls/13.231.141.103:7448 \
  -c vehicle/zenoh.json5
```

remote 側 domain で `/racing_kart/joy` を publish する。

```bash
ROS_DOMAIN_ID=10 \
ros2 topic pub /racing_kart/joy sensor_msgs/msg/Joy \
  "{header: {frame_id: joy}, axes: [0.0, 0.0, 0.0, 0.0], buttons: [0, 0, 0, 0]}" -r 1
```

vehicle 側 domain で `/racing_kart/joy` を確認する。

```bash
ROS_DOMAIN_ID=11 ros2 topic echo /racing_kart/joy
```

EC2 側で `zenoh-router-a2` のログを確認する。

```bash
journalctl -u zenoh-router-a2 -f
```

期待:

- TLS/mTLS の証明書エラーが出ない
- vehicle 側 domain で `/racing_kart/joy` が echo できる

## 手順5. 車両側/遠隔側の接続先を正式反映

遠隔 PC 側:

```bash
remote/connect_zenoh.bash
```

確認用 TCP:

```bash
-e tcp/<EC2_PUBLIC_IP>:7448
```

TLS/mTLS:

```bash
-e tls/<ZENOH_ROUTER_DNS_NAME>:7448
```

### 手順5-1. A1/A5/A8 の case 追加

A1/A5/A8 を使う場合は、遠隔 PC 側と車両側の両方に車両 ID とポートの対応を追加する。

遠隔 PC 側は `remote/connect_zenoh.bash` の `case "$NAMESPACE" in` に追加する。

```bash
A1)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' - Port 7452"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/<ZENOH_ROUTER_DNS_NAME>:7452 \
        -c zenoh-user.json5
    ;;
A5)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' - Port 7453"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/<ZENOH_ROUTER_DNS_NAME>:7453 \
        -c zenoh-user.json5
    ;;
A8)
    echo "Connecting Zenoh. Target Vehicle: '$NAMESPACE' - Port 7454"
    RUST_BACKTRACE=1 zenoh-bridge-ros2dds client \
        -e tls/<ZENOH_ROUTER_DNS_NAME>:7454 \
        -c zenoh-user.json5
    ;;
```

使用法表示とエラーメッセージも更新する。

```bash
echo "使用法: $0 {A1|A2|A3|A5|A6|A7|A8|test-*}" >&2
echo "A1, A2, A3, A5, A6, A7, A8, test-* のいずれかを指定してください。" >&2
```

車両側:

```bash
docker-compose.yml
```

確認用 TCP:

```bash
-e tcp/<EC2_PUBLIC_IP>:$$PORT
```

TLS/mTLS:

```bash
-e tls/<ZENOH_ROUTER_DNS_NAME>:$$PORT
```

車両側は `docker-compose.yml` の `zenoh` service 内で `VEHICLE_ID` から `PORT` を決めているため、以下のように `case ${VEHICLE_ID:-} in` に追加する。

```bash
case ${VEHICLE_ID:-} in
  A2) PORT=7448;;
  A3) PORT=7449;;
  A6) PORT=7450;;
  A7) PORT=7451;;
  A1) PORT=7452;;
  A5) PORT=7453;;
  A8) PORT=7454;;
  *) echo 'Invalid VEHICLE_ID'; exit 1;;
esac
```

起動コマンド:

```bash
VEHICLE_ID=A1 docker compose up -d zenoh
VEHICLE_ID=A5 docker compose up -d zenoh
VEHICLE_ID=A8 docker compose up -d zenoh
```

### 手順5-2. 正式反映後の実運用相当確認

遠隔 PC 側と車両側 PC の 2 台構成で確認する。

車両側 PC で起動する。

```bash
VEHICLE_ID=A2 make autoware-driver-zenoh
```

遠隔 PC 側で Zenoh bridge を起動する。

```bash
cd remote
./connect_zenoh.bash A2
```

遠隔 PC 側で RViz を起動する。

```bash
make rviz2
```

遠隔 PC 側で joy node を起動する。

```bash
cd remote
./joy.bash
```

joy コントローラが手元にない場合は、遠隔 PC 側でダミーの `/racing_kart/joy` を publish する。

```bash
ros2 topic pub /racing_kart/joy sensor_msgs/msg/Joy \
  "{header: {frame_id: joy}, axes: [0.0, 0.0, 0.0, 0.0], buttons: [0, 0, 0, 0]}" -r 1
```

車両側 PC で `/racing_kart/joy` を確認する。

```bash
ROS_DOMAIN_ID=1 ros2 topic echo /racing_kart/joy
ROS_DOMAIN_ID=1 ros2 topic info -v /racing_kart/joy
```

`topic info` で `racing_kart_driver` が subscriber として表示されることを確認する。

車両側 PC で zenoh container のログを確認する。

```bash
docker compose logs --tail=100 zenoh
```

EC2 側で router のログを確認する。

```bash
journalctl -u zenoh-router-a2 -f
```

期待:

- 遠隔 PC 側の Zenoh bridge が TLS endpoint `tls/13.231.141.103:7448` に接続できる
- 車両側の Zenoh bridge が TLS endpoint `tls/13.231.141.103:7448` に接続できる
- 車両側 PC で `/racing_kart/joy` が echo できる
- `racing_kart_driver` が `/racing_kart/joy` の subscriber として表示される

## AWS Security Group

確認段階:

- TCP 7448 from 操作 PC の Global IP
- 車両側から接続する場合は、車両側ネットワークの Global IP も追加

複数車両:

- TCP 7448
- TCP 7449
- TCP 7450
- TCP 7451
- TCP 7452
- TCP 7453
- TCP 7454

一時的に `0.0.0.0/0` に開ける場合は、動作確認後に必ず接続元 IP を絞る。

2026-05-18 時点では、A1/A5/A8 車両分として 7452/7453/7454 を追加で許可。
確認時の接続元は `153.227.191.4/32`。

管理用 SSH 22 は開けず、SSM 経由で接続する方針。

## メモ

2026-05 時点の検証では、EC2 `aichallenge-zenoh-router` に対して
`nc -vz 13.231.141.103 7448` の TCP 到達性は確認済み。

SSM 経由 SSH も設定済み:

```bash
ssh aichallenge-zenoh-router
```
