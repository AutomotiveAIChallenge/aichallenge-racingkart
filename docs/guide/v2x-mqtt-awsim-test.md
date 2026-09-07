# AI Challenge — AWSIM（柏の葉）で MQTT 版 V2X を試す

実車で使う MQTT 経路の V2X 位置情報共有（`v2x_position_sharing` + `v2x_communicator_node` + MQTT broker）を、実車なしで AWSIM の柏の葉トラック上でテストする手順。broker だけの疎通確認から、AWSIM + Autoware + MQTT の統合、2 台での fan-out 往復まで 4 段階に分けてある。

関連文書：

| 文書 | 内容 |
| ---- | ---- |
| `racing_kart_interface/src/v2x_position_sharing/docs/usage.md` | launch 引数・環境変数の一覧、実車モードの検証手順 |
| [`../../vehicle/v2x-virtual-objects.md`](../../vehicle/v2x-virtual-objects.md) | `v2x_virtual_objects.py`（疑似カートの投入）の使い方 |
| [`../../vehicle/v2x-setup.md`](../../vehicle/v2x-setup.md) | 実車への V2X 導入・TLS 証明書の配布 |
| [`../../vehicle/kashiwanoha-track.md`](../../vehicle/kashiwanoha-track.md) | lanelet2 マップ・レースラインの柏の葉への切り替え |
| `aichallenge-aws/cloudformation_templates/v2x-mqtt-broker/README.md` | 本番 broker・CA・証明書の発行と失効 |
| `aichallenge-v2x/docs/SPECIFICATION.md` | 仕様（本書の `R…` はこの要求番号） |

---

# 第0部 なぜ手順が必要か

## 0-1. V2X には経路が 2 つある

参加者コードから見た入口は `/v2x/vehicle_positions` の 1 本だけだが（R3.1）、その中身を作る経路はシミュレーションと実車で別物である。

```text
【シミュレーション（既定）】                    【実車】
AWSIM                                          ublox ──► /sensing/gnss/nav_sat_fix
 └ MultiDomainV2XVehiclePositionFanout               └► racing_kart_gnss_poser
    └ 各車両ドメインへ直接 publish (DDS)                  └► v2x_position_sharing (JSON 化)
       /v2x/vehicle_positions                                └► v2x_communicator ──► MQTT broker
                                                                                        │ fan-out
                                                       /v2x/vehicle_positions ◄─────────┘
```

`make autoware-simulator` は前者しか起動しない。`aichallenge_system.launch.xml` が `mode/v2x.launch.xml` で上げるのは RViz 表示用の `v2x_marker_publisher` だけで、MQTT には一切触れない（`v2x_position_sharing` は `racing_kart_interface` 側のパッケージで、`driver` コンテナの実車 launch からしか起動されない）。

したがって「AWSIM と一緒に MQTT 経路をテストする」とは、**AWSIM 由来の `/v2x/vehicle_positions` を止め、同じトピックを MQTT 経路に作らせる**ことである。

```text
AWSIM (--v2x off, ROS_DOMAIN_ID=0)          オペレータ PC
 └ /sensing/gnss/nav_sat_fix ─┐              └ v2x_virtual_objects.py（相手役）
   （車両ドメイン 1..N）      │                        │ MQTT pub v2x/vehicles/d2/position
                             ▼                        ▼
   racing_kart_interface コンテナ（ドメイン 1）   ┌─────────────┐
    v2x_position_sharing ──► v2x_communicator ──►│ MQTT broker │
    /v2x/vehicle_positions ◄──────────────────── │  fan-out    │
                 │                                └─────────────┘
    autoware コンテナ（ドメイン 1）
     └ v2x_marker_publisher ──► /v2x/vehicle_positions/markers（RViz）
```

## 0-2. 押さえるべき 3 点

