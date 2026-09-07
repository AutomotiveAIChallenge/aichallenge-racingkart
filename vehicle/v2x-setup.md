# AI Challenge - V2X 位置情報共有の実車導入とテスト

V2X 対応の `racing_kart_interface` を実車 ECU に載せ、TLS 証明書を配布し、
`docker compose` の `driver` サービスから起動して、実車でテストするまでの手順。

前提となる文書：

| 文書                                                                | 内容                                                    |
| ------------------------------------------------------------------- | ------------------------------------------------------- |
| [`ecu-setup.md`](./ecu-setup.md)                                    | ECU 自体の初期構築（OS / udev / ネットワーク / ROS 2）  |
| [`README.md`](./README.md)                                          | 構築済み ECU で走らせる手順（`.env` / IMU バイアス）    |
| [`setup_check.md`](./setup_check.md)                                | `setup_check.sh` の各項目と走行前チェックリスト         |
| [`../remote/README.md`](../remote/README.md)                        | 遠隔操作（zenoh / joy / RViz）                          |
| `racing_kart_interface/docs/v2x-position-sharing-testing.md`        | 机上（AWSIM・2 コンテナ）での段階テスト                 |
| `racing_kart_interface/src/v2x_position_sharing/docs/usage.md`      | launch 引数・環境変数の一覧                             |
| `aichallenge-aws/cloudformation_templates/v2x-mqtt-broker/README.md` | broker・CA・証明書の発行と失効                          |
| `aichallenge-v2x/docs/SPECIFICATION.md`                             | 仕様（本書の `R…` はこの要求番号）                      |

---

# 第0部 何が変わるのか

## 0-1. データの流れ

```text
実車 ECU (ROS_DOMAIN_ID=1)
┌─────────────────────────────────────────────┐
│ driver コンテナ (racing_kart_interface)      │
│   ublox_gps ──► /sensing/gnss/nav_sat_fix   │
│        └► v2x_gnss_poser                    │
│             └► /v2x/gnss/pose_with_covariance│
│                  └► v2x_position_sharing    │  JSON 化 (§7.1)
│                       └► v2x_communicator ──┼──► MQTTS 8883
│                  ◄────────────────────────  │  ◄── MQTTS 8883
│   /v2x/vehicle_positions (10 Hz)            │
└──────────────────┬──────────────────────────┘
                   │ 同一 ROS ドメイン
┌──────────────────▼──────────────────────────┐         ┌──────────────────┐
│ autoware コンテナ                            │         │ Mosquitto on EC2 │
│   v2x_marker_publisher（RViz 表示）          │         │ v2x-mqtt.dev.… │
│   multi_purpose_mpc_ros（障害物回避、既定 off）│        └──────────────────┘
└─────────────────────────────────────────────┘
```

カートは `v2x/vehicles/{自号 ID}/position` へ publish し、`v2x/vehicles/+/position`
を subscribe する。broker の fan-out がそのまま中継になる（R6.4.1）。
`vehicle_id` は **MQTT トピック名だけ**で伝達され、payload には入らない（R4.2）。

## 0-2. 机上テスト手順がそのまま使えない理由

`racing_kart_interface/docs/v2x-position-sharing-testing.md` は
`docker/docker_run.sh`（rocker）でコンテナを起動する前提だが、**実車 ECU では
`racing_kart_interface` は rocker では起動しない**。起動経路は次のとおり。

```text
make autoware-driver-zenoh-rosbag
  └► docker compose up -d driver
       image: ghcr.io/tier4/racing_kart_interface:latest-experiment
       command: /vehicle/run_driver.bash vehicle ${ROS_DOMAIN_ID} ${LOG_DIR}
         └► /entrypoint.sh vehicle
              └► /workspace/utils/run.bash vehicle
                   └► ros2 launch racing_kart_launch racing_kart_vehicle.launch.xml
                        can:=can0 usb:=/dev/vcu/usb bench:=false
                        （use_v2x は既定 true）
```

したがって実車導入で必要な作業は次の 3 点に集約される。

| # | 作業                                                                     | 節    |
| - | ------------------------------------------------------------------------ | ----- |
| 1 | V2X パッケージを含むイメージを `:latest-experiment` タグで ECU に入れる  | 1-1   |
| 2 | 証明書を ECU の `/etc/v2x/tls` に置き、`driver` サービスへマウントする    | 2-1、2-2 |
| 3 | `V2X_*` 環境変数を `driver` サービスへ**明示的に**渡す                    | 2-2、2-3 |

3 が落とし穴になる。`docker-compose.yml` の `x-racing_kart_interface-base` は
`environment:` を列挙型で書いているため、**`.env` に `V2X_*` を書いただけでは
コンテナに渡らない**。列挙に足すまでは、カートは既定値（broker `127.0.0.1:1883`、
平文、ID `d1`）で起動して延々と再接続を繰り返す。

---

# 第1部 事前準備（オフィス作業）

## 1-1. イメージのビルドとタグ付け

`racing_kart_interface` の V2X 対応ブランチ（本書執筆時点で
`feat/add-v2x-position-sharing`）をチェックアウトしてビルドする。

```bash
cd racing_kart_interface
git switch feat/add-v2x-position-sharing
./utils/initialize_workspace.bash     # depends.repos の取得（初回のみ）
cd docker && ./docker_build.sh        # racing_kart_interface:latest を生成
```

Dockerfile は `--packages-up-to racing_kart_launch` でビルドする。
`racing_kart_launch` が `v2x_position_sharing` を実行時依存として宣言しているので、
`v2x_position_sharing` と vendoring された `src/v2x_communicator` も自動で入る。
入ったことを確認する。

