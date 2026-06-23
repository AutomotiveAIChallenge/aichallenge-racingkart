#!/usr/bin/env python3
"""
AWSIM Gymnasium Environment
"""

import time

import cv2
import gymnasium as gym
import numpy as np
import rclpy

from action.interfaces import ActionAdapter
from context.context_manager import AWSIMContextManager, StepContext
from node.awsim_env_node import AWSIMEnvNode
from observation.interfaces import ObservationBuilder
from reward.interfaces import RewardFunction
from termination.interfaces import TerminationFunction

from dataclasses import dataclass, field
from typing import Any, Optional

from autoware_auto_control_msgs.msg import AckermannControlCommand
from autoware_auto_planning_msgs.msg import Trajectory
from autoware_auto_vehicle_msgs.msg import GearReport, SteeringReport, VelocityReport
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import CameraInfo, NavSatFix, Imu, LaserScan


EPISODE_MAX_STEPS         = 10000   # [step] 1step=0.05s想定で約60秒 #[change]

# ============================================================
# Gymnasium 環境
# ============================================================

class AWSIMEnv(gym.Env):
    """
    AWSIMをバックエンドとしたGymnasium環境

    観測    : {'image': カメラ画像, 'speed': 現在速度}
    行動    : [steering [rad], acceleration [0, 1]] continuous
    報酬    : 速度そのもの + セクション通過ボーナス
    終了    : エピソードステップ数が EPISODE_MAX_STEPS に到達
    リセット: /awsim/reset を publish する
    """

    metadata = {'render_modes': ['human']}

    def __init__(
        self,
        *,
        context_manager: Optional[AWSIMContextManager] = None,
        action_adapter: Optional[ActionAdapter] = None,
        observation_builder: Optional[ObservationBuilder] = None,
        reward_function: Optional[RewardFunction] = None,
        termination_function: Optional[TerminationFunction] = None,
    ):
        super().__init__()

        # --- ROS2ノード初期化 ---
        if not rclpy.ok():
            rclpy.init()
            
        self._node = AWSIMEnvNode()

        self._step_count = 0

        # 外部クラス
        self._context_manager = context_manager
        self._action_adapter = action_adapter
        self._observation_builder = observation_builder
        self._reward_function = reward_function
        self._collision_termination = termination_function

        self.action_space = self._action_adapter.action_space
        self.observation_space = self._observation_builder.observation_space

    # ============================================================
    # Gymnasium API
    # ============================================================

    def step(self, action: np.ndarray):
        """
        1ステップ実行

        Args:
            action: [steering [rad], acceleration [0, 1]]

        Returns:
            obs, reward, terminated, truncated, info
        """
        start = time.perf_counter()

        # 1. action の前処理
        # 現時点では行動空間の拡張はノードのpublish_controlも変わるため不可能
        sim_action = self._action_adapter.adapt(action)
        steering = float(sim_action["steering"])
        acceleration = float(sim_action["acceleration"])
        sim_action = np.array([steering, acceleration], dtype=np.float32)

        # 2. action を AWSIM へ送信
        # 将来的にはこのノードだけ拡張をもたせるべき
        self._node.publish_alt_control(steering, acceleration)

        # 3. ROS2 メッセージを受信（最新画像になるまで待機）
        self._wait_for_fresh_image()

        # 4. step count更新
        self._step_count += 1

        # 5. context更新
        ctx = self._context_manager.update(
            node=self._node,
            step_count=self._step_count,
            agent_action=action,
            sim_action=sim_action,
        )

        # 6. observation生成
        obs, ctx = self._observation_builder.build(ctx)

        # 7. 終了判定
        terminated, ctx = self._collision_termination.is_terminated(ctx)

        # 8. 報酬計算
        reward, ctx = self._reward_function.compute(ctx)

        # 9. info生成
        info = self._build_info_from_context(ctx)

        elapsed = time.perf_counter() - start
        
        return obs, reward, terminated, False, info


    def reset(self, seed=None, options=None):
        """
        エピソードリセット

        /awsim/state が 'FinishALL' または 'wait' になるまで待機し、
        /awsim/reset を publish して初期状態に戻す
        """
        super().reset(seed=seed)

        # 1. 外部クラスのリセット
        self._context_manager.reset()
        self._collision_termination.reset()
        self._action_adapter.reset()
        self._observation_builder.reset()
        self._reward_function.reset()
        
        # 2. step countリセット
        self._step_count = 0
    
        # 3. AWSIM がリセット可能な状態になるまで待機
        self._node.get_logger().info("Waiting for AWSIM resettable state...")
        # self._wait_for_resettable_state()

        # 4. リセットをpublish
        self._node.call_reset()
        self._node.get_logger().info("Reset published.")

        # 5. AWSIM初期化待機（spinしながら待つことでcallbackを処理する）. 
        # ここは将来的に、車両の姿勢とかで判断したい。simが始まってから動き出せるまで時間がかかる。ここをどう判断するか。
        self._node.sensing_camera_image_raw = None  # 古い画像をクリア
        self._wait_for_fresh_image(timeout_sec=3.0, spin_timeout_sec=0.1)

        # 6. context更新
        ctx = self._context_manager.update(
            node=self._node,
            step_count=self._step_count,
            agent_action=None,
            sim_action=None,
        )

        # 7. observation生成
        obs, ctx = self._observation_builder.build(ctx)
        
        # 8. info生成
        info = self._build_info_from_context(ctx)

        return obs, info

    def close(self):
        self._node.destroy_node()
        rclpy.shutdown()

    def render(self):
        if self._node.sensing_camera_image_raw is not None:
            img_bgr = cv2.cvtColor(self._node.sensing_camera_image_raw, cv2.COLOR_RGB2BGR)
            cv2.imshow("AWSIM Camera", img_bgr)
            cv2.waitKey(1)

    # ============================================================
    # 内部メソッド（reset・info関連）
    # ============================================================

    def _wait_for_fresh_image(
        self,
        timeout_sec: float = 0.2,
        spin_timeout_sec: float = 0.01,
    ) -> None:
        """カメラ画像が更新されるまで spin しながら待機する。"""
        prev_image = self._node.sensing_camera_image_raw
        deadline = time.time() + timeout_sec

        while self._node.sensing_camera_image_raw is prev_image:
            rclpy.spin_once(self._node, timeout_sec=spin_timeout_sec)
            if time.time() > deadline:
                self._node.get_logger().warn("image timeout")
                break

    def _wait_for_resettable_state(self, timeout_sec: float = 120.0):
        """/awsim/state が 'FinishALL' または 'wait' になるまで待機。必要ならここに追加状態を増やせる。"""
        start = time.time()
        while True:
            rclpy.spin_once(self._node, timeout_sec=0.1)
            state_msg = self._node.awsim_state
            state = state_msg.data if state_msg is not None else ""
            if state in ('FinishALL', 'wait'):
                self._node.get_logger().info(f"AWSIM state='{state}' -> resettable.")
                break
            if time.time() - start > timeout_sec:
                self._node.get_logger().warn(
                    f"Timeout waiting for resettable state. current='{state}'"
                )
                break
            time.sleep(0.1)

    def _build_info_from_context(self, ctx: StepContext) -> dict:
        """StepContext から info dict を構築する。"""
        speed = float(ctx.env_state.get_value("vehicle_speed_mps", 0.0))

        session_time = float(ctx.env_state.get_value("awsim_session_time_s", 0.0))
        lap_count = int(ctx.env_state.get_value("awsim_lap_count", 0.0))
        lap_time = float(ctx.env_state.get_value("awsim_lap_time_s", 0.0))
        section = int(ctx.env_state.get_value("awsim_section", 0.0))
        time_scale = float(ctx.env_state.get_value("awsim_time_scale", 1.0))
        boost_remaining = float(ctx.env_state.get_value("awsim_boost_remaining", 0.0))
        is_boosting = bool(ctx.env_state.get_value("awsim_is_boosting", 0.0))
        awsim_state = str(ctx.env_state.get_value("awsim_state", ""))

        info = {
            "speed": speed,
            "session_time": session_time,
            "lap_count": lap_count,
            "lap_time": lap_time,
            "section": section,
            "time_scale": time_scale,
            "boost_remaining": boost_remaining,
            "is_boosting": is_boosting,
            "awsim_state": awsim_state,
            "awsim_status": {
                "session_time": session_time,
                "lap_count": lap_count,
                "lap_time": lap_time,
                "section": section,
                "time_scale": time_scale,
                "boost_remaining": boost_remaining,
                "is_boosting": is_boosting,
            },
            "collision": ctx.collision,
            "collision_count": ctx.collision_count,
            "section_changed": ctx.section_changed,
            "lap_completed": ctx.lap_completed,
        }
        info.update(ctx.info)
        return info