| # | 条件 | 破ったときの症状 |
| - | ---- | ---------------- |
| 1 | AWSIM を `--v2x off` で起動する | AWSIM が 20 Hz で publish する**空配列**と MQTT 由来の配列が交互に出て、参加者コードから見て他車が点滅する |
| 2 | MQTT スタックを車両と同じ ROS ドメインで動かす | 誰も `/v2x/vehicle_positions` を受け取れない。AWSIM 本体はドメイン 0、車両 N のセンサ・トピックは `1 + (N-1)` |
| 3 | `vehicle_ids` に相手の ID を入れる | broker には届くが受信ルートが無いので捨てられ、`/v2x/received/vehicle_position/dN` すら出ない。既定は `d1,d2,d3,d4` |

自号 ID は受信ルートから構造的に外れる（R5.2.4）ので、`vehicle_id:=d1` のカートに `d1` の疑似カートを見せることはできない。

---

# 第1部 準備

## 1-1. ローカル broker（平文 1883）

段階 1〜3 は TLS も証明書も要らない。`racing_kart_interface` に検証専用の設定が入っている。

```bash
cd racing_kart_interface
mosquitto -c src/v2x_position_sharing/deploy/mqtt_broker/mosquitto/mosquitto.local.conf -v
```

`allow_anonymous true` / ACL なしなので、到達可能なホストでは絶対に使わないこと。ホストに mosquitto が無ければ `sudo apt install mosquitto mosquitto-clients`、あるいは `src/v2x_position_sharing/deploy/mqtt_broker/docker-compose.yaml`（`eclipse-mosquitto:2.0`、認証あり）を使う。

## 1-2. MQTT スタックを動かすイメージ

`v2x_position_sharing` は `racing_kart_interface` のイメージに焼かれている。入っているか確認する。

```bash
docker run --rm --entrypoint bash ghcr.io/tier4/racing_kart_interface:latest-experiment \
  -lc 'ls /workspace/install | grep v2x; cat /buildtime.txt'
```

`v2x_position_sharing` と `v2x_communicator_node` が並べば良い。無ければ `cd racing_kart_interface/docker && ./docker_build.sh` で作り直す。

> **`driver` サービスや `./docker_run.sh sim` を使わない理由** — `docker-compose.yml` の `driver` は `run_driver.bash vehicle` 固定で、`/dev/vcu`・`/dev/gnss` の bind mount と CAN 設定（`pcan.bash`）を伴う。`racing_kart_interface` の `utils/run.bash sim` は逆に `use_v2x:=false` を明示的に渡す（シミュレータが自分で publish する前提なので）。どちらも机上テストには向かないため、本書では `docker run` で V2X の launch だけを直接起動する。

## 1-3. `--v2x off` 付きのシミュレータ起動スクリプト

`aichallenge/simulator_scripts/kashiwanoha.sh` は AWSIM の引数を固定で `exec` するので、`--v2x off` を後から足せない。V2X テスト用のモードを 1 本追加する（`Makefile` が `simulator_scripts/*.sh` を拾って `make simulator-<名前>` を自動生成するので、置くだけで使えるようになる）。

```bash
cd aichallenge-racingkart
sed -e 's/^    --venue kashiwanoha \\$/    --venue kashiwanoha \\\n    --v2x off \\/' \
    aichallenge/simulator_scripts/kashiwanoha.sh \
    > aichallenge/simulator_scripts/kashiwanoha-v2x.sh
chmod +x aichallenge/simulator_scripts/kashiwanoha-v2x.sh
grep -n -- "--venue\|--v2x\|vehicles" aichallenge/simulator_scripts/kashiwanoha-v2x.sh
```

`--vehicles "${vehicles}"`（第 1 引数、既定 1）はそのまま残るので、第5部の 2 台構成でも同じスクリプトを使える。

AWSIM 側の V2X は起動 UI の `V2X` 行でも切れるが、`--vehicles` が指定されていると起動 UI 自体がスキップされるため、`make` 経由の起動では CLI 引数（`--v2x off`）だけが効く。

