from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# 基本は Python 側で固定値を管理する（頻繁に変える前提ではない設定）
DEFAULT_CONFIG: dict[str, Any] = {
	"algorithm": {
		"name": "sac",
		"policy": "MultiInputPolicy",
		"verbose": 1,
		"learning_rate": 3e-4,
		"ent_coef": "auto_0.1",
		"train_freq_n": 1,
		"train_freq_unit": "episode",
		"batch_size": 32,
		"gradient_steps": 600,
		"learning_starts": 300,
		"buffer_size": 30000,
		"gamma": 0.98,
		"tau": 0.02,
		"use_sde_at_warmup": True,
		"use_sde": True,
		"sde_sample_freq": 64,
		"net_arch": [256],
		"total_timesteps": 300000,
		"log_interval": 1,
		"wrapper_order": ["timelimit"],
		"timelimit": {
			"enable": False,
			"max_episode_steps": 10000,
		},
	},
	"context_manager": {
		"name": "default_awsim_context_manager",
	},
	"action_adapter": {
		"name": "default_awsim_action_adapter",
		"min_steering": -1.0,
		"max_steering": 1.0,
		"min_accel": 0.0,
		"max_accel": 1.0,
	},
	"observation_builder": {
		"name": "default_image_speed_observation_builder",
	},
	"reward": {
		"name": "default_awsim_reward",
		"speed_reward_scale": 0.1,
		"section_bonus": 10.0,
		"lap_bonus": 50.0,
		"laptime_bonus_scale": 400.0,
		"laptime_bonus_cap": 40.0,
		"step_time_penalty": 0.05,
		"collision_penalty": 100.0,
	},
	"termination": {
		"name": "default_collision_termination",
		"collision_speed_threshold": 0.05,
		"collision_count_threshold": 100,
		"collision_speed_drop_threshold": 1.5,
		"min_speed_before_drop": 2.0,
	},
}


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
	merged = dict(base)
	for key, value in override.items():
		if isinstance(value, dict) and isinstance(merged.get(key), dict):
			merged[key] = _deep_merge_dict(merged[key], value)
		else:
			merged[key] = value
	return merged


def load_config(config_path: str | None = None) -> dict[str, Any]:
	"""
	設定をロードする。

	- config_path 未指定: Python の DEFAULT_CONFIG を返す
	- config_path 指定あり: YAML を読み込み、DEFAULT_CONFIG に deep merge して返す
	"""
	if not config_path:
		return dict(DEFAULT_CONFIG)

	path = Path(config_path)
	if not path.exists():
		print(f"Config file not found: {path}. Use Python DEFAULT_CONFIG.")
		return dict(DEFAULT_CONFIG)

	with path.open("r", encoding="utf-8") as f:
		loaded = yaml.safe_load(f) or {}

	if not isinstance(loaded, dict):
		raise ValueError(f"Config root must be mapping: {path}")

	return _deep_merge_dict(DEFAULT_CONFIG, loaded)
