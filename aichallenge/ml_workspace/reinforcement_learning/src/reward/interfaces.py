from abc import ABC, abstractmethod
from context.context_types import StepContext


class RewardFunction(ABC):
    """
    StepContext から報酬を計算する。
    """
    def reset(self) -> None:
        return None

    @abstractmethod
    def compute(self, context: StepContext) -> tuple[float, StepContext]:
        raise NotImplementedError
