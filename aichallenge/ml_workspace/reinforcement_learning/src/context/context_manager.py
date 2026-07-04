from __future__ import annotations

from typing import Any, Optional

import numpy as np
from .context_types import EnvState, StepContext


class AWSIMContextManager:
    def __init__(self, *, extract_map: Optional[dict[str, Any]] = None) -> None:
        self._prev_env_state: Optional[EnvState] = None
        self._context: Optional[StepContext] = None
        self._extract_map: dict[str, Any] = extract_map or {}

    def reset(self) -> None:
        self._prev_env_state = None
        self._context = None

    def _build_env_state(self, node: Any) -> EnvState:
        named_context = self._build_named_context(node)
        camera_image = named_context.get("camera_image")
        if isinstance(camera_image, np.ndarray):
            named_context["camera_image"] = camera_image.copy()

        return EnvState(named_context=named_context)

    @staticmethod
    def _resolve_attr_path(source: Any, path: list[str]) -> Any:
        current = source
        for key in path:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
        return current

    def _collect_named_context(self, source_value: Any, mapping: Any, out: dict[str, Any], path: list[str]) -> None:
        if isinstance(mapping, str):
            out[mapping] = self._resolve_attr_path(source_value, path)
            return

        if not isinstance(mapping, dict):
            return

        for key, child in mapping.items():
            self._collect_named_context(source_value, child, out, path + [key])

    def _build_named_context(self, node: Any) -> dict[str, Any]:
        named: dict[str, Any] = {}
        if not isinstance(self._extract_map, dict):
            return named

        for source_name, mapping in self._extract_map.items():
            if not isinstance(source_name, str):
                continue
            source_value = getattr(node, source_name, None)
            self._collect_named_context(source_value, mapping, named, [])

        return named

    def build(
        self,
        *,
        node: Any,
        step_count: int,
        agent_action: Optional[np.ndarray],
        sim_action: Optional[np.ndarray],
    ) -> StepContext:
        env_state = self._build_env_state(node)
        prev = self._prev_env_state

        curr_section = int(env_state.get_value("awsim_section", 0.0))
        prev_section = int(prev.get_value("awsim_section", 0.0)) if prev is not None else 0
        curr_lap = int(env_state.get_value("awsim_lap_count", 0.0))
        prev_lap = int(prev.get_value("awsim_lap_count", 0.0)) if prev is not None else 0

        section_changed = prev is not None and curr_section > prev_section
        lap_completed = prev is not None and curr_lap > prev_lap

        return StepContext(
            env_state=env_state,
            prev_env_state=prev,
            agent_action=agent_action,
            sim_action=sim_action,
            step_count=step_count,
            collision_count=0,
            section_changed=section_changed,
            lap_completed=lap_completed,
            collision=False,
            info={},
        )

    def update(
        self,
        *,
        node: Any,
        step_count: int,
        agent_action: Optional[np.ndarray],
        sim_action: Optional[np.ndarray],
    ) -> StepContext:
        self._context = self.build(
            node=node,
            step_count=step_count,
            agent_action=agent_action,
            sim_action=sim_action,
        )
        self._prev_env_state = self._context.env_state
        return self._context