## 1-4. 相手役（疑似カート）

`vehicle/v2x_virtual_objects.py` に柏の葉のレースラインを周回する疑似カートのシナリオが同梱されている。`local-test.yaml` は平文 `127.0.0.1:1883` 向けで、まさに本書の段階 1〜3 用である。

```bash
cd aichallenge-racingkart/vehicle
./v2x_virtual_objects.py --scenario v2x-scenarios/local-test.yaml --dry-run --duration 2
```

`d2`（6.0 m/s で周回）と `d3`（弧長 100 m の位置に静止）が、`defaults.raceline` のレースライン（MGRS 座標）の上に出る。`--dry-run` の 1 行目に読めた CSV・周長・`loop` / `open line` の判定が出るので、まずそこを見る。

`local-test.yaml` と `kashiwanoha-demo.yaml` の `raceline` は**作成者のローカル絶対パスを指している**（`/home/yuasabe/Downloads/…`）ので、他の PC ではまずここを直す。リポジトリ内で完結させるなら柏の葉のレースライン CSV に向ける。

```yaml
defaults:
  raceline: aichallenge/workspace/src/aichallenge_submit/simple_trajectory_generator/data/kashiwanoha/raceline_awsim_30km_from_garage.csv
```

このパス（1 周 約 377 m、閉ループ）は AWSIM の `--venue kashiwanoha` と同じ座標系で、相対パスは `aichallenge-racingkart` のリポジトリルートから解決される。ID を増減する・速度を変える場合は [`../../vehicle/v2x-virtual-objects.md`](../../vehicle/v2x-virtual-objects.md) 第3部を参照。

---

# 第2部 段階1 — broker と MQTT スタックだけ（AWSIM なし）

AWSIM を立てる前に、MQTT 経路そのものを切り分けておく。参加者スタックと衝突させないため、ここでは捨てドメイン（`ROS_DOMAIN_ID=9`）を使う。

```bash
# 端末1: broker
cd racing_kart_interface
mosquitto -c src/v2x_position_sharing/deploy/mqtt_broker/mosquitto/mosquitto.local.conf -v

# 端末2: MQTT スタック（ego pose は手で与えるので poser は止める）
docker run -d --name v2x-d1 --network host -e ROS_DOMAIN_ID=9 --entrypoint bash \
  ghcr.io/tier4/racing_kart_interface:latest-experiment -lc \
  "source /workspace/install/setup.bash && \
   ros2 launch v2x_position_sharing v2x_kart_position.launch.py \
     vehicle_id:=d1 use_gnss_poser:=false broker_host:=127.0.0.1 broker_port:=1883"
docker logs -f v2x-d1
```

起動ログで、受信ルート・自号 ID・publish 周期を確認する。

```text
[v2x_position_sharing]: Listening for vehicle_id=d2 on /v2x/received/vehicle_position/d2
[v2x_position_sharing]: Listening for vehicle_id=d3 on /v2x/received/vehicle_position/d3
[v2x_position_sharing]: Sharing V2X positions: vehicle_id=d1 frame_id=map rate=10.0 Hz ...
[v2x_connector_std.mqtt_transporter]: Connected to MQTT broker successfully
[v2x_connector_std.mqtt_transporter]: Subscribed to topic: v2x/vehicles/+/position
```

`rate=` は launch の `publish_rate_hz` の既定値で、イメージのビルド時期によって 10 Hz と 20 Hz がある。仕様の 20 Hz（R5.2.1）で試すなら `publish_rate_hz:=20.0` を明示的に渡す。

## 2-1. 送信（TX）

ego pose を手で流し込み、`v2x/vehicles/d1/position` に出ることを見る。

