from dataclasses import dataclass, field
from typing import Any, Optional
import numpy as np


@dataclass
class EnvState:
    # YAMLで定義した抽出エイリアスを動的に保持する
    named_context: dict[str, Any]

    def get_value(self, key: str, default: Any = None) -> Any:
        value = self.named_context.get(key, default)
        if value is None:
            return default
        return value


@dataclass
class StepContext:
    env_state: EnvState
    prev_env_state: Optional[EnvState]

    agent_action: Optional[np.ndarray]
    sim_action: Optional[np.ndarray]

    step_count: int
    collision_count: int

    section_changed: bool
    lap_completed: bool
    collision: bool

    info: dict[str, Any] = field(default_factory=dict)
