from abc import ABC, abstractmethod

from context.context_types import StepContext


class TerminationFunction(ABC):
    """
    タスクとして本質的に終了したかを判定する。
    例:
      - 衝突
      - ゴール
      - コースアウト
    最大ステップ数は TimeLimit に任せる想定。
    
    Returns:
        Tuple of (is_terminated, updated_context)
        - is_terminated: 終了したか
        - updated_context: 終了判定のために context を更新したい場合に返す。通常は元の context を返せば良い。
    """

    def reset(self) -> None:
        return None

    @abstractmethod
    def is_terminated(self, context: StepContext,) -> tuple[bool, StepContext]:
        raise NotImplementedError
