from dataclasses import replace

from context.context_types import StepContext
from termination.interfaces import TerminationFunction


class CollisionTermination(TerminationFunction):
    def __init__(
        self,
        collision_speed_threshold: float,
        collision_count_threshold: int,
        collision_speed_drop_threshold: float = 0.5,
        min_speed_before_drop: float = 1.0,
    ) -> None:
        self.collision_speed_threshold = collision_speed_threshold
        self.collision_count_threshold = collision_count_threshold
        self.collision_speed_drop_threshold = collision_speed_drop_threshold
        self.min_speed_before_drop = min_speed_before_drop
        self._collision_count: int = 0

    def reset(self) -> None:
        self._collision_count = 0

    def is_terminated(self, context: StepContext) -> tuple[bool, StepContext]:
        # エピソード初期は判定を抑制して、リセット直後の不安定挙動で終了しないようにする。
        if context.step_count <= 20:
            self._collision_count = 0
            return False, replace(context, collision=False, collision_count=self._collision_count)

        # 1) 急減速判定（壁衝突を想定）
        prev = context.prev_env_state
        curr_speed = max(0.0, float(context.env_state.get_value("vehicle_speed_mps", 0.0)))
        sudden_drop = False
        if prev is not None:
            prev_speed = max(0.0, float(prev.get_value("vehicle_speed_mps", 0.0)))
            speed_drop = prev_speed - curr_speed
            sudden_drop = (
                prev_speed >= self.min_speed_before_drop
                and speed_drop >= self.collision_speed_drop_threshold
            )

        # 2) 既存の低速連続判定（フォールバック）
        low_speed = curr_speed < self.collision_speed_threshold
        if low_speed:
            self._collision_count += 1
        else:
            self._collision_count = 0

        collision = sudden_drop or (
            self._collision_count >= self.collision_count_threshold
        )

        # contextをコピーしてcollisionフラグを更新
        updated_context = replace(context, collision=collision, collision_count=self._collision_count)

        return collision, updated_context