```bash
docker run --rm --entrypoint ls racing_kart_interface:latest /workspace/install | grep v2x
# v2x_communicator_node / v2x_connector_core / v2x_connector_manager
# v2x_connector_std / v2x_msgs / v2x_position_sharing
```

**ECU が参照するタグは `ghcr.io/tier4/racing_kart_interface:latest-experiment`**
（`docker-compose.yml` の `x-racing_kart_interface-base`）なので、付け替えて
エクスポートする。

```bash
docker tag racing_kart_interface:latest ghcr.io/tier4/racing_kart_interface:latest-experiment
docker save ghcr.io/tier4/racing_kart_interface:latest-experiment \
  | gzip > racing_kart_interface_latest-experiment.tar.gz
sha256sum racing_kart_interface_latest-experiment.tar.gz \
  > racing_kart_interface_latest-experiment.tar.gz.sha256
```

搬入と `docker load` の手順は [`ecu-setup.md`](./ecu-setup.md) 2-2 と同じ。
ECU 上では**入れ替え前に旧イメージを退避**しておくと切り戻しが速い
（第6部「ロールバック」）。

```bash
# ECU 上（新イメージを load する前に）
docker tag ghcr.io/tier4/racing_kart_interface:latest-experiment \
           ghcr.io/tier4/racing_kart_interface:pre-v2x
```

## 1-2. カート証明書の発行

broker はクライアント証明書だけを資格情報として使う。パスワードは無い。
証明書の CN がそのまま MQTT ユーザ名になり（`use_identity_as_username true`）、
ACL `pattern write v2x/vehicles/%u/position` により **自分の ID のトピックにしか
publish できない**（strict モード）。

```bash
cd aichallenge-aws/cloudformation_templates/v2x-mqtt-broker
./issue-kart-cert.sh --envtype dev --vehicle-id d1
./issue-kart-cert.sh --envtype dev --vehicle-id d2
# 走行に参加する台数ぶん発行する（上限なし）
```

発行物は `./kart-certs/<envtype>/<id>/` に `ca.crt` / `kart.crt` / `kart.key` /
`env` の 4 ファイル。`env` は `docker/.env` 形式で、コンテナ内パスを指している。

```bash
V2X_VEHICLE_ID=d1
V2X_BROKER_HOST=v2x-mqtt.dev.aichallenge-board.jsae.or.jp
V2X_BROKER_PORT=8883
V2X_MQTT_TLS_CA_FILE=/etc/v2x/tls/ca.crt
V2X_MQTT_TLS_CERT_FILE=/etc/v2x/tls/kart.crt
V2X_MQTT_TLS_KEY_FILE=/etc/v2x/tls/kart.key
```

`kart.key` は秘密鍵である。**git に入れない。チャットに貼らない。** 受け渡しは
USB / SSD の手渡しか、鍵を扱ってよい経路に限る。有効期限は既定 397 日。

`--envtype prd` は本番 broker の CA・ホスト名になる。dev の証明書で prd broker には
繋がらない（CA が別）。走行に使う環境を先に決めてから発行する。

## 1-3. ID の対応表を作る

このシステムには**名前空間の違う ID が 3 つ**あり、混同が最も多い故障原因になる。

| ID                | 値の例         | 決まる場所                       | 用途                                        |
| ----------------- | -------------- | -------------------------------- | ------------------------------------------- |
| `VEHICLE_ID`      | `A1`,`A3`,`A6` | `aichallenge-racingkart/.env`   | 号機番号。**zenoh の接続先ポート**を決める  |
| `V2X_VEHICLE_ID`  | `d1`..`d4`     | 同 `.env`（本書で追加）          | V2X の自号 ID。**証明書の CN と一致必須**   |
| `ROS_DOMAIN_ID`   | `1`            | 同 `.env`                        | 実車では既定 `1` のまま                     |

走行前に、参加する全カートについて次のような表を埋めて共有する。

| 号機 | `VEHICLE_ID` | `V2X_VEHICLE_ID` | 証明書 CN | ECU ホスト名 |
| ---- | ------------ | ---------------- | --------- | ------------ |
| 1号車 | A1          | d1               | d1        | ECU-RK-01    |
| 2号車 | A3          | d2               | d2        | ECU-RK-03    |

`V2X_VEHICLE_IDS`（走行に参加しうる ID の全リスト）は**全カートで同じ値**にする。
ここに入っていない ID からの位置情報は受信ルートが生成されず、届いても捨てられる。

## 1-4. 机上テストを先に通す

実車に持ち込む前に、`racing_kart_interface/docs/v2x-position-sharing-testing.md` の
Stage 0〜3（broker 疎通・コンテナ 1 台・コンテナ 2 台）をオフィスで通しておく。
実車で初めて TLS を試すと、故障の切り分け対象が「証明書・回線・ECU・車両」の
4 つに増えて手に負えなくなる。

---

# 第2部 ECU 側の設定

以降はすべて対象 ECU 上での作業。`sudo` が必要な箇所を明示している。

## 2-1. 証明書の配置

```bash
sudo mkdir -p /etc/v2x/tls
sudo cp ca.crt kart.crt kart.key /etc/v2x/tls/
sudo chown -R root:root /etc/v2x/tls
sudo chmod 700 /etc/v2x/tls
sudo chmod 600 /etc/v2x/tls/kart.key
sudo chmod 644 /etc/v2x/tls/ca.crt /etc/v2x/tls/kart.crt
```