```bash
# 端末3: ego pose を流し続ける（Ctrl-C で停止）
docker exec v2x-d1 bash -lc "source /workspace/install/setup.bash && \
  ros2 topic pub -r 20 /v2x/gnss/pose_with_covariance geometry_msgs/msg/PoseWithCovarianceStamped \
  '{header: {frame_id: map}, pose: {pose: {position: {x: 3833.2, y: 73770.7, z: 0.0}},
    covariance: [0.0064,0,0,0,0,0, 0,0.0064,0,0,0,0, 0,0,0.0225,0,0,0,
                 0,0,0,0,0,0, 0,0,0,0,0,0, 0,0,0,0,0,0]}}'"

# 端末4: broker に出ているか
timeout 6 mosquitto_sub -h 127.0.0.1 -t 'v2x/vehicles/d1/position' -v
```

```text
v2x/vehicles/d1/position {"covariance":{"x":0.08,"y":0.08,"z":0.15},"frame_id":"map",
  "position":{"x":3833.2,"y":73770.7,"z":0.0},"stamp":"1970-01-01T00:00:00.000Z"}
```

payload に `vehicle_id` が入らないこと（ID はトピック名だけで伝わる、R4.2）と、`covariance` が分散ではなく**標準偏差** [m]（0.0064 → 0.08）であること（R10.2.1）を確認する。`stamp` が 1970 になるのは `ros2 topic pub` が `header.stamp` を 0 で送るためで、実際の pose では観測時刻が入る。

## 2-2. 受信（RX）

疑似カートを出して、`/v2x/vehicle_positions` に相手だけが並ぶことを見る。

```bash
# 端末3
cd aichallenge-racingkart/vehicle
./v2x_virtual_objects.py --scenario v2x-scenarios/local-test.yaml --duration 60

# 端末4
docker exec v2x-d1 bash -lc "source /workspace/install/setup.bash && timeout 5 ros2 topic hz /v2x/vehicle_positions"
docker exec v2x-d1 bash -lc "source /workspace/install/setup.bash && ros2 topic echo --once /v2x/vehicle_positions"
```

```text
vehicles:
- vehicle_id: d2
  position: {x: 3847.07…, y: 73743.83…, z: 0.0}
  covariance: {x: 0.08, y: 0.08, z: 0.15}
- vehicle_id: d3
  position: {x: 3791.99…, y: 73706.73…, z: 0.0}
```

配列の `header.stamp` は publish 時刻、各要素の `header.stamp` は送信側の観測時刻で、別物である（R10.1.1）。他車が居なくても 20 Hz（または 10 Hz）で空配列が出るのが正しい挙動（R5.2.2）。MQTT・受信統計は `/comm_status` と `/diagnostics` に出る。

片付け：`docker rm -f v2x-d1`。

---

# 第3部 段階2 — AWSIM（柏の葉）+ MQTT スタック（Autoware なし）

ego pose は AWSIM の GNSS から取る。実車と同じく `use_gnss_poser` は既定の `true` のままで、V2X スタックが自分用に起動する `v2x_gnss_poser`（`racing_kart_gnss_poser` を無改造で使用）が `/sensing/gnss/nav_sat_fix` を MGRS の `map` pose に変換する。実車ではこの fix を ublox が出し、シミュレーションでは AWSIM が出す — V2X スタックから見た入口は同じである。

```bash
cd aichallenge-racingkart
make simulator-kashiwanoha-v2x        # AWSIM: 柏の葉・1 台・V2X fanout off

# MQTT スタックを車両 1 のドメイン（=1）で起動
docker run -d --name v2x-d1 --network host -e ROS_DOMAIN_ID=1 --entrypoint bash \
  ghcr.io/tier4/racing_kart_interface:latest-experiment -lc \
  "source /workspace/install/setup.bash && \
   ros2 launch v2x_position_sharing v2x_kart_position.launch.py \
     vehicle_id:=d1 vehicle_ids:=d1,d2,d3 broker_host:=127.0.0.1"

# 相手役
cd vehicle && ./v2x_virtual_objects.py --scenario v2x-scenarios/local-test.yaml
```

