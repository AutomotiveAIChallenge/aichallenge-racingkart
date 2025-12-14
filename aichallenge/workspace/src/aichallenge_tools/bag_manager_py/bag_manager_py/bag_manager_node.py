#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from std_srvs.srv import Trigger 
import subprocess
import datetime
import signal
import os
import threading

class RosBagManagerNode(Node):
    def __init__(self):
        super().__init__('ros2_bag_manager_node')

        # === パラメータ宣言・取得 ===
        self.declare_parameter('output_dir', 'rosbag2_output')
        self.declare_parameter('all_topics', True)
        self.declare_parameter('topics', ['/rosbag2_recorder/trigger'])
        self.declare_parameter('storage_id', 'mcap') 
        
        self.output_dir = self.get_parameter('output_dir').get_parameter_value().string_value
        self.all_topics = self.get_parameter('all_topics').get_parameter_value().bool_value
        self.topics = list(self.get_parameter('topics').get_parameter_value().string_array_value)
        self.storage_id = self.get_parameter('storage_id').get_parameter_value().string_value

        now = datetime.datetime.now()
        session_ts = now.strftime('%Y%m%d_%H%M%S')
        self.session_dir = os.path.join(self.output_dir, session_ts)
        os.makedirs(self.session_dir, exist_ok=True)
        self.get_logger().info(f"セッションディレクトリ作成: {self.session_dir}")

        self.trigger_sub = self.create_subscription(
            Bool,
            '/rosbag2_recorder/trigger',
            self.trigger_callback,
            10
        )

        self.status_pub = self.create_publisher(Bool, '~/status', 10)

        self.start_srv = self.create_service(
            Trigger, 
            '~/start_recording', 
            self.start_recording_callback
        )
        self.stop_srv = self.create_service(
            Trigger, 
            '~/stop_recording', 
            self.stop_recording_callback
        )

        self.recording_process = None
        self.is_recording = False
        self.lock = threading.Lock() # 排他制御用ロック
        
        # 起動時に現在のステータス (False) を一度発行する
        self.status_pub.publish(Bool(data=self.is_recording))

    def start_recording_callback(self, request, response):
        """録画開始サービス コールバック"""
        success, message = self._start_recording_logic()
        response.success = success
        response.message = message
        return response

    def stop_recording_callback(self, request, response):
        """録画停止サービス コールバック"""
        success, message = self._stop_recording_logic()
        response.success = success
        response.message = message
        return response

    def trigger_callback(self, msg: Bool):
        """録画トリガートピック コールバック"""
        if msg.data:
            # 録画開始
            success, message = self._start_recording_logic()
            if not success:
                self.get_logger().warn(f"トリガーによる録画開始失敗: {message}")
        else:
            # 録画停止
            success, message = self._stop_recording_logic()
            if not success:
                self.get_logger().warn(f"トリガーによる録画停止失敗: {message}")

    def _start_recording_logic(self):
        """録画開始の共通ロジック (排他制御あり)"""
        with self.lock:
            if self.is_recording:
                self.get_logger().warn("すでに録画中です。新しい録画は開始しません。")
                return False, "Already recording"

            # === 録画開始処理 ===
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            record_dir = os.path.join(self.session_dir, ts)
            
            cmd = ['ros2', 'bag', 'record']
            if self.all_topics:
                cmd.append('-a')
            elif self.topics:
                cmd.extend(self.topics)
            else:
                self.get_logger().warn("all_topics=false かつ topics=[] のため、何も録画しません")
                return False, "No topics to record"

            cmd.extend(['-o', record_dir, '-s', self.storage_id])

            command_str = ' '.join(cmd)
            self.get_logger().info(f"[DEBUG] Command to be executed: {command_str}")

            self.get_logger().info(f"録画開始: {command_str}")
            self.recording_process = subprocess.Popen(cmd, preexec_fn=os.setsid)
            self.is_recording = True
    
            self.status_pub.publish(Bool(data=True))
            
            return True, f"Recording started: {record_dir}"

    def _stop_recording_logic(self):
        """録画停止の共通ロジック (排他制御あり)"""
        with self.lock:
            if not self.is_recording:
                self.get_logger().warn("録画は現在行われていません。停止要求は無視されます。")
                return False, "Not recording"

            # === 録画停止処理 (サービス/トピック用) ===
            self.get_logger().info("録画停止要求 (Service/Topic) — プロセス終了を待ちます")
            self._robust_stop_and_wait() 
            
            return True, "Recording stopped"

    def _robust_stop_and_wait(self):
        """録画プロセスを停止し、終了を待つ (Service/Topic用)"""
        if self.recording_process and self.recording_process.poll() is None:
            pgid = os.getpgid(self.recording_process.pid)
            self.get_logger().info(f"録画プロセス (PGID: {pgid}) に SIGINT を送信します。")
            try:
                os.killpg(pgid, signal.SIGINT)
                # タイムアウトを10秒に延長 (bag が大きい場合を考慮)
                self.recording_process.wait(timeout=10.0) 
                self.get_logger().info("録画プロセスは正常に終了しました。")
            
            except subprocess.TimeoutExpired:
                self.get_logger().warn(f"録画プロセス (PGID: {pgid}) が SIGINT 後 10秒で終了しませんでした。SIGTERM を送信します。")
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    self.recording_process.wait(timeout=5.0) # SIGTERM 後は 5 秒待つ
                    self.get_logger().info("録画プロセスは SIGTERM により終了しました。")
                except subprocess.TimeoutExpired:
                    self.get_logger().error(f"録画プロセス (PGID: {pgid}) が SIGTERM 後 5秒で終了しませんでした。SIGKILL を送信します。")
                    os.killpg(pgid, signal.SIGKILL)
                    self.recording_process.wait()
                    self.get_logger().info("録画プロセスは SIGKILL により終了しました。")
                except Exception as e:
                    self.get_logger().error(f"SIGTERM/wait 中に予期せぬエラー: {e}")
            except Exception as e:
                # (例: プロセスがすでに終了していた場合など)
                self.get_logger().warn(f"SIGINT/wait 中にエラー: {e}")
            
            finally:
                # この関数が呼ばれた時点で、プロセスは終了(またはKILL)されているはず
                self.recording_process = None
                self.is_recording = False
                self.status_pub.publish(Bool(data=False))
                self.get_logger().info("録画停止処理 (wait) 完了")
        else:
            # プロセスが存在しないか、すでに終了している
            self.get_logger().info("録画はすでに停止していました。")
            self.recording_process = None
            self.is_recording = False
            self.status_pub.publish(Bool(data=False))

    def destroy_node(self):
        self.get_logger().info("ノードシャットダウン: 録画プロセス停止処理を実行します")
        with self.lock: 
            if self.is_recording and self.recording_process and self.recording_process.poll() is None:
                # プロセスがまだ動いている場合
                self.get_logger().info("録画プロセスに SIGINT を送信します (wait しません)")
                try:
                    pgid = os.getpgid(self.recording_process.pid)
                    os.killpg(pgid, signal.SIGINT)
                except Exception as e:
                    self.get_logger().error(f"シャットダウン中の SIGINT 送信に失敗: {e}")
                
                self.is_recording = False
                self.recording_process = None
            
            elif self.is_recording:
                # is_recording=True なのにプロセスが存在しない場合
                self.get_logger().warn("録画中フラグが立っていましたが、プロセス実体が存在しませんでした。")
                self.is_recording = False

        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RosBagManagerNode()
    
    try:
        # spin() は Ctrl+C を受け取ると KeyboardInterrupt を発生させる
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("KeyboardInterrupt (SIGINT) 受信 — シャットダウンします")
    except Exception as e:
        node.get_logger().error(f"rclpy.spin() 中に予期せぬエラー: {e}")
    finally:
        # rclpy.spin() が終了した (Ctrl+C or other error)
        if rclpy.ok():
            node.get_logger().info("Spin 終了。ノードを破棄します。")
            node.destroy_node() 
            rclpy.shutdown()
        node.get_logger().info("シャットダウン完了")


if __name__ == '__main__':
    main()
