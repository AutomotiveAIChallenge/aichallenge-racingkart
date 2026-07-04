from __future__ import annotations

from pathlib import Path
from typing import Any, Type

import yaml
from action.default_action_adapter import DefaultAWSIMActionAdapter
from context.context_manager import AWSIMContextManager
from observation.default_observation import ImageSpeedObservationBuilder
from reward.default_reward import DefaultAWSIMReward
from termination.default_termination import CollisionTermination
from termination.interfaces import TerminationFunction


_DEFAULT_EXTRACT_MAP_PATH = (
    Path(__file__).resolve().parent / "context" / "extract_map" / "context_extract_map.yaml"
)


def _build_train_freq(algorithm_cfg: dict) -> tuple[int, str]:
    if "train_freq" in algorithm_cfg:
        train_freq = algorithm_cfg["train_freq"]
        if isinstance(train_freq, (list, tuple)) and len(train_freq) == 2:
            return int(train_freq[0]), str(train_freq[1])
        if isinstance(train_freq, dict):
            return int(train_freq.get("n", 1)), str(train_freq.get("unit", "episode"))
    return (
        int(algorithm_cfg.get("train_freq_n", 1)),
        str(algorithm_cfg.get("train_freq_unit", "episode")),
    )


def _build_policy_kwargs(algorithm_cfg: dict) -> dict[str, Any]:
    if "policy_kwargs" in algorithm_cfg and isinstance(algorithm_cfg["policy_kwargs"], dict):
        return algorithm_cfg["policy_kwargs"]
    return {"net_arch": algorithm_cfg.get("net_arch", [256])}


def select_algorithm(algorithm_cfg: dict, env):
    name = str(algorithm_cfg.get("name", "sac")).lower()
    if name == "sac":
        from stable_baselines3 import SAC

        ent_coef = algorithm_cfg.get("ent_coef", algorithm_cfg.get("entropy_coef", "auto_0.1"))
        return SAC(
            str(algorithm_cfg.get("policy", "MultiInputPolicy")),
            env,
            verbose=int(algorithm_cfg.get("verbose", 1)),
            tensorboard_log=algorithm_cfg.get("tensorboard_log", "./awsim_sac_log/"),
            learning_rate=float(algorithm_cfg.get("learning_rate", 3e-4)),
            ent_coef=ent_coef,
            train_freq=_build_train_freq(algorithm_cfg),
            batch_size=int(algorithm_cfg.get("batch_size", 32)),
            gradient_steps=int(algorithm_cfg.get("gradient_steps", 600)),
            learning_starts=int(algorithm_cfg.get("learning_starts", 300)),
            buffer_size=int(algorithm_cfg.get("buffer_size", 30000)),
            gamma=float(algorithm_cfg.get("gamma", 0.98)),
            tau=float(algorithm_cfg.get("tau", 0.02)),
            use_sde_at_warmup=bool(algorithm_cfg.get("use_sde_at_warmup", True)),
            use_sde=bool(algorithm_cfg.get("use_sde", True)),
            sde_sample_freq=int(algorithm_cfg.get("sde_sample_freq", 64)),
            policy_kwargs=_build_policy_kwargs(algorithm_cfg),
        )

    raise ValueError(f"Unknown algorithm name: {name}")


def select_algorithm_class(algorithm_cfg: dict) -> Type:
    name = str(algorithm_cfg.get("name", "sac")).lower()
    if name == "sac":
        from stable_baselines3 import SAC

        return SAC
    raise ValueError(f"Unknown algorithm name: {name}")


def _load_extract_map_from_yaml() -> dict[str, Any]:
    map_path = _DEFAULT_EXTRACT_MAP_PATH
    if not map_path.exists():
        raise ValueError(f"extract_map YAML file not found: {map_path}")

    with map_path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"extract_map YAML root must be mapping: {map_path}")

    extract_map = loaded.get("extract_map", loaded)
    if not isinstance(extract_map, dict):
        raise ValueError(f"extract_map must be mapping in YAML: {map_path}")

    return extract_map


