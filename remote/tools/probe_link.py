#!/usr/bin/env python3
"""joy の遅延測定と並行して、回線側の状態を 1 秒刻みで記録する。

遅延が伸びて同時に受信レートも落ちたとき、原因が経路（LTE 区間）なのか
router なのかを切り分けるために使う。

zenoh は TCP/TLS の上で動き、輻輳制御は Drop に設定されている
（ブリッジのログの reliable_routes_blocking: false）。したがって経路で
パケットが落ちても TCP が再送するので、アプリ層にはロスではなく遅延として
現れる。アプリ層でメッセージが消えるのは、送信キューが詰まって zenoh 自身が
捨てたときである。つまり:

  劣化の瞬間に TCP の retrans が跳ねている  -> 経路（LTE 区間）が原因
  retrans は平常なのにメッセージだけ消える  -> router かブリッジが原因

ping も併記するが、ICMP は LTE 網で TCP と違う扱いを受けることがあるため
補助的な指標として見る。

  ./probe_link.py --host zenoh.dev.aichallenge-board.jsae.or.jp --port 7448 \
      --duration 300 --csv output/joy-latency/data/link.csv
"""

import argparse
import re
import socket
import subprocess
import time

#: ss -ti の 2 行目から拾う項目。rtt は "rtt:95.5/12.3" の形で平均/ばらつき。
RETRANS = re.compile(r"retrans:\d+/(\d+)")
RTT = re.compile(r"\brtt:([\d.]+)/([\d.]+)")
CWND = re.compile(r"\bcwnd:(\d+)")
PORT_OF = re.compile(r":(\d+)\s+[\d.]+:(\d+)\s*$")


def sample_sockets(address, port):
    """対象へ張られている TCP コネクションごとに (ローカルポート, 統計) を返す。"""
    try:
        output = subprocess.run(
            ["ss", "-tin", "dst", address, "dport", f"= :{port}"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    result = {}
    local_port = None
    for line in output.splitlines():
        if line.startswith("ESTAB") or "ESTAB" in line[:20]:
            match = PORT_OF.search(line.rstrip())
            local_port = match.group(1) if match else None
            continue
        if local_port is None:
            continue
        retrans = RETRANS.search(line)
        rtt = RTT.search(line)
        cwnd = CWND.search(line)
        if rtt or retrans:
            result[local_port] = {
                "retrans": int(retrans.group(1)) if retrans else 0,
                "rtt": float(rtt.group(1)) if rtt else float("nan"),
                "rttvar": float(rtt.group(2)) if rtt else float("nan"),
                "cwnd": int(cwnd.group(1)) if cwnd else 0,
            }
            local_port = None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="zenoh.dev.aichallenge-board.jsae.or.jp")
    parser.add_argument("--port", type=int, default=7448)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--csv", required=True)
    args = parser.parse_args()

    address = socket.gethostbyname(args.host)
    print(f"probing {args.host} ({address}):{args.port} for {args.duration:.0f} s", flush=True)

    # ping は別プロセスで流しっぱなしにし、1 行ずつ読む。
    ping = subprocess.Popen(
        ["ping", "-i", "1", "-O", address],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    handle = open(args.csv, "w", encoding="utf-8")
    handle.write("epoch,elapsed,socket,retrans_total,retrans_delta,rtt_ms,rttvar_ms,cwnd\n")

    previous = {}
    started = time.time()
    try:
        while time.time() - started < args.duration:
            now = time.time()
            moment = now - started
            for local_port, stats in sample_sockets(address, args.port).items():
                before = previous.get(local_port, stats["retrans"])
                handle.write(
                    f"{now:.2f},{moment:.2f},{local_port},{stats['retrans']},"
                    f"{stats['retrans'] - before},{stats['rtt']:.2f},"
                    f"{stats['rttvar']:.2f},{stats['cwnd']}\n"
                )
                previous[local_port] = stats["retrans"]
            handle.flush()
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        handle.close()
        ping.terminate()
        try:
            remainder = ping.communicate(timeout=3)[0]
        except subprocess.TimeoutExpired:
            ping.kill()
            remainder = ""
        # ping の要約（送信/受信/損失）だけ残す。1 行ごとの RTT は TCP 側で見る。
        for line in (remainder or "").splitlines():
            if "packets transmitted" in line or "rtt min" in line:
                print(line, flush=True)
        print(f"wrote {args.csv}", flush=True)


if __name__ == "__main__":
    main()
