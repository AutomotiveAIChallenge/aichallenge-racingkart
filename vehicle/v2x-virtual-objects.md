# AI Challenge - V2X 仮想オブジェクトの投入（デモ・練習会）
`vehicle/v2x_virtual_objects.py` は、実車が 1 台しかいない場所でも他車が居るように見せるためのツールである。V2X の MQTT broker へ「疑似カート」の位置を publish するので、実車側は何も変更せずに `/v2x/vehicle_positions` で相手を受け取れる。回避・追い越しの練習と、V2X 経路そのものの疎通確認に使う。
関連文書：[`v2x-setup.md`](./v2x-setup.md)（実車への V2X 導入・証明書配布）、[`kashiwanoha-track.md`](./kashiwanoha-track.md)（柏の葉トラックへの切り替え）、[`README.md`](./README.md)（実車の起動手順）、`aichallenge-v2x/docs/SPECIFICATION.md`（本書の `R…` はこの要求番号）、`aichallenge-aws/cloudformation_templates/v2x-mqtt-broker/README.md`（broker・証明書）。

---

# 第0部 何をしているのか
## 0-1. データの流れ
```text
オペレータ PC                                   実車 ECU (ROS_DOMAIN_ID=1)
┌───────────────────────────┐                  ┌──────────────────────────────────┐
│ v2x_virtual_objects.py     │   MQTTS 8883    │ v2x_communicator (無改造)        │
│  d5 (静止)  ──pub v2x/vehicles/d5/position──►│   └► /v2x/received/…/d5          │
│  d8 (周回)  ──pub v2x/vehicles/d8/position──►│   └► /v2x/received/…/d8          │
│  d10 (周回) ──pub v2x/vehicles/d10/position─►│        └► v2x_position_sharing   │
└───────────────────────────┘   ▲              │             └► /v2x/vehicle_positions
                                │              └──────────────────────────────────┘
                       ┌────────┴────────┐                    │ 同一 ROS ドメイン
                       │ Mosquitto on EC2│        v2x_marker_publisher（RViz 表示）
                       │  fan-out = 中継 │        multi_purpose_mpc_ros（回避）
                       └─────────────────┘
```
broker の fan-out がそのまま中継になる（R6.4.1）ので、**実車側の設定変更もノード追加も要らない**。仮想オブジェクトは実車から見て他のカートと完全に区別が付かない。`vehicle_id` は MQTT トピック名だけで伝わり、payload には入らない（R4.2）。

## 0-2. 実車から見えるようにするための 3 つの条件
| # | 条件 | 破ったときの症状 |
| - | ---- | ---------------- |
| 1 | 実車の `V2X_VEHICLE_IDS` に仮想オブジェクトの ID が入っていること | broker には届くが実車が捨てる。`/v2x/received/vehicle_position/dN` すら出ない |
| 2 | ID ごとの証明書（CN = ID）を持っていること | TLS は通るが publish が ACL で拒否され、静かに切断される |
| 3 | 実車が使う ID と衝突していないこと | 自号 ID は自分で除外する（R5.2.4）ので、その仮想オブジェクトだけ見えない |
条件 1 は launch が `vehicle_ids` から受信ルートを生成する仕組みの帰結で、既定値は `d1,d2,d3,d4` である。条件 2 は broker が `use_identity_as_username true` + `pattern write v2x/vehicles/%u/position` で運用されているため。スクリプトは 1 について必要な `V2X_VEHICLE_IDS` を起動時に表示し、2 は publish 前に `openssl` で CN を照合して落とす。

---

# 第1部 準備
## 1-1. オペレータ PC に必要なもの
| もの | 用途 | 入れ方 |
| ---- | ---- | ------ |
| Python 3.8+ / PyYAML | スクリプト本体 | `pip3 install pyyaml` |
| paho-mqtt | MQTT 送信（推奨） | `pip3 install paho-mqtt` |
| mosquitto-clients | paho が無いときの代替、および受信確認 | `sudo apt install mosquitto-clients` |
| openssl | 証明書 CN の照合 | 通常は既に入っている |
`--transport auto`（既定）は paho-mqtt が import できればそれを使い、無ければ長寿命の `mosquitto_pub -l` に落ちる。どちらでも動くが、台数が増えるときは paho の方が軽い。ROS 2 は要らない。ネットワークは broker の 8883/tcp に出られれば足りる。

