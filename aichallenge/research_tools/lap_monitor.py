"""Subscribe /awsim/status and print lap times. Usage: python3 lap_monitor.py [duration_sec]"""
import sys
import time
import rclpy
from std_msgs.msg import Float32MultiArray

def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    rclpy.init()
    node = rclpy.create_node("lap_monitor")
    st = {"lap": None, "t": 0.0, "sec": None}
    def cb(msg):
        d = msg.data
        lap, t, sec = int(d[1]), d[2], int(d[3])
        if st["lap"] is not None and lap != st["lap"]:
            print(f"LAP {st['lap']} done: {st['t']:.2f} s", flush=True)
        st.update(lap=lap, t=t, sec=sec)
    node.create_subscription(Float32MultiArray, "/awsim/status", cb, 10)
    end = time.time() + dur
    while time.time() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
    print(f"END: lap={st['lap']} section={st['sec']} current_lap_time={st['t']:.2f} s", flush=True)

main()
