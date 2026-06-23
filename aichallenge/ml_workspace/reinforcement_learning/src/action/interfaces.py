from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from gymnasium import spaces


class ActionAdapter(ABC):
    """
    Agent が出した action を、環境が実行可能な action に変換する。
    例:
      [-1, 1] の正規化 action
        -> steering/acceleration の実制御値
    """

    def reset(self) -> None:
        """エピソード開始時に内部状態を初期化したい場合に使う。"""
        return None

    @property
    @abstractmethod
    def action_space(self) -> spaces.Space:
        """agent が出力する action の Gymnasium Space。"""
        raise NotImplementedError

    @abstractmethod
    def adapt(self, action: np.ndarray) -> dict[str, Any]:
        """
        agent action を simulator 制御コマンド辞書へ変換する。
        例: {"steering": float, "acceleration": float, "gear": int}
        """
        raise NotImplementedError
