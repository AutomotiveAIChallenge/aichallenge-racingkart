#!/usr/bin/env python3
"""joy トピックの遅延を測る（遠隔PC → EC2 zenoh router → 車両）。

同一 PC 上に遠隔側ブリッジ（domain 0）と車両側ブリッジ（domain 61, ``-n /A2``）を立て、
両方を EC2 router の同じポートへ繋いだ状態で使う。**送信と受信を 1 プロセスで行うため
すべての時刻が同一クロックから得られ、車両間のクロック誤差が測定値に混入しない。**
代わりに経路は PC → EC2 → PC の往復になる。

ドメインを分けることが要点である。分けないと 2 本のブリッジが DDS で直接つながり、
EC2 を経由せずに joy が届いてしまうため測定にならない。

計測点:

  t0  domain tx  publish 直前                    送信
  t1  domain tx  /<VID>/racing_kart/joy          manager の変換完了（--via-manager のみ）
  t2  domain rx  /racing_kart/joy                車両 DDS に出た

  transform = t1 - t0   manager の変換（--via-manager のみ）
  zenoh     = t2 - t1   DDS -> bridge -> EC2 -> bridge -> DDS の往復
  end-to-end= t2 - t0

照合キーは ``header.stamp`` の ns 値。manager は stamp を引き継ぎ
（racing_kart_manager_core.transform、test_transform.py の「stamp を保つ」）、zenoh は
CDR をそのまま通すので ns 分解能が経路上で失われない。

**測定を続けて回すときは 30 秒ほど間隔を空けること。** 前のプロセスが終わった直後に
次を起動すると、ブリッジが古い subscriber の undeclare を処理しきる前に新しい
subscriber が現れ、ルートが張られないまま 1 件も届かないことがある。ルート自体は
本来 0.3 秒で立つ（測定開始時に「最初の受信まで」として出る）。

QoS は実運用と揃えてある。driver 側は ``rclcpp::QoS(1)``
（racing_kart_driver_node.cpp:68）、manager 側も depth=1 なので、ここも depth=1 の
RELIABLE を使う。ブリッジが張るルートの性質を実運用と一致させるため。
"""

import argparse
import math
import os
import statistics
import threading
import time

from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import Joy

# racing_kart_manager_core.py の複製。ペイロードの大きさを実運用と揃えるために要る。
NUM_AXES = 8
NUM_BUTTONS = 11
AXIS_STEER = 0
#: 無操作の実値。アクセル・ブレーキは 0.0 ではなく +1.0（driver が (1-x)/2 で読む）。
NO_INPUT_AXES = (0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

#: driver / manager と同じ。depth を増やすとブリッジの張るルートが実運用と変わる。
JOY_QOS = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
)

STAGES = [
    ("transform", "t0", "t1"),
    ("zenoh", "t1", "t2"),
    ("end-to-end", "t0", "t2"),
]


def stamp_key(stamp):
    """ns 値そのもの。経路上で失われる桁が無いので切り捨てない。"""
    return stamp.sec * 1_000_000_000 + stamp.nanosec


class Samples:
    """joy 1 通ごとの時刻。飛行中のあいだだけ保持する。"""

    def __init__(self, retention_sec):
        self.lock = threading.Lock()
        self.pending = {}
        self.done = []
        self.late = 0
        self.retention_sec = retention_sec

    def mark(self, key, field, moment):
        with self.lock:
            entry = self.pending.get(key)
            if entry is None:
                if field != "t0":
                    # t0 を見ていないサンプル（測定開始前に飛んでいたもの）。
                    self.late += 1
                    return
                entry = {}
                self.pending[key] = entry
            if field in entry:
                return  # 最初の 1 回だけを採る
            entry[field] = moment
            if field == "t2":
                self.done.append(self.pending.pop(key))

    def sweep(self):
        """完了しないサンプルを退役させる。

        t1 まで来て t2 が無いものは、ブリッジが zenoh へ流さなかった（間引き）か
        経路で落ちたかのどちらか。ここでは区別できないので lost として数え、
        間引きの有無は送信数と受信数の比で別に判断する。
        """
        deadline = time.time() - self.retention_sec
        with self.lock:
            stale = [k for k, e in self.pending.items() if e.get("t0", 0.0) < deadline]
            for key in stale:
                self.pending.pop(key)
            return len(stale)

    def take_done(self):
        with self.lock:
            done, self.done = self.done, []
            return done