確認：

```bash
V2X="docker exec v2x-d1 bash -lc"
$V2X "source /workspace/install/setup.bash && ros2 topic echo --once /v2x/gnss/gnss_fixed"        # data: true
$V2X "source /workspace/install/setup.bash && timeout 5 ros2 topic hz /v2x/gnss/pose_with_covariance"
$V2X "source /workspace/install/setup.bash && ros2 topic echo --once /v2x/vehicle_positions"

# 自号の送信が broker に出ているか
timeout 6 mosquitto_sub -h 127.0.0.1 -t 'v2x/vehicles/+/position' -v
```

この段階だけの注意として、Autoware 側の `robot_state_publisher` が居らず `base_link → gnss_link` の TF が無いため、poser は TF 警告を出したうえで恒等変換に落ち、`base_link` ではなくアンテナ位置（`base_link` から 0.26 m 前）を報告する。切り分けには影響しないが、位置が一貫して 0.26 m ずれていることの説明になる。段階 3 で Autoware を上げれば TF が来るので解消する。`v2x_kart_position.launch.py` は poser の `base_frame` を引数として公開していない（`input_topic_fix` / `output_topic_gnss_pose_cov` / `map_frame` だけを渡す）ので、この段階で明示したい場合は `v2x_gnss_poser.launch.xml` を単体で `base_frame:=gnss_link` 付きで起動する。

AWSIM 側の `/v2x/vehicle_positions` が本当に止まっているかは、MQTT スタックを止めた状態で確認するのが確実。

```bash
docker rm -f v2x-d1
docker compose run --rm --no-deps autoware-command bash -lc \
  'timeout 10 ros2 topic hz /v2x/vehicle_positions'   # 何も出なければ --v2x off が効いている
```

---

# 第4部 段階3 — AWSIM + Autoware + MQTT スタック（統合）

段階 2 との違いは Autoware が並ぶことだけで、V2X スタックの引数は変えない。`use_gnss_poser` は**既定の `true` のまま**にする — これが実車と同じ構成である。

Autoware 側（`aichallenge_submit_launch/reference.launch.xml`）は `sensing/gnss` 名前空間で自分用の `racing_kart_gnss_poser` を起動するので、同じ `/sensing/gnss/nav_sat_fix` を読む poser が 2 つ並ぶ。これは事故ではなく設計で、`v2x_gnss_poser.launch.xml` が 3 点を分けることで衝突しないようにしてある。

| | Autoware 側 | V2X 側 |
| - | ----------- | ------ |
| ノード名 | `racing_kart_gnss_poser` | `v2x_gnss_poser` |
| pose 出力 | `/sensing/gnss/pose_with_covariance` | `/v2x/gnss/pose_with_covariance` |
| broadcast する TF の child | `gnss_base_link` | `v2x_gnss_base_link` |

実車でも同じことが起きている。`racing_kart_vehicle.launch.xml` は `v2x_kart_position.launch.py` に `vehicle_id` しか渡さないので `use_gnss_poser` は `true` のままで、`driver` コンテナの ublox が出す `/sensing/gnss/nav_sat_fix` を V2X 側の poser が、同じ fix を Autoware 側の poser が、それぞれ読む。シミュレーションではその fix の出どころが ublox から AWSIM に変わるだけである。

```bash
cd aichallenge-racingkart
make simulator-kashiwanoha-v2x
make autoware-simulator                # ドメイン 1（.env の ROS_DOMAIN_ID）

docker run -d --name v2x-d1 --network host -e ROS_DOMAIN_ID=1 --entrypoint bash \
  ghcr.io/tier4/racing_kart_interface:latest-experiment -lc \
  "source /workspace/install/setup.bash && \
   ros2 launch v2x_position_sharing v2x_kart_position.launch.py \
     vehicle_id:=d1 vehicle_ids:=d1,d2,d3 \
     broker_host:=127.0.0.1 publish_rate_hz:=20.0"

cd vehicle && ./v2x_virtual_objects.py --scenario v2x-scenarios/local-test.yaml
```