def select_context_manager(context_cfg: dict) -> AWSIMContextManager:
    name = str(context_cfg.get("name", "default_awsim_context_manager")).lower()
    if name == "default_awsim_context_manager":
        return AWSIMContextManager(
            extract_map=_load_extract_map_from_yaml(),
        )
    raise ValueError(f"Unknown context manager name: {name}")


def select_action_adapter(action_cfg: dict) -> DefaultAWSIMActionAdapter:
    name = str(action_cfg.get("name", "default_awsim_action_adapter")).lower()
    if name == "default_awsim_action_adapter":
        return DefaultAWSIMActionAdapter(
            min_steering=float(action_cfg["min_steering"]),
            max_steering=float(action_cfg["max_steering"]),
            min_accel=float(action_cfg["min_accel"]),
            max_accel=float(action_cfg["max_accel"]),
        )
    raise ValueError(f"Unknown action adapter name: {name}")


def select_observation_builder(observation_cfg: dict) -> ImageSpeedObservationBuilder:
    name = str(observation_cfg.get("name", "default_image_speed_observation_builder")).lower()
    if name == "default_image_speed_observation_builder":
        return ImageSpeedObservationBuilder()
    raise ValueError(f"Unknown observation builder name: {name}")


def select_reward_function(reward_cfg: dict) -> DefaultAWSIMReward:
    name = str(reward_cfg.get("name", "default_awsim_reward")).lower()
    if name == "default_awsim_reward":
        return DefaultAWSIMReward(
            speed_reward_scale=float(reward_cfg["speed_reward_scale"]),
            step_time_penalty=float(reward_cfg["step_time_penalty"]),
            collision_penalty=float(reward_cfg.get("collision_penalty", 100.0)),
        )
    raise ValueError(f"Unknown reward function name: {name}")


def select_termination_function(termination_cfg: dict) -> TerminationFunction:
    name = str(termination_cfg.get("name", "default_collision_termination")).lower()
    if name == "default_collision_termination":
        return CollisionTermination(
            collision_speed_threshold=float(termination_cfg["collision_speed_threshold"]),
            collision_count_threshold=int(termination_cfg["collision_count_threshold"]),
            collision_speed_drop_threshold=float(
                termination_cfg.get("collision_speed_drop_threshold", 1.5)
            ),
            min_speed_before_drop=float(
                termination_cfg.get("min_speed_before_drop", 2.0)
            ),
        )
    elif name == "none_termination":
        from termination.none_termination import NoneTermination

        return NoneTermination()

    raise ValueError(f"Unknown termination function name: {name}")


def _apply_time_limit_wrapper(env, timelimit_cfg: dict):
    from gymnasium.wrappers import TimeLimit

    max_episode_steps = int(
        timelimit_cfg.get("max_episode_steps", timelimit_cfg.get("time_step", 1000))
    )
    return TimeLimit(env, max_episode_steps=max_episode_steps)


def select_wrappers(algorithm_cfg: dict, env):
    """
    複数Wrapperを順番に適用して返す。

    推奨設定:
      algorithm:
        wrapper_order: [timelimit]
        timelimit:
          enable: true
          max_episode_steps: 10000
    """
    wrapped_env = env

    wrapper_order = algorithm_cfg.get("wrapper_order", ["timelimit"])
    if not isinstance(wrapper_order, (list, tuple)):
        raise ValueError("algorithm.wrapper_order must be a list")

    for wrapper_name in wrapper_order:
        name = str(wrapper_name).lower()
        if name == "timelimit":
            from gymnasium.wrappers import TimeLimit
            timelimit_cfg = algorithm_cfg.get("timelimit", {})
            if bool(timelimit_cfg.get("enable", False)):
                max_episode_steps = int(
                    timelimit_cfg.get("max_episode_steps", timelimit_cfg.get("time_step", 1000))
                )
                wrapped_env = TimeLimit(wrapped_env, max_episode_steps=max_episode_steps)
        elif name in ("none", ""):
            continue
        else:
            raise ValueError(f"Unknown wrapper name: {name}")

    return wrapped_env