class Sender(Node):
    """遠隔側ドメインで joy を一定レートで流し、必要なら manager の出力も観測する。"""

    def __init__(self, context, samples, args, counters):
        super().__init__(f"joy_latency_sender_{os.getpid()}", context=context)
        self.samples = samples
        self.counters = counters
        self.args = args

        vehicle_topic = f"/{args.vehicle_id}/racing_kart/joy"
        send_topic = "/racing_kart/joy" if args.via_manager else vehicle_topic
        self.publisher = self.create_publisher(Joy, send_topic, JOY_QOS)
        if args.via_manager:
            # manager の出力。ここが zenoh へ入る点になる。
            self.create_subscription(Joy, vehicle_topic, self.on_manager_output, JOY_QOS)

        self.phase = 0.0
        self.step = 2.0 * math.pi / max(args.rate * 4.0, 1.0)
        self.create_timer(1.0 / args.rate, self.on_tick)
        self.get_logger().info(f"publishing {send_topic} at {args.rate} Hz")

    def on_tick(self):
        axes = list(NO_INPUT_AXES)
        # ステアだけ振る。実操作に近い変化を与えつつ、無操作判定の側は壊さない。
        axes[AXIS_STEER] = math.sin(self.phase)
        self.phase += self.step

        message = Joy()
        message.axes = [float(a) for a in axes]
        message.buttons = [0] * NUM_BUTTONS

        moment = time.time()
        message.header.stamp.sec = int(moment)
        message.header.stamp.nanosec = int((moment - int(moment)) * 1e9)
        key = stamp_key(message.header.stamp)
        self.publisher.publish(message)
        self.samples.mark(key, "t0", moment)
        if not self.args.via_manager:
            # manager を挟まないので、送信した瞬間が zenoh へ入る点でもある。
            self.samples.mark(key, "t1", moment)
        self.counters["sent"] += 1

    def on_manager_output(self, message):
        self.samples.mark(stamp_key(message.header.stamp), "t1", time.time())
        self.counters["manager"] += 1


class Receiver(Node):
    """車両側ドメインで joy を受ける。ここに subscriber が居ることがルートの条件でもある。"""

    def __init__(self, context, samples, counters):
        super().__init__(f"joy_latency_receiver_{os.getpid()}", context=context)
        self.samples = samples
        self.counters = counters
        self.first_received = None
        self.create_subscription(Joy, "/racing_kart/joy", self.on_joy, JOY_QOS)

    def on_joy(self, message):
        moment = time.time()
        if self.first_received is None:
            self.first_received = moment
        self.samples.mark(stamp_key(message.header.stamp), "t2", moment)
        self.counters["received"] += 1


def spin_in_thread(context, node):
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    return executor


