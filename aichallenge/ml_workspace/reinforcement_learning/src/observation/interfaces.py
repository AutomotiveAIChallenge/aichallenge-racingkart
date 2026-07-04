from abc import ABC, abstractmethod
from typing import Any

from gymnasium import spaces

from context.context_types import StepContext


class ObservationBuilder(ABC):
    """
    StepContext から agent に渡す観測を構築する。
    """

    def reset(self) -> None:
        return None

    @property
    @abstractmethod
    def observation_space(self) -> spaces.Space:
        """builder が返す観測の Gymnasium Space。"""
        raise NotImplementedError

    @abstractmethod
    def build(self, context: StepContext,) -> tuple[Any, StepContext]:
        raise NotImplementedError