`driver` サービスは `user: "root"` で動く（`docker-compose.yml` に明示されている。
CAN / DDS のホスト側設定のため）。したがって `root:root 0600` の秘密鍵をそのまま
読める。

| 起動方法                              | コンテナ内 UID       | 鍵の所有者・権限        |
| ------------------------------------- | -------------------- | ----------------------- |
| `docker compose up -d driver`（実車） | root                 | `root:root 0600` でよい |
| `docker/docker_run.sh`（rocker、机上） | ホストの UID         | 自ユーザ所有にする必要あり |

机上検証を rocker で行う場合は、リポジトリ外の `~/v2x-tls/<id>/` に置いて
自分の UID で `600` にする（机上テスト手順書の P2 参照）。

配置した証明書が想定どおりか確認する。

```bash
sudo openssl x509 -in /etc/v2x/tls/kart.crt -noout -subject -dates
# subject=CN = d1        ← V2X_VEHICLE_ID と一致すること
# notAfter=...           ← 走行日より先であること
sudo openssl verify -CAfile /etc/v2x/tls/ca.crt /etc/v2x/tls/kart.crt
# kart.crt: OK
```

## 2-2. `docker-compose.yml` の変更

`x-racing_kart_interface-base` に環境変数とマウントを追加する。**この変更が無いと
`.env` に何を書いても効かない。**

```diff
     - NTRIP_USERNAME=${NTRIP_USERNAME:-}
     - NTRIP_PASSWORD=${NTRIP_PASSWORD:-}
+    # V2X position sharing. Passed through to racing_kart_vehicle.launch.xml.
+    # V2X_VEHICLE_ID defaults to d1 (the launch file's own default) so that a
+    # missing value can never abort the driver launch; see the note below.
+    - V2X_VEHICLE_ID=${V2X_VEHICLE_ID:-d1}
+    - V2X_VEHICLE_IDS=${V2X_VEHICLE_IDS:-d1,d2,d3,d4}
+    - V2X_BROKER_HOST=${V2X_BROKER_HOST:-127.0.0.1}
+    - V2X_BROKER_PORT=${V2X_BROKER_PORT:-1883}
+    - V2X_MQTT_TLS_CA_FILE=${V2X_MQTT_TLS_CA_FILE:-}
+    - V2X_MQTT_TLS_CERT_FILE=${V2X_MQTT_TLS_CERT_FILE:-}
+    - V2X_MQTT_TLS_KEY_FILE=${V2X_MQTT_TLS_KEY_FILE:-}
   volumes:
     - ./output:${OUTPUT_ROOT:-/output}
     - ./vehicle:/vehicle:ro
     - ./aichallenge/utils:/aichallenge/utils:ro
+    - ${V2X_TLS_DIR:-/etc/v2x/tls}:/etc/v2x/tls:ro
     - /dev/vcu:/dev/vcu
     - /dev/gnss:/dev/gnss
```

さらに、現地で V2X だけを切れるようにするため `driver` サービスの `command` に
追加引数の口を空けておく。

```diff
   driver:
     <<: *racing_kart_interface-base
     user: "root"
     entrypoint: []
-    command: ["bash", "-lc", "exec /vehicle/run_driver.bash vehicle ${ROS_DOMAIN_ID:-1} ${LOG_DIR:-}"]
+    command: ["bash", "-lc", "exec /vehicle/run_driver.bash vehicle ${ROS_DOMAIN_ID:-1} ${LOG_DIR:-} ${DRIVER_LAUNCH_ARGS:-}"]
```

`run_driver.bash` は第 4 引数以降をそのまま `entrypoint.sh` → `utils/run.bash` →
`ros2 launch` へ渡すので、`.env` に `DRIVER_LAUNCH_ARGS=use_v2x:=false` と書けば
V2X スタックだけを外して従来どおり走れる。**現地での退避手段として必ず入れておく。**

### 空の `V2X_VEHICLE_ID` は driver を落とす

`v2x_kart_position.launch.py` は `vehicle_id` が空文字だと
`RuntimeError: vehicle_id is required` を送出する。これは launch description の
構築時に起きるため、**`racing_kart_vehicle.launch.xml` 全体が起動に失敗し、
モータドライバも VCU も GNSS も上がらない**。上の diff で
`${V2X_VEHICLE_ID:-d1}` としているのはこのため（launch 側の既定値と揃えてある）。

代償として、`.env` への記入漏れは「エラー」ではなく「全車が d1 として振る舞う」
という静かな誤りになる。第3部 Step 0 のチェックで必ず拾うこと。

同様に、`V2X_MQTT_TLS_{CA,CERT,KEY}_FILE` は**3 つ揃えるか 3 つとも空**にする。
1 つでも欠けると `must be given together` で同じく launch 全体が落ちる。

## 2-3. `.env` の追記

`aichallenge-racingkart/.env`（`racing_kart_interface/docker/.env` ではない）に、
証明書に同梱された `env` の内容を追記する。`.env` は git 管理外。

```bash
cat >> ~/aichallenge-racingkart/.env <<'EOF'

# --- V2X position sharing ---
V2X_VEHICLE_ID=d1
V2X_VEHICLE_IDS=d1,d2
V2X_BROKER_HOST=v2x-mqtt.dev.aichallenge-board.jsae.or.jp
V2X_BROKER_PORT=8883
V2X_MQTT_TLS_CA_FILE=/etc/v2x/tls/ca.crt
V2X_MQTT_TLS_CERT_FILE=/etc/v2x/tls/kart.crt
V2X_MQTT_TLS_KEY_FILE=/etc/v2x/tls/kart.key
EOF
```