## 1-2. 証明書
ID ごとに `issue-kart-cert.sh` の出力（`ca.crt` / `kart.crt` / `kart.key`）が必要である。既に発行済みのものが `aichallenge-aws/cloudformation_templates/v2x-mqtt-broker/kart-certs/dev/` にある。
```bash
# 足りない ID を発行する
cd aichallenge-aws/cloudformation_templates/v2x-mqtt-broker
./issue-kart-cert.sh --envtype dev --vehicle-id d5
```
シナリオの `certs_dir` は `<certs_dir>/<id>/{ca.crt,kart.crt,kart.key}` というレイアウトを期待する。発行スクリプトの出力そのままなので、通常は `certs_dir` を発行先ディレクトリに向けるだけでよい。
broker を `--acl-mode open` で立てた場合は CN と ID が一致していなくても publish できる。その場合は `--skip-cert-check` で照合を飛ばせるが、既定の strict では**照合が落ちる = 当日確実に繋がらない**ので飛ばさないこと。

## 1-3. ID の割り当て
実車が使う ID と仮想オブジェクトの ID は必ず分ける。走行枠ごとに実車が `d1` / `d2` を使うなら、仮想オブジェクトは `d5` / `d8` / `d10` のように離した番号にしておくと取り違えない。発行済みの証明書は次のとおり（2026-09-04 時点、いずれも 2027-09 まで有効）。
| broker | host | 証明書のある ID |
| ------ | ---- | --------------- |
| dev（既定） | `v2x-mqtt.dev.aichallenge-board.jsae.or.jp` | `d1` `d2` `d5` `d8` `d10` `d11` |
| dev / cctb | `v2x-mqtt-cctb.dev.aichallenge-board.jsae.or.jp` | `d2` `d3` `d7` |

---

# 第2部 使い方
## 2-1. 3 段階で確認する
順番に上げていくと、当日いきなり本番 broker に向かって原因が分からなくなる事故を避けられる。
```bash
cd vehicle

# ① 座標と動きだけ確認する（broker へ一切繋がない）
./v2x_virtual_objects.py --scenario v2x-scenarios/kashiwanoha-demo.yaml --dry-run --duration 2

# ② 手元の mosquitto で送受信を確認する
mosquitto -p 1883 -v                                          # 別端末
mosquitto_sub -h 127.0.0.1 -t 'v2x/vehicles/+/position' -v    # 別端末
./v2x_virtual_objects.py --scenario v2x-scenarios/local-test.yaml --duration 10

# ③ 本番 broker へ 1 台だけ、10 秒だけ出す
./v2x_virtual_objects.py --scenario v2x-scenarios/kashiwanoha-demo.yaml --only d5 --duration 10

# ④ 本番（Ctrl-C で停止）
./v2x_virtual_objects.py --scenario v2x-scenarios/kashiwanoha-demo.yaml
```
起動直後に、送信計画と「実車に必要な `V2X_VEHICLE_IDS`」が表示される。この行を走行枠の担当者へそのまま渡すのが確実である。
```text
raceline …/kashiwanoha/raceline_awsim_30km_from_garage.csv: 377 points, 377.0 m, loop
broker: mqtts://v2x-mqtt.dev.aichallenge-board.jsae.or.jp:8883 qos=0  rate=20 Hz
id     mode                 x            y       z   speed  topic
d5     static         3777.44     73716.89    0.00       -  v2x/vehicles/d5/position
d8     raceline       3827.51     73725.11    0.00    4.00  v2x/vehicles/d8/position
d10    raceline       3720.55     73756.33    0.00    3.00  v2x/vehicles/d10/position

the karts must be launched with these ids in their receive routes:
  V2X_VEHICLE_IDS=d1,d2,d3,d4,d5,d8,d10
```
走行中は 1 秒ごとに状態行が出る。`[up]` が接続、`s=` がレースライン上の弧長 [m]、`v=` が速度 [m/s] である。
```text
t=  12.0s sent=720     d5[up] s= 120.0 x=  3777.44 y= 73716.89 v= 0.0 | d8[up] s= 108.0 x=  3811.02 y= 73692.55 v= 4.0
```