> **`use_gnss_poser:=false` を使う場合** — Autoware が既に publish している pose をそのまま共有したい（参加者コードが localize に使う pose と V2X で送る pose を一致させる、ノードを 1 つ減らす）ときの選択肢で、`racing_kart_interface` の `usage.md` §3 が挙げているのはこのケースである。実車と構成を変えることになるので、既定は上のまま `true` にしておき、必要なときだけ切り替える。
>
> ```bash
> #（上の docker run の引数に足す）
> use_gnss_poser:=false gnss_pose_topic:=/sensing/gnss/pose_with_covariance
> ```
>
> このとき V2X 側の poser が消えるので `/v2x/gnss/pose_with_covariance` も出なくなる。下の確認手順はそのトピックを見ているので、`/sensing/gnss/pose_with_covariance` に読み替える。

走らせる：

```bash
make autoware-request-initialpose
make autoware-request-control
# RViz は autoware コンテナが起動している（awsim モードは run_rviz:=true）。別 PC から見る場合のみ make rviz2
```

見るもの：

| 対象 | 期待 |
| ---- | ---- |
| `/v2x/gnss/pose_with_covariance` | V2X 側の poser が出す自号 pose。`/sensing/gnss/pose_with_covariance`（Autoware 側の poser）と同じ位置を指す（TF が来るので段階 2 の 0.26 m ずれは無い） |
| RViz の `v2x_vehicles` マーカ | 疑似カートが柏の葉のレースライン上に出る。`v2x_marker_publisher` は `/v2x/vehicle_positions/markers` へ変換しており、ドメイン 0 以外なら常に起動している |
| `/v2x/vehicle_positions` | `d2`・`d3` が 20 Hz で並び、自号 `d1` は入らない |
| `v2x/vehicles/d1/position`（broker） | 自車が動くと位置が更新される |
| `/comm_status` | MQTT 接続状態と送受信統計 |

## 4-1. 時刻（stamp）の読み方

`v2x_kart_position.launch.py` は `use_sim_time` を引数として公開していない（実車 launch では `racing_kart_vehicle.launch.xml` がグローバルパラメータとして設定している）。上の `docker run` では設定されないので、この構成では stamp の出どころが 2 系統になる。

| stamp | 時刻系 | 由来 |
| ----- | ------ | ---- |
| payload / 配列要素の `header.stamp`（観測時刻） | AWSIM のシミュレーション時刻 | poser が fix の stamp をそのまま載せる（`gnss_poser_core.cpp`）。AWSIM の時刻源は Unity の経過時間なので 0 起点、RFC 3339 にすると `1970-01-01T00:0…` に見える |
| 配列の `header.stamp`（publish 時刻） | V2X コンテナの wall clock | `use_sim_time` が渡らないため |

`stale_timeout_sec` は既定 0（最終値保持、R7.2.3）で時刻比較をしないので、この不一致で他車が落ちることはない。ただし 2 つの stamp の差を遅延として読むことはできない。実車では両方が wall clock になるので、この点だけは机上テスト固有である。

`multi_purpose_mpc_ros` などの回避挙動まで見る場合の設定は [`../../vehicle/v2x-setup.md`](../../vehicle/v2x-setup.md) を参照。トラック（lanelet2 マップ・レースライン）は `reference.launch.xml` の `track` 既定が `kashiwanoha` なので、AWSIM の `--venue kashiwanoha` と既に一致している。

---

# 第5部 段階4 — 2 台とも実 MQTT（fan-out 往復）

疑似カートを使わず、AWSIM の 2 台にそれぞれ MQTT スタックを付けて、broker の fan-out を往復させる。`make simulator-*` は車両数を渡せないので、`run_simulator.bash` を直接呼ぶ。