`V2X_MQTT_USERNAME` / `V2X_MQTT_PASSWORD` は**設定しない**。identity は証明書で
決まり、クライアントが送るユーザ名は broker に無視される。設定すると connector の
設定ファイルに書き込まれて送信され、どの資格情報で認証されているのかが
分からなくなる。

反映を確認する。

```bash
cd ~/aichallenge-racingkart
docker compose config | grep -A2 -E 'V2X_|/etc/v2x'
```

## 2-4. ホスト側ツールと前提条件

```bash
sudo apt install -y mosquitto-clients   # イメージには入っていない
timedatectl                             # NTP synchronized: yes（TLS 検証は時刻に依存）
getent hosts v2x-mqtt.dev.aichallenge-board.jsae.or.jp   # 名前解決できること
```

`ecu-setup.md` の手順で `ufw` は無効化済みだが、カートが繋がるモバイル回線側で
**外向き 8883/tcp** が通ることは別途確認が必要。

## 2-5.（任意）遠隔 RViz で他車を見る

`vehicle/zenoh.json5` の `allow.publishers` は現状 V2X トピックを含まないため、
**遠隔 PC の RViz には他車が表示されない**。表示したい場合は追加する。

```diff
       allow: {
         publishers: [
           "/control/command/control_cmd",
           "/localization/kinematic_state",
           "/planning/scenario_planning/trajectory",
+          "/v2x/vehicle_positions/markers",
           "/tf",
```

`/v2x/vehicle_positions/markers` は autoware コンテナの `v2x_marker_publisher`
（`domain_id != 0` のとき起動）が出す `MarkerArray`。マーカを送らず
`/v2x/vehicle_positions` だけを転送しても、遠隔側にマーカ生成ノードが居ないので
RViz には出ない。回線帯域と相談して選ぶこと（10 Hz × 数台ぶんのマーカ）。

---

# 第3部 実車テスト

段ごとに 1 層ずつ足す。失敗した段が、壊れている層を指す。
**Step 0〜3 は車両を動かさない。** Step 4 以降は遠隔操作の緊急停止を必ず確保する
（[`../remote/README.md`](../remote/README.md) 第3部）。

| Step | 何を確かめるか                                     | 車両 | 台数 |
| ---- | -------------------------------------------------- | ---- | ---- |
| 0    | 設定・証明書・時刻・回線                           | 停止 | 1    |
| 1    | ECU から broker へ TLS で到達できる                | 停止 | 1    |
| 2    | driver コンテナ内の V2X ノードが接続し 10 Hz 出す | 停止 | 1    |
| 3    | 実 GNSS の自車位置が broker に届く                 | 停止 | 1    |
| 4    | 2 台が静止状態で相互受信し、位置が実測と合う       | 停止 | 2    |
| 5    | 走行中も追随する                                   | 走行 | 2    |
| 6    | 障害（回線断・RTK 断・相手停止）で安全側に落ちる   | 走行 | 2    |
| 7    | フルスタック（MPC 障害物回避・遠隔可視化）         | 走行 | 2    |

## Step 0 — 設定の突き合わせ（車両停止）

```bash
cd ~/aichallenge-racingkart
grep -E '^(VEHICLE_ID|ROS_DOMAIN_ID|V2X_)' .env
sudo openssl x509 -in /etc/v2x/tls/kart.crt -noout -subject -dates
docker image inspect ghcr.io/tier4/racing_kart_interface:latest-experiment \
  --format '{{.Id}} {{.Created}}'
docker run --rm --entrypoint ls ghcr.io/tier4/racing_kart_interface:latest-experiment \
  /workspace/install | grep v2x_position_sharing
timedatectl | grep -i synchronized
```

**合格条件**

| 項目                                          | 期待                                       |
| --------------------------------------------- | ------------------------------------------ |
| `V2X_VEHICLE_ID`                              | 1-3 の表どおり。証明書の CN と一致          |
| `V2X_VEHICLE_IDS`                             | 参加全台ぶん。全カートで同一                |
| `V2X_BROKER_HOST` / `_PORT`                   | 発行時の `env` と同じ。ポートは 8883        |
| TLS 3 変数                                    | 3 つとも設定済み                            |
| `kart.crt` の `notAfter`                      | 走行日より先                                |
| イメージ                                      | `v2x_position_sharing` を含む               |
| NTP                                           | `System clock synchronized: yes`            |

## Step 1 — ECU ホストから broker へ疎通（車両停止）

ROS はまだ関係ない。「AWS・回線・証明書の問題」と「こちらのスタックの問題」を
先に切り分ける。10 秒で終わる。

```bash
cd /tmp
sudo mosquitto_sub -h "$(grep -oP '(?<=^V2X_BROKER_HOST=).*' ~/aichallenge-racingkart/.env)" \
  -p 8883 -t 'v2x/vehicles/+/position' -v -d -W 8 \
  --cafile /etc/v2x/tls/ca.crt --cert /etc/v2x/tls/kart.crt --key /etc/v2x/tls/kart.key
```

**合格**

```text
Client (null) sending CONNECT
Client (null) received CONNACK (0)
Client (null) sending SUBSCRIBE (Mid: 1, Topic: v2x/vehicles/+/position, QoS: 0, ...)
Client (null) received SUBACK
Subscribed (mid: 1): 0
```

`CONNACK (0)` は証明書が受理されたこと、`Subscribed … : 0` は ACL が読みを
許可したこと。誰も publish していなければメッセージは出ず、`-W 8` の
タイムアウトで終わるのが正しい。

