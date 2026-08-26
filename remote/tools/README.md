# joy 遅延測定ツール

遠隔操作 PC から EC2 の zenoh router を経由して車両へ届くまでの、`joy` トピックの
遅延を測る。測定結果は [docs/joy-latency-test.md](../../docs/joy-latency-test.md)。

## 考え方

1 台の PC 上に遠隔側ブリッジ（`ROS_DOMAIN_ID` 0）と車両側ブリッジ（`ROS_DOMAIN_ID` 61,
`-n /A2`）を両方立て、どちらも EC2 router の同じポートへ繋ぐ。**送信と受信を 1 プロセスで
行うため、両方の時刻が同一クロックから得られる。** 実車 2 台では NTP の同期誤差
（数 ms〜数十 ms）が遅延の測定値と区別できないが、この構成ならそれが混入しない。
代わりに経路は PC から EC2 を往復する。

**ドメインを分けることが要点。** 分けないと 2 本のブリッジが DDS で直接つながり、
EC2 を経由せずに joy が届いてしまうため測定にならない。既定を 61 にしてあるのは、
51/52 が V2X の 2 車両模擬で埋まっていることがあるため。

## 使い方

```bash
# 1) 両ブリッジを起動（前景で動き続ける。Ctrl+C で両方畳む）
./remote/tools/run_joy_bridges.sh -v A2 -d 61

# 2) 別シェルで測定
source /opt/ros/humble/setup.bash
python3 remote/tools/measure_joy_latency.py \
    --rate 20 --duration 300 --warmup 30 \
    --csv output/joy-latency/data/joy_e2e.csv

# 3) 作図
uv run remote/tools/plot_joy_latency.py \
    --csv output/joy-latency/data/joy_e2e.csv --out docs/images
```

`run_joy_bridges.sh` の主なオプション。

| 指定 | 意味 |
| --- | --- |
| `-v A2` | 車両 ID。ポートは `vehicle/vehicle_ports.sh` が決める |
| `-d 61` | 車両側ブリッジのドメイン |
| `-f keep\|off\|<Hz>` | `pub_max_frequencies`。`keep` は現行設定のまま |
| `-e on\|off` | `pub_priorities` の `express` |

## 落とし穴

- **測定を続けて回すときは 30 秒ほど間隔を空ける。** 前のプロセスが終わった直後に次を
  起動すると、ブリッジが古い subscriber の undeclare を処理しきる前に新しい subscriber が
  現れ、ルートが張られないまま 1 件も届かないことがある。`--warmup` はこの待ちも兼ねる。
  ルート自体は本来 0.3 秒で立ち、測定開始時に「最初の受信まで」として表示される
- **指定した車両 ID のポートに割り込む。** 実車がそのポートで走っている時間帯には使わない
- ホストで動かすため、車両側の `vehicle/zenoh.json5` に書かれた mTLS 資材のパス
  （コンテナ内の `/remote`）はスクリプトがリポジトリの `remote/` へ書き換える