```bash
cd aichallenge-racingkart
docker compose run -d --rm --no-deps -e ROS_DOMAIN_ID=0 simulator \
  bash -lc '/aichallenge/run_simulator.bash kashiwanoha-v2x 2'

# 車両 1 → ドメイン 1、車両 2 → ドメイン 2
for p in 1 2; do LOG_DIR=/output/$(date +%Y%m%d-%H%M%S) ROS_DOMAIN_ID=$p docker compose -p $p up -d autoware; done

for p in 1 2; do
  docker run -d --name v2x-d$p --network host -e ROS_DOMAIN_ID=$p --entrypoint bash \
    ghcr.io/tier4/racing_kart_interface:latest-experiment -lc \
    "source /workspace/install/setup.bash && \
     ros2 launch v2x_position_sharing v2x_kart_position.launch.py \
       vehicle_id:=d$p vehicle_ids:=d1,d2 \
       broker_host:=127.0.0.1 publish_rate_hz:=20.0"
done
```

確認：`d1` のスタックには `d2` だけ、`d2` のスタックには `d1` だけが並ぶ（自号除外、R5.2.4）。

```bash
for p in 1 2; do
  docker exec v2x-d$p bash -lc "source /workspace/install/setup.bash && \
    ros2 topic echo --once /v2x/vehicle_positions" | grep vehicle_id
done
timeout 6 mosquitto_sub -h 127.0.0.1 -t 'v2x/vehicles/+/position' -v   # d1 と d2 の両方が流れる
```

`ROS_DOMAIN_ID` を 1 と 2 に分けているので DDS 上は完全に別スタックであり、両者が互いを見られるのは MQTT 経路だけを通った結果である。ここが AWSIM の DDS fanout と混ざっていないことの一番強い証拠になる。

---

# 第6部 本番 broker（TLS）に向ける

段階 3 のまま broker だけ本番へ差し替えると、TLS・ACL・ID 割り当てまで含めた実車と同じ経路になる。証明書は ID ごと（CN = ID）で、`issue-kart-cert.sh` の出力レイアウト（`<dir>/<id>/{ca.crt,kart.crt,kart.key}`）をそのまま使う。

```bash
CERTS=$PWD/../aichallenge-aws/cloudformation_templates/v2x-mqtt-broker/kart-certs/dev/d1

docker run -d --name v2x-d1 --network host -e ROS_DOMAIN_ID=1 \
  -v "$CERTS":/etc/v2x/tls:ro --entrypoint bash \
  ghcr.io/tier4/racing_kart_interface:latest-experiment -lc \
  "source /workspace/install/setup.bash && \
   ros2 launch v2x_position_sharing v2x_kart_position.launch.py \
     vehicle_id:=d1 vehicle_ids:=d1,d2,d3 \
     broker_host:=v2x-mqtt.dev.aichallenge-board.jsae.or.jp broker_port:=8883 \
     mqtt_tls_ca_file:=/etc/v2x/tls/ca.crt \
     mqtt_tls_cert_file:=/etc/v2x/tls/kart.crt \
     mqtt_tls_key_file:=/etc/v2x/tls/kart.key"
```

証明書の CN が `vehicle_id` と一致していないと、TLS は通っても broker の ACL（`use_identity_as_username true` + `pattern write v2x/vehicles/%u/position`）で publish が拒否され、静かに切断される。

相手役も TLS へ切り替える。`v2x-scenarios/kashiwanoha-demo.yaml` はそのためのシナリオである。`local-test.yaml` から作る場合は、CLI に TLS を**有効化**するオプションが無く（`--certs-dir` だけでは平文のまま）`tls: true` はシナリオ側に書く必要がある点だけ注意する。`raceline` は 1-4 のとおりどちらのシナリオでも要確認。