| 症状                                                | 原因                                                                   |
| --------------------------------------------------- | ---------------------------------------------------------------------- |
| TLS handshake failure / `certificate verify failed` | `ca.crt` 不一致、または DNS 名でなく IP で接続している                 |
| `Connection refused` / TCP タイムアウト             | ポート違い（8883。1883 ではない）、broker 停止、回線側で塞がれている   |
| `CONNACK (5)` not authorised                        | 証明書が失効済み（CRL）、または別 CA で発行された証明書                |
| `Subscribed … : 128`                                | ACL が subscribe を拒否                                                 |
| 名前解決できない                                    | モバイル回線の DNS。`getent hosts` で再確認                            |

broker 側の実像が要るときは（要 AWS 権限、ECU 以外の PC から）:

```bash
aws logs tail /aws/ec2/ai-challenge-dev-v2x-mqtt-broker --follow \
  --profile ai-challenge-dev --region ap-northeast-1
```

## Step 2 — driver コンテナで V2X ノードが上がる（車両停止）

CAN・VCU・GNSS を接続し、いつもどおり起動する。まだ autoware は要らない。

```bash
cd ~/aichallenge-racingkart
docker compose up -d driver
```

`driver` は標準出力をログファイルへ落とすので、`docker compose logs` ではなく
出力ディレクトリを見る。

```bash
tail -f output/*/d1/driver.log
```

**合格 — この順で出ること**

```text
[v2x_communicator]: TLS enabled: ca=/etc/v2x/tls/ca.crt, cert=…/kart.crt, key=…/kart.key
[v2x_communicator]: MQTT connect target: host=v2x-mqtt.dev…, port=8883, client_id=kart_d1
[v2x_communicator]: Connected to MQTT broker successfully
[v2x_communicator]: Subscribed to topic: v2x/vehicles/+/position
[v2x_position_sharing]: Sharing V2X positions: vehicle_id=d1 frame_id=map rate=10.0 Hz …
```

ホスト側の ROS 2 から確認する（`ecu-setup.md` 第5部でホストにも ROS 2 が入っている）。

```bash
export ROS_DOMAIN_ID=1
ros2 node list | grep v2x            # /v2x_communicator, /v2x_position_sharing, /v2x_gnss_poser
ros2 topic hz /v2x/vehicle_positions # 10 Hz
ros2 topic echo --once /v2x/vehicle_positions   # 自車しか居なければ vehicles: []（R5.2.2）
ros2 topic echo --once /comm_status  # statuses[0].connection_state: 3 (Connected)
```

`connection_state` は `0 Closed / 1 Opening / 2 Connecting / 3 Connected /
4 RetryWaiting / 5 Closing / 6 Error`。

**同時に、V2X を足したことで既存機能が壊れていないことを見る。**

```bash
cd vehicle && ./setup_check.sh --phase runtime
```

| 症状                                                        | 原因                                                                     |
| ----------------------------------------------------------- | ------------------------------------------------------------------------ |
| driver コンテナが即終了、`driver.log` に `vehicle_id is required` | `V2X_VEHICLE_ID` が空。2-2 の `:-d1` が入っていない                   |
| `must be given together` で起動失敗                          | TLS 3 変数のうち一部だけ設定されている                                   |
| `TLS file not found: /etc/v2x/tls/…`                         | 2-2 のマウント行が入っていない、または置き場所が違う                     |
| `TLS config incomplete, TLS not applied` → 平文で失敗        | 同上（環境変数が渡っていない）                                           |
| `Connection attempt timed out after 5 sec. Will retry…` の繰り返し | ホスト名/ポート違い、または CONNACK 前に証明書が拒否された          |
| V2X ノードが 1 つも居ない                                    | 古いイメージ、または `DRIVER_LAUNCH_ARGS=use_v2x:=false` が残っている     |

**確認しておくべき安全側の性質**：broker に繋がらなくても driver は落ちない。
`v2x_communicator` が再接続を繰り返すだけで、モータ・VCU・GNSS は通常どおり動く。
ここで一度、`.env` の `V2X_BROKER_HOST` をわざと存在しない名前にして起動し、
**車両が普通に動くこと**を確認しておくと現地で慌てずに済む。

## Step 3 — 実 GNSS の自車位置が broker に届く（車両停止）

RTK Fix を確保してから行う。

```bash
export ROS_DOMAIN_ID=1
ros2 topic echo --once /sensing/gnss/navpvt | grep -i fix   # RTK Fixed
ros2 topic echo --once /v2x/gnss/gnss_fixed                 # data: true
ros2 topic hz /v2x/gnss/pose_with_covariance                # 受信レート（~20 Hz）
```

別端末で broker を覗く。

```bash
sudo mosquitto_sub -h <broker-host> -p 8883 -t 'v2x/vehicles/+/position' -v \
  --cafile /etc/v2x/tls/ca.crt --cert /etc/v2x/tls/kart.crt --key /etc/v2x/tls/kart.key
```

**合格**

```text
v2x/vehicles/d1/position {"covariance":{"x":0.02,"y":0.02,"z":0.03},"frame_id":"map",
                          "position":{"x":3833.20,"y":73770.76,"z":0.0},
                          "stamp":"2026-08-17T04:12:33.250Z"}
```

確かめるのは 4 点。

