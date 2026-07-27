from __future__ import annotations

from context.context_types import StepContext
from reward.interfaces import RewardFunction


class DefaultAWSIMReward(RewardFunction):
    def __init__(
        self,
        speed_reward_scale: float = 0.1,
        step_time_penalty: float = 0.05,
        collision_penalty: float = 50.0,
    ) -> None:
        self.speed_reward_scale = speed_reward_scale
        self.step_time_penalty = step_time_penalty
        self.collision_penalty = collision_penalty

    def compute(self, context: StepContext,) -> tuple[float, StepContext]:
        speed = float(context.env_state.get_value("vehicle_speed_mps", 0.0))
        speed_reward = self.speed_reward_scale * max(0.0, speed)
        collision_penalty = self.collision_penalty if context.collision else 0.0

        reward = (
            speed_reward
            - collision_penalty
            - self.step_time_penalty
        )

        context.info["reward_breakdown"] = {
            "speed_reward": speed_reward,
            "time_penalty": -self.step_time_penalty,
            "collision_penalty": -collision_penalty,
            "total_reward": reward,
        }
        return reward, context