```bash
cd aichallenge-racingkart/vehicle
sed -e 's/^  host: .*/  host: v2x-mqtt.dev.aichallenge-board.jsae.or.jp/' \
    -e 's/^  port: .*/  port: 8883/' \
    -e 's/^  tls: .*/  tls: true\n  certs_dir: ..\/aichallenge-aws\/cloudformation_templates\/v2x-mqtt-broker\/kart-certs\/dev/' \
    v2x-scenarios/local-test.yaml > v2x-scenarios/kashiwanoha-tls.yaml

./v2x_virtual_objects.py --scenario v2x-scenarios/kashiwanoha-tls.yaml --only d2 --duration 10
```

`certs_dir` の相対パスは `aichallenge-racingkart` のリポジトリルートから解決される。起動時に送信計画・証明書パス・「実車に必要な `V2X_VEHICLE_IDS`」が表示されるので、まず `--only`＋`--duration` で 1 台だけ短時間出して確かめる。
ここから先の証明書・ID 運用は [`../../vehicle/v2x-virtual-objects.md`](../../vehicle/v2x-virtual-objects.md) と broker の README が正。**本番 broker は他のチームや実車と共有している**ので、使う ID は事前に確保したものだけにする。TLS の 3 引数は 3 つ揃っていないと無視される。パスワードや鍵のパスは環境変数（`V2X_MQTT_TLS_*` / `V2X_MQTT_PASSWORD`）で渡すのが本来の運用（R6.4.2）。

---

# 第7部 片付けとトラブルシュート

```bash
docker rm -f v2x-d1 v2x-d2
pkill -f v2x_virtual_objects.py
pkill -f 'mosquitto -c'
cd aichallenge-racingkart && make down
for p in 1 2; do docker compose -p $p down --remove-orphans; done   # 第5部を実施した場合
```

| 症状 | 原因 | 対処 |
| ---- | ---- | ---- |
| `/v2x/vehicle_positions` が空配列と中身入りを交互に返す | AWSIM の fanout が生きている | `--v2x off` 付きのシナリオで起動する（1-3）。効いていれば `output/<timestamp>/awsim.log` に `V2X vehicle-position fanout disabled via command line argument` が出る（生きている場合は `added /v2x/vehicle_positions publisher for domain N`） |
| MQTT スタックの `/v2x/vehicle_positions` が誰にも見えない | ドメイン違い | 車両 N のドメインは `1 + (N-1)`。`-e ROS_DOMAIN_ID=` を合わせる |
| broker には流れているが `/v2x/vehicle_positions` に出ない | `vehicle_ids` に相手 ID が無い | `vehicle_ids:=d1,d2,d3` のように受信ルートを作る（0-2 の条件 3） |
| 自号 ID の疑似カートだけ見えない | 仕様どおりの自号除外（R5.2.4） | 疑似カートの ID を変える |
| `Connected to MQTT broker` が出ない | broker 未起動 / ホスト・ポート違い / TLS 不一致 | `mosquitto_sub` で同じ引数を叩いて切り分ける |
| コンテナ間で DDS が繋がらない | `lo` のマルチキャストと受信バッファ（通常は `driver` コンテナの `dds.bash` が設定する。`docker run` では通らない） | `sudo ip link set multicast on lo` / `sudo sysctl -w net.core.rmem_max=2147483647` |
| 位置がトラックから外れている | レースライン CSV とトラックの不一致 | 柏の葉なら `data/kashiwanoha/` 配下の CSV を使う |
| `/v2x/gnss/pose_with_covariance` が出ない | AWSIM の GNSS が off、または `use_gnss_poser:=false` にしている | AWSIM を `--gnss on`（既定）で起動する。`--gnss off` は V2X からもそのカートを外す |
| payload の `stamp` が 1970 年台 | AWSIM の時刻源が Unity の経過時間（0 起点） | 異常ではない（4-1）。手で `ros2 topic pub` した pose でも同じ見え方になる |