1. トピック末尾が `/d1` である。**ID が現れるのはここだけ**（R4.2）。
2. payload に `vehicle_id` フィールドが**無い**（R6.2.1）。
3. `covariance` は分散ではなく**標準偏差 [m]**（R10.2.1）。RTK Fix なら数 cm。
4. `position` が地図座標として妥当（MGRS のローカル座標。柏の葉なら
   x≈3.8e3、y≈7.4e4 前後。City Circuit なら x≈9.0e4、y≈4.3e4 前後）。
   桁が全く違うなら座標系がずれている。
5. `stamp` が現在の UTC 時刻。1970 年なら GNSS 時刻が乗っていない。

ROS 側のバイト列も確認する：`ros2 topic hz /v2x/send/vehicle_position`。

### ACL が ID を実際に縛っていること

自号 `d1` の証明書で `d2` を騙ってみる。

```bash
sudo mosquitto_pub -h <broker-host> -p 8883 \
  -t v2x/vehicles/d2/position -m '{"frame_id":"map","position":{"x":0,"y":0,"z":0}}' \
  --cafile /etc/v2x/tls/ca.crt --cert /etc/v2x/tls/kart.crt --key /etc/v2x/tls/kart.key
```

**合格：`mosquitto_pub` は 0 で終了し、subscriber には何も出ない。** strict モードの
ACL 拒否は仕様上サイレント。ここでメッセージが届くなら broker が
`--acl-mode open` で構築されており、ID 偽装を防げていない。

## Step 4 — 2 台が静止状態で相互受信（車両停止）

2 台を**互いの距離を実測できる位置**（例：10.0 m 離して駐車）に置く。
それぞれの ECU で Step 2・3 まで通っていることが前提。

各 ECU で：

```bash
export ROS_DOMAIN_ID=1
ros2 topic echo --once /v2x/vehicle_positions
```

**合格**

| 確認                                                             | 根拠      |
| ---------------------------------------------------------------- | --------- |
| 要素がちょうど 1 つ、`vehicle_id` は相手の ID                    | R5.2.4    |
| **自号は絶対に現れない**（broker は自分の publish も返してくる） | R5.2.4    |
| 配列は 10 Hz（相手の送信レートに依らない）                       | R5.2.1    |
| 配列の `header.stamp` は publish 時刻、要素の `header.stamp` は相手の観測時刻 | R10.1.1 |
| `frame_id` は両車とも `map`                                      | R9.1、R9.2 |

**位置の妥当性検証**（ここが実車ならではの最重要項目）。1号車が受信した
2号車の位置と、2号車が自分で出している位置を突き合わせる。

```bash
# 2号車の ECU 上
ros2 topic echo --once /v2x/gnss/pose_with_covariance
# 1号車の ECU 上
ros2 topic echo --once /v2x/vehicle_positions
```

両者の `position.x/y` が RTK の精度（数 cm〜数十 cm）で一致すること。さらに
2 台の座標差の距離が**巻尺で測った実距離と一致**すること。

```bash
# 例：(x1,y1) と (x2,y2) から
python3 -c 'import math;print(math.dist((3833.20,73770.76),(3841.05,73764.55)))'
```

| 症状                                                    | 原因                                                                       |
| ------------------------------------------------------- | -------------------------------------------------------------------------- |
| バイト列は来るが配列が空                                | payload が捨てられている。`driver.log` の `Dropped a malformed payload` / `frame_id … does not match` を見る |
| バイト列すら来ない（broker には相手が見えている）       | 相手の ID が `V2X_VEHICLE_IDS` に無く受信ルートが生成されていない            |
| 一定のオフセットが乗る                                  | 一方が別の座標系（`coordinate_system` は全車 `1`=MGRS）。地図/トラックの取り違え |
| 0.2〜0.3 m ほどずれる                                   | `base_link`↔`gnss_link` の TF。autoware コンテナ未起動時はアンテナ位置になる |
| 両方が接続と切断を交互に繰り返す                        | 2 台が同じ `V2X_VEHICLE_ID`（= 同じ MQTT client_id）を使っている            |

## Step 5 — 走行中の追随（低速）

遠隔操作の緊急停止を確保し、`v_max` を低く抑えた状態で 1 台ずつ動かす。
**この段階では MPC の V2X 障害物回避は使わない**（既定 `use_obstacle_avoidance` は
`false` なので、V2X は制御に一切影響しない。観測専用）。

1. 1号車を手動走行、2号車は停止。1号車の位置が 2号車側で連続的に動くこと。
2. 逆にする。
3. 両方を低速走行させ、双方向で追随すること。

観測項目：

```bash
ros2 topic hz /v2x/vehicle_positions            # 10 Hz を維持
ros2 topic hz /v2x/received/vehicle_position/d2 # 相手の送信レート
ros2 topic echo --once /comm_status             # 再接続が起きていないか
```

**遅延の見方**：東京の broker を往復して数十 ms 程度。厳密な計測は、ホスト側の
`mosquitto_sub` の到着時刻と受信側 `/v2x/received/vehicle_position/<id>` を
同時に見るのが正直なやり方。両車の `header.stamp` 同士の比較は、両方が GNSS の
UTC 時刻を載せている実車でのみ意味を持つ（AWSIM の sim time では成立しない）。

**位置が飛ぶ・止まる場合**、まず自車側の RTK 状態（Step 3）を疑う。V2X は
相手の GNSS 精度をそのまま運ぶだけで、何も補正しない。

## Step 6 — 障害時の振る舞い（走行、緊急停止を確保）