def summarize(values):
    """ms の統計。V2X レポートと同じ並びで出す。"""
    if not values:
        return None
    ordered = sorted(v * 1e3 for v in values)
    count = len(ordered)

    def percentile(fraction):
        return ordered[min(count - 1, int(count * fraction))]

    deviation = statistics.stdev(ordered) if count > 1 else 0.0
    return {
        "n": count,
        "mean": statistics.mean(ordered),
        "sd": deviation,
        "var": deviation**2,
        "min": ordered[0],
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def format_row(name, stats):
    if stats is None:
        return f"{name:12s} no data"
    return (
        f"{name:12s} n={stats['n']:5d}  mean {stats['mean']:7.1f}  SD {stats['sd']:6.1f}  "
        f"min {stats['min']:7.1f}  p50 {stats['p50']:7.1f}  p95 {stats['p95']:7.1f}  "
        f"p99 {stats['p99']:7.1f}  max {stats['max']:7.1f} ms"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vehicle-id", default="A2")
    parser.add_argument("--tx-domain", type=int, default=0, help="遠隔側ブリッジのドメイン")
    parser.add_argument("--rx-domain", type=int, default=61, help="車両側ブリッジのドメイン")
    parser.add_argument("--rate", type=float, default=20.0, help="[Hz] 送信レート")
    parser.add_argument("--duration", type=float, default=120.0, help="[s] 0 で無限")
    parser.add_argument("--interval", type=float, default=10.0, help="[s] 途中経過の間隔")
    parser.add_argument("--retention", type=float, default=5.0, help="[s] 飛行中の打ち切り")
    parser.add_argument(
        "--warmup",
        type=float,
        default=15.0,
        help="[s] 統計に入れない助走。ブリッジが車両側 subscriber を見つけてルートを"
        "張り直すまで joy は届かない。これを含めて測ると到達率が実際より低く出る",
    )
    parser.add_argument(
        "--via-manager",
        action="store_true",
        help="racing_kart_manager を経由させ、transform 区間も分解する",
    )
    parser.add_argument("--csv", help="1 サンプル 1 行で書き出す（作図用）")
    args = parser.parse_args()

    samples = Samples(args.retention)
    counters = {"sent": 0, "manager": 0, "received": 0}

    tx_context = Context()
    tx_context.init(domain_id=args.tx_domain)
    rx_context = Context()
    rx_context.init(domain_id=args.rx_domain)

    receiver = Receiver(rx_context, samples, counters)
    sender = Sender(tx_context, samples, args, counters)
    rx_executor = spin_in_thread(rx_context, receiver)
    tx_executor = spin_in_thread(tx_context, sender)

    warm_started = time.time()
    route = "manager -> " if args.via_manager else ""
    print(
        f"measuring {args.vehicle_id}: domain {args.tx_domain} -> {route}"
        f"zenoh (EC2) -> domain {args.rx_domain}, "
        f"{args.rate} Hz, {args.duration or 'endless'} s",
        flush=True,
    )

    if args.warmup > 0.0:
        # ルートが立つのを待つ。測定プロセスを起動し直すたびに、車両側ドメインの
        # subscriber がいったん消えるためブリッジはルートを閉じる。張り直しを待たずに
        # 数え始めると、その間の送信がすべて損失に見える。
        print(f"warming up {args.warmup:.0f} s ...", flush=True)
        time.sleep(args.warmup)
        samples.sweep()
        samples.take_done()
        if receiver.first_received is None:
            print(
                "  警告: 助走のあいだ 1 件も届いていない。ブリッジのログを確認すること",
                flush=True,
            )
        else:
            print(
                f"  最初の受信まで {receiver.first_received - warm_started:.1f} s"
                "（ブリッジがルートを張るまでの時間）",
                flush=True,
            )
        for key in counters:
            counters[key] = 0

    collected = {name: [] for name, _, _ in STAGES}
    dropped = 0
    started = time.time()
    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "w", encoding="utf-8")
        # epoch を入れておくと probe_link.py の記録と時刻で突き合わせられる。
        csv_file.write("epoch,elapsed,transform,zenoh,end_to_end\n")
    try:
        while args.duration <= 0.0 or time.time() - started < args.duration:
            time.sleep(args.interval)
            dropped += samples.sweep()
            for entry in samples.take_done():
                for name, first, last in STAGES:
                    if first in entry and last in entry:
                        collected[name].append(entry[last] - entry[first])
                if csv_file is not None and "t1" in entry:
                    csv_file.write(
                        f"{entry['t0']:.4f},"
                        f"{entry['t0'] - started:.4f},"
                        f"{(entry['t1'] - entry['t0']) * 1e3:.3f},"
                        f"{(entry['t2'] - entry['t1']) * 1e3:.3f},"
                        f"{(entry['t2'] - entry['t0']) * 1e3:.3f}\n"
                    )
            elapsed = time.time() - started
            stats = summarize(collected["end-to-end"][-200:])
            recent = "no data" if stats is None else (
                f"p50 {stats['p50']:6.1f}  p95 {stats['p95']:6.1f} ms"
            )
            print(
                f"[{elapsed:6.1f}s] sent={counters['sent']} received={counters['received']} "
                f"dropped={dropped}  e2e(直近) {recent}",
                flush=True,
            )
    except KeyboardInterrupt:
        pass

    print("\n=== 遅延の内訳 ===", flush=True)
    for name, _, _ in STAGES:
        if name == "transform" and not args.via_manager:
            continue
        print(format_row(name, summarize(collected[name])), flush=True)

    sent = counters["sent"]
    received = counters["received"]
    print("\n=== 受信レート ===", flush=True)
    print(f"送信 {sent} 件 / 受信 {received} 件", flush=True)
    if sent:
        ratio = received / sent
        effective = received / max(time.time() - started, 1e-9)
        print(
            f"到達率 {ratio * 100:.2f} %  実効受信レート {effective:.1f} Hz "
            f"(送信 {args.rate:.1f} Hz)",
            flush=True,
        )
        # pub_max_frequencies が効いていれば、送信レートを上げても受信レートは
        # 上限で頭打ちになる。効いていなければ送信レートにそのまま追随する。
        if ratio < 0.9:
            print(
                "  → 到達率が低い。ブリッジの間引き（pub_max_frequencies）か経路の損失。"
                "送信レートを変えて受信レートが頭打ちになるか確認すること",
                flush=True,
            )
    if samples.late:
        print(f"t0 を見ていない受信 {samples.late} 件（測定開始前の残り）", flush=True)

    if csv_file is not None:
        csv_file.close()
        print(f"\nwrote {args.csv}", flush=True)

    tx_executor.shutdown()
    rx_executor.shutdown()
    sender.destroy_node()
    receiver.destroy_node()
    tx_context.try_shutdown()
    rx_context.try_shutdown()


if __name__ == "__main__":
    main()
