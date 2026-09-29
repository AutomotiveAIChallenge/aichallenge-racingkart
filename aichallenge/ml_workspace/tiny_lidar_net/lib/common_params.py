from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml

# Values used when no common parameter file is given (the behaviour before the file existed).
DEFAULT_COMMON_PARAMS: Dict[str, Any] = {
    "input_dim": 750,
    "max_range": 30.0,
    "accel_scale": 1.0,
    "brake_scale": 1.0,
}


def load_common_params(path: Optional[Union[str, Path]], base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads the parameters shared by training and the inference node.

    The file is the ROS 2 parameter file of tiny_lidar_net_controller
    (tiny_lidar_net_common.param.yaml), so that training and inference read
    the same values.

    Args:
        path: Path to the parameter file. A relative path is resolved against base_dir.
            If None, DEFAULT_COMMON_PARAMS is returned.
        base_dir: Base directory for a relative path (defaults to the current directory).

    Returns:
        dict with input_dim, max_range, accel_scale and brake_scale.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If a scale is not positive.
    """
    params = dict(DEFAULT_COMMON_PARAMS)
    if path is None:
        return params

    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (base_dir or Path.cwd()) / p
    if not p.exists():
        raise FileNotFoundError(f"Common parameter file not found: {p}")

    with open(p) as f:
        doc = yaml.safe_load(f) or {}
    ros_params = doc.get("/**", {}).get("ros__parameters", {})

    if "input_dim" in ros_params.get("model", {}):
        params["input_dim"] = int(ros_params["model"]["input_dim"])
    for key in ("max_range", "accel_scale", "brake_scale"):
        if key in ros_params:
            params[key] = float(ros_params[key])

    if params["accel_scale"] <= 0.0 or params["brake_scale"] <= 0.0:
        raise ValueError(
            f"accel_scale and brake_scale must be positive, got {params['accel_scale']} and {params['brake_scale']}"
        )
    return params