| # | 起こすこと                                     | 期待される振る舞い                                                                 |
| - | ---------------------------------------------- | ---------------------------------------------------------------------------------- |
| 1 | 相手カートの電源を落とす / driver を止める      | `/v2x/vehicle_positions` は 10 Hz を維持し、相手の**最後の位置を保持し続ける**（R7.2.3。`stale_timeout_sec` 既定 0） |
| 2 | 自車のモバイル回線を抜く                        | `Disconnected unexpectedly … Will reconnect.` をログに出し、10 Hz は維持。**driver は落ちない** |
| 3 | 回線を戻す                                      | 自動で再接続し、受信が再開する                                                     |
| 4 | 自車の RTK を Float / None に落とす（アンテナ遮蔽） | `covariance` が大きくなったまま送信は継続する。受信側は値をそのまま受け取る       |
| 5 | 相手が `V2X_VEHICLE_IDS` に無い ID で送信       | 何も現れない（ルート未生成）。ID リストの不一致がこの形で出ることを体感しておく    |

1 は**重要な注意点**：既定では古い位置が消えない。相手が居なくなったのか
止まっているのかは `/v2x/vehicle_positions` からは区別できない。
消えてほしい運用なら `stale_timeout_sec` を設定するが、これは相手の観測時刻を
自車の時計と比較するため、**全車の時刻同期（NTP / GNSS 時刻）が前提**になる。

2 は「V2X の障害が車両の安全に波及しない」ことの確認であり、
**Step 7 に進む前に必ず通しておく**。

## Step 7 — フルスタックと遠隔可視化

いつもの起動手順に戻す。

```bash
cd ~/aichallenge-racingkart
make autoware-driver-zenoh-rosbag
```

### 7-1. GNSS poser の二重起動について

autoware コンテナ（`aichallenge_submit_launch/reference.launch.xml`）は
`/sensing/gnss` 名前空間で `racing_kart_gnss_poser` を起動する。driver コンテナの
V2X スタックは**別インスタンス**を `v2x_gnss_poser` という名前で、
`gnss_base_frame=v2x_gnss_base_link`、出力 `/v2x/gnss/pose_with_covariance` として
起動する。名前も TF の子フレームも重ならないよう選ばれているので、
同一ドメインで共存できる。

確認：

```bash
ros2 node list | grep -c gnss_poser     # 2（/sensing/gnss/racing_kart_gnss_poser と /v2x_gnss_poser）
ros2 run tf2_ros tf2_echo map v2x_gnss_base_link
```

autoware が上がっていると `base_link`↔`gnss_link` の TF が
`robot_state_publisher` から出るので、V2X が送る位置は**アンテナ位置ではなく
`base_link` 位置**になる。driver だけを先に起動した Step 2〜4 との間に
約 0.26 m の差が出るのはこのため。起動直後の数秒は TF 未達の警告が出るが、
autoware が上がれば収まる。

poser を二重に起動せず autoware 側の `/sensing/gnss/pose_with_covariance` に
相乗りさせる構成（`use_gnss_poser:=false` + `gnss_pose_topic:=…`）も V2X launch
自体はサポートしている。ただし **`racing_kart_vehicle.launch.xml` が V2X launch へ
転送しているのは `vehicle_id` だけ**なので、`DRIVER_LAUNCH_ARGS` から渡すには
同 launch に引数の転送を足す改修が要る。

そこまでする価値は薄い。相乗りにすると autoware が落ちたときに V2X も止まる一方、
独立構成なら driver だけで位置共有が成立する。**既定（独立）のまま運用し、
0.26 m は既知のオフセットとして扱う**ことを推奨する。

### 7-2. RViz と MPC

```bash
ros2 topic hz /v2x/vehicle_positions/markers    # マーカが出ていること
```

遠隔 PC の RViz で他車を見るには 2-5 の zenoh 設定が要る。

MPC の V2X 障害物回避は `multi_purpose_mpc_ros` の `use_obstacle_avoidance`
パラメータ（`mpc.launch.xml` の既定 `false`）で切り替わる。**`false` の間、
V2X の値は制御に一切入らない**ので、Step 5〜6 までは安全に観測だけができる。

有効化して実走させるときは、必ず次の順で上げる。

1. 低速（柏の葉の既定は `v_max: 5.0` km/h）で、相手カートを**静止**させたまま
   自車だけ走らせ、回避挙動が想定どおりか見る。
2. 相手を低速で動かす。
3. 遠隔操作の緊急停止に人を張り付けたまま行う。

関係するパラメータ（`config_kashiwanoha.yaml` の `v2x_obstacle_avoidance`）：

| パラメータ                | 既定    | 意味                                                     |
| ------------------------- | ------- | -------------------------------------------------------- |
| `vehicle_radius`          | 0.5 m   | 他車を囲む円の半径                                       |
| `v_max_safety`            | 30 m/s  | 追跡器が受け付ける速度の上限（外れ値の棄却）             |
| `position_jump_threshold` | 5.0 m   | この距離を超える飛びは追跡をリセットする                 |

コリドー外の他車は参照経路の近傍（`max_width/2 + vehicle_radius + 0.5 m`）で
足切りされるので、遠くに居る他車が経路を狭めることは無い。

### 7-3. 記録

`make autoware-driver-zenoh-rosbag` は全トピック（`-a --include-hidden-topics`）を
`output/<timestamp>/d1/rosbag2_all/` に mcap で記録する。V2X トピックもここに入る。
`v2x_msgs` の型定義は autoware ワークスペース側の install から解決されるので、
追加の設定は要らない。

走行後に残す証跡：