## 2-2. 実車側でどう見えるか
```bash
# ECU 上（autoware コンテナ内）
ros2 topic hz /v2x/vehicle_positions           # 20 Hz
ros2 topic echo --once /v2x/vehicle_positions  # 仮想オブジェクトが並ぶ
ros2 topic echo --once /comm_status            # MQTT 接続と受信統計
```
RViz では `v2x_marker_publisher` のマーカとして出る。MPC の回避を効かせる設定は `multi_purpose_mpc_ros` 側の話なので [`v2x-setup.md`](./v2x-setup.md) を参照。

---

# 第3部 シナリオの書き方
シナリオは `vehicle/v2x-scenarios/*.yaml` に置く。同梱の 3 本をコピーして始めるのが早い。
| ファイル | 用途 |
| -------- | ---- |
| `kashiwanoha-demo.yaml` | 柏の葉。静止 1 台 + 周回 2 台。デモ・練習会の既定 |
| `citycircuit-demo.yaml` | City Circuit Tokyo Bay。cctb 用の別 broker・別 CA |
| `local-test.yaml` | 手元の mosquitto に平文で出すだけ。搬入前の疎通確認用 |

## 3-1. トップレベル
| キー | 既定 | 意味 |
| ---- | ---- | ---- |
| `rate_hz` | `20.0` | 各オブジェクトの publish 周期。実車の GNSS 相当は 20 Hz |
| `broker.host` | `127.0.0.1` | broker のホスト名。証明書の検証名なので IP ではなく名前で書く |
| `broker.port` | TLS 時 `8883` / 平文 `1883` | ポート |
| `broker.tls` | `true` | `false` で平文（手元テスト専用） |
| `broker.certs_dir` | — | 証明書の親ディレクトリ。相対パスはリポジトリルートから解決される |
| `broker.qos` | `0` | MQTT QoS（R6.4.4） |
| `defaults` | — | 下の各オブジェクトのキーの既定値をまとめて与える |
| `objects` | — | 1 要素 = 1 台の仮想オブジェクト |

## 3-2. オブジェクト
`defaults` に書いたキーは全オブジェクトに効き、オブジェクト側に同じキーがあればそちらが勝つ。
| キー | モード | 意味 |
| ---- | ------ | ---- |
| `id` | 両方 | `vehicle_id`。トピック名になるので `/` `+` `#` は使えない |
| `mode` | — | `static`（動かない）または `raceline`（レースラインを周回） |
| `x` / `y` / `z` | static | map(MGRS) 座標。`s_m` とは併用できない |
| `s_m` | static | レースライン上の弧長 [m] で位置を指定する。座標を調べる必要がない |
| `raceline` | raceline / static(`s_m`) | レースライン CSV のパス。`x,y[,z][,speed]` 列を読む |
| `speed_mps` | raceline | 一定速度 [m/s]。指定するとこちらが優先 |
| `speed_scale` | raceline | CSV の `speed` 列に掛ける倍率（既定 `1.0`） |
| `start_s_m` | raceline | 開始位置の弧長 [m]。複数台をばらすのに使う |
| `frame_id` | 両方 | 既定 `map`。全車で一致していないと意味を持たない（R9.1） |
| `covariance` | 両方 | 位置の**標準偏差** [m]（分散ではない、R10.2.1）。既定 `[0.08, 0.08, 0.15]` |
| `z_offset` | 両方 | z に足すオフセット [m]。レースライン CSV の z が 0 のため、実車の z に合わせたいとき使う |
レースライン CSV は `simple_trajectory_generator/data/` のものをそのまま使える。始点と終点が 5 m 以内なら閉ループとみなして無限に周回し、離れていれば開いた線として終端で止まる。判定結果は起動時に `loop` / `open line` として表示される。