| ファイル                              | 内容                                     |
| ------------------------------------- | ---------------------------------------- |
| `output/<ts>/d1/driver.log`           | V2X ノードの接続・再接続・payload 棄却   |
| `output/<ts>/d1/autoware.log`         | MPC・可視化                              |
| `output/<ts>/d1/rosbag2_all/`         | 全トピック（`/v2x/*` を含む）            |
| `output/<ts>/d1/ros/log/`             | ROS 2 ノードログ                         |

---

# 第4部 走行前チェックリスト

[`setup_check.md`](./setup_check.md) の既存項目に加えて確認する。

| # | 項目                                                          | 確認方法                                            |
| - | ------------------------------------------------------------- | --------------------------------------------------- |
| 1 | `V2X_VEHICLE_ID` が号機の割当と一致                            | `grep V2X_VEHICLE_ID .env`                          |
| 2 | 証明書 CN が `V2X_VEHICLE_ID` と一致し、期限内                | `openssl x509 -noout -subject -dates`               |
| 3 | `V2X_VEHICLE_IDS` が全車で同一・参加台数ぶん                    | 各車で `grep`                                       |
| 4 | broker ホスト名・ポート（8883）が全車で同一                    | 同上                                                |
| 5 | ECU の時刻が同期している                                       | `timedatectl`                                       |
| 6 | broker へ TLS 疎通できる                                       | Step 1 の `mosquitto_sub`                           |
| 7 | `/v2x/vehicle_positions` が 10 Hz                              | `ros2 topic hz`                                     |
| 8 | 他車が見えていて、自車が含まれていない                         | `ros2 topic echo --once`                            |
| 9 | 静止時の相互位置が実測距離と一致                               | Step 4                                              |
| 10 | 回線を抜いても driver が落ちない                              | Step 6-2（当日でなく事前に）                        |
| 11 | `DRIVER_LAUNCH_ARGS=use_v2x:=false` で切れることを全員が知っている | 手順書の共有                                     |
| 12 | MPC の `use_obstacle_avoidance` の設定値を把握している         | `mpc.launch.xml` / 起動ログ                         |

---

# 第5部 トラブルシューティング早見表

| 症状                                       | 最初に見る場所                                                       |
| ------------------------------------------ | -------------------------------------------------------------------- |
| driver コンテナが起動しない                 | `output/*/d1/driver.log` の先頭。`vehicle_id is required` / `must be given together` |
| broker に繋がらない                         | Step 1 を単体で実行。ROS より先に切り分ける                          |
| 繋がるが他車が出ない                        | `V2X_VEHICLE_IDS`（全車一致か）→ `ros2 topic hz /v2x/received/vehicle_position/<id>` |
| 自車が `/v2x/vehicle_positions` に出る      | 起きない設計。起きたら `V2X_VEHICLE_ID` と証明書 CN の不一致を疑う   |
| 位置が数十 km ずれる                        | 座標系。トラック（地図）の取り違え。第2の文書を参照                  |
| 位置が 0.26 m ずれる                        | `base_link`↔`gnss_link` の TF。autoware 未起動時の既知の差            |
| 接続と切断を交互に繰り返す                  | 2 台が同じ `V2X_VEHICLE_ID` = 同じ MQTT client_id                    |
| 相手の位置が固まったまま                    | 仕様どおりの last-value-hold。相手側を確認                           |
| 遠隔 RViz に他車が出ない                    | `vehicle/zenoh.json5` の `allow.publishers`（2-5）                    |
| 走行中に挙動が乱れた                        | まず遠隔で停止。`use_obstacle_avoidance` を `false` に戻して再現確認 |

payload 自体の異常（不正 JSON、frame 不一致）は `v2x_position_sharing` のログに
出る。`raw_parser` は失敗しないため `/comm_status` のパース統計には現れない。

---

# 第6部 ロールバックと撤収

## 6-1. V2X だけを止める（最速）

```bash
cd ~/aichallenge-racingkart
echo 'DRIVER_LAUNCH_ARGS=use_v2x:=false' >> .env
make down
make autoware-driver-zenoh-rosbag
```

## 6-2. イメージごと戻す

```bash
docker tag ghcr.io/tier4/racing_kart_interface:pre-v2x \
           ghcr.io/tier4/racing_kart_interface:latest-experiment
make down && make autoware-driver-zenoh-rosbag
```

## 6-3. 証明書を失効させる（紛失時）

```bash
# 管理者の PC で
cd aichallenge-aws/cloudformation_templates/v2x-mqtt-broker
./revoke-kart-cert.sh --envtype dev --vehicle-id d3
```

CRL が再生成・公開され、broker は即座に再読込する（放置しても 1 時間以内）。
失効した証明書を持つカートは TLS ハンドシェイクで拒否される。

---

# 第7部 既知の制約

- **UDP 経路は未完成**（仕様 §6.5）。送信側は動くが、UDP にはトピック名が無く
  `vehicle_id` の伝達方法が仕様上未定（payload への埋め込みは R6.2.1 で禁止）。
  実車運用は MQTT のみ。
- **`vehicle_id` の配布方法**は運用で決める。本書は `.env` に固定で書く前提。
  走行ごとにランダム割当（R4.1）する運用にするなら、strict ACL では証明書も
  同時に差し替えるか、broker を `--acl-mode open` で構築する必要がある。
- **`stale_timeout_sec` は既定 0**（無効）。有効化すると全車の時刻同期が前提になる。
- **遠隔 RViz への V2X 転送は既定で無効**（2-5）。
- **`racing_kart_vehicle.launch.xml` から V2X launch へ転送される引数は
  `vehicle_id` のみ**。broker や poser の設定を現地で launch 引数から変えたい場合は
  同 launch に転送を追加する必要がある（環境変数経由なら変更不要）。