## 3-3. よく使う形
```yaml
# 回避練習用の停止車を 2 台、レースライン上に離して置く
objects:
  - {id: d5, mode: static, s_m: 120.0}
  - {id: d8, mode: static, s_m: 260.0}

# 追い越し練習用。低速で周回させる
objects:
  - {id: d10, mode: raceline, speed_mps: 3.0, start_s_m: 40.0}

# コース外に置いて「受信はするが走行に影響しない」確認をする
objects:
  - {id: d11, mode: static, x: 3700.0, y: 73650.0}
```

---

# 第4部 コマンドラインオプション
| オプション | 意味 |
| ---------- | ---- |
| `--scenario PATH` | シナリオ YAML（必須） |
| `--only d5,d8` | シナリオのうち指定した ID だけを出す。1 台ずつ切り分けるときに使う |
| `--host` / `--port` / `--certs-dir` | シナリオの `broker.*` を上書きする |
| `--no-tls` | 平文にする（`certs_dir` も無効化） |
| `--rate` | `rate_hz` を上書きする |
| `--duration SEC` | 指定秒で自動停止する。既定は Ctrl-C まで無限 |
| `--transport auto\|paho\|mosquitto_pub` | MQTT クライアントの選択。既定 `auto` |
| `--dry-run` | broker へ繋がず payload を標準出力に出す |
| `--skip-cert-check` | 証明書 CN の照合を飛ばす（`--acl-mode open` の broker 専用） |
| `--quiet` | 1 秒ごとの状態行を出さない |
Ctrl-C（SIGINT）と SIGTERM のどちらでも、接続を閉じてから送信数を報告して終わる。

---

# 第5部 トラブルシュート
| 症状 | 原因 | 対処 |
| ---- | ---- | ---- |
| `certificate file(s) missing` | `certs_dir` か ID のディレクトリ名が違う | `issue-kart-cert.sh` の出力先を確認する。`certs_dir` の相対パスはリポジトリルート基準 |
| `the certificate's CN is 'dN'` | ID と証明書が食い違っている | その ID の証明書を使う。broker が `--acl-mode open` なら `--skip-cert-check` |
| 状態行が `[down]` のまま | 8883 に出られない / ホスト名が証明書と不一致 | `openssl s_client -connect <host>:8883 -CAfile ca.crt` で切り分ける |
| `[exit 1]`（mosquitto_pub 使用時） | 認証・ACL 拒否 | 同じ引数を単発の `mosquitto_pub` で叩いてメッセージを読む |
| broker では見えるが実車で見えない | 実車の `V2X_VEHICLE_IDS` に ID が無い | 起動時に表示される `V2X_VEHICLE_IDS=…` を実車へ設定して再起動する |
| 自号 ID と同じ仮想オブジェクトが見えない | 仕様どおりの自号除外（R5.2.4） | ID を変える |
| 位置がトラックから外れている | トラックとレースライン CSV の不一致 | `kashiwanoha` なら `data/kashiwanoha/` 配下の CSV を使う（[`kashiwanoha-track.md`](./kashiwanoha-track.md)） |
broker 側で誰が何を出しているかを直接見るのが最も早い。
```bash
CERTS=aichallenge-aws/cloudformation_templates/v2x-mqtt-broker/kart-certs/dev/d5
mosquitto_sub -h v2x-mqtt.dev.aichallenge-board.jsae.or.jp -p 8883 \
  --cafile $CERTS/ca.crt --cert $CERTS/kart.crt --key $CERTS/kart.key \
  -t 'v2x/vehicles/+/position' -v
```

---

# 第6部 中身
| ファイル | 役割 |
| -------- | ---- |
| `vehicle/v2x_virtual_objects.py` | CLI・MQTT 送信・事前確認・送信ループ |
| `vehicle/v2x_virtual_objects_core.py` | シナリオ検証・レースライン幾何・payload 生成（純ロジック） |
| `vehicle/tests/v2x_virtual_objects_core_test.py` | 上記の単体テスト |
| `vehicle/v2x-scenarios/*.yaml` | シナリオ |
`_core.py` は MQTT・sleep・ファイルアクセスを持たないので、broker もトラックも無しにテストできる。
```bash
cd vehicle && python3 -m unittest discover -s tests -p "v2x_virtual_objects_core_test.py"
```
