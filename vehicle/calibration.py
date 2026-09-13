"""Vehicle calibration files and IMU offsets (standard library only)."""
from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from pathlib import Path

AXES = ("x", "y", "z")
MAP_DIR = Path("aichallenge_submit_launch/data")
IMU_PARAM = Path("imu_corrector/config/imu_corrector.param.yaml")
DEFAULT_CALIBRATION_DIR = Path(__file__).resolve().parent / ".calibration"
_OFFSET_LINE = re.compile(
    r"^(?P<prefix>\s*angular_velocity_offset_(?P<axis>[xyz])\s*:\s*)"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"(?P<suffix>\s*(?:#.*)?)$"
)


def vehicle_id(repo_root: Path) -> str:
    """Read environment, then repo .env; never guess a vehicle."""
    value = os.environ.get("VEHICLE_ID", "").strip()
    if not value:
        try:
            lines = (repo_root / ".env").read_text().splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines:
            line = line.strip().removeprefix("export ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "VEHICLE_ID":
                value = raw.strip().strip("\"'").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("VEHICLE_ID is missing or invalid; set it in the environment or .env")
    return value


def parse_offsets(text: str, *, flat: bool = False) -> dict[str, float]:
    """Read exactly three finite offsets; flat calibration YAML has no other keys."""
    offsets = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _OFFSET_LINE.fullmatch(line)
        if not match:
            if flat or line.lstrip().startswith("angular_velocity_offset_"):
                raise ValueError(f"invalid IMU bias line: {line}")
            continue
        axis = match["axis"]
        value = float(match["value"])
        if axis in offsets or not math.isfinite(value):
            raise ValueError(f"duplicate or non-finite IMU offset: {axis}")
        offsets[axis] = value
    if set(offsets) != set(AXES):
        raise ValueError("IMU bias must contain angular_velocity_offset_x, _y and _z")
    return offsets


def replace_offsets(text: str, offsets: dict[str, float]) -> str:
    """Replace only offset values, retaining the participant's other settings."""
    parse_offsets(text)
    if set(offsets) != set(AXES) or not all(math.isfinite(v) for v in offsets.values()):
        raise ValueError("IMU offsets must be three finite numbers")
    lines = []
    for line in text.splitlines(keepends=True):
        match = _OFFSET_LINE.fullmatch(line.rstrip("\r\n"))
        if match:
            start, end = match.span("value")
            line = f"{line[:start]}{offsets[match['axis']]:.6f}{line[end:]}"
        lines.append(line)
    return "".join(lines)


def atomic_write(path: Path, text: str) -> None:
    """Replace the real file, including when install/ points to a source symlink."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    descriptor, tmp = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_current_offsets(param_yaml_path: str) -> dict[str, float] | None:
    try:
        return parse_offsets(Path(param_yaml_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_new_offsets(param_yaml_path: str, offsets: dict[str, float]) -> bool:
    try:
        path = Path(param_yaml_path)
        atomic_write(path, replace_offsets(path.read_text(encoding="utf-8"), offsets))
        return True
    except (OSError, ValueError):
        return False


def save_bias(path: Path, offsets: dict[str, float]) -> None:
    text = "".join(f"angular_velocity_offset_{axis}: {offsets[axis]:.6f}\n" for axis in AXES)
    parse_offsets(text, flat=True)
    atomic_write(path, text)


def read_map(path: Path) -> bytes:
    """Check the rectangular velocity/pedal table before copying its original bytes."""
    data = path.read_bytes()
    rows = list(csv.reader(data.decode("utf-8").splitlines()))
    if len(rows) < 3 or len(rows[0]) < 3 or rows[0][0].strip() != "default":
        raise ValueError(f"invalid accel/brake map header or size: {path}")
    width = len(rows[0])
    try:
        velocities = [float(cell) for cell in rows[0][1:]]
        pedals = []
        for row in rows[1:]:
            if len(row) != width:
                raise ValueError("inconsistent row width")
            values = [float(cell) for cell in row]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("non-finite value")
            pedals.append(values[0])
        for axis in (velocities, pedals):
            if not all(math.isfinite(v) for v in axis) or any(
                left >= right for left, right in zip(axis, axis[1:])
            ):
                raise ValueError("axis must be finite and strictly increasing")
    except ValueError as exc:
        raise ValueError(f"invalid accel/brake map {path}: {exc}") from exc
    return data


def apply_calibration(submit: Path, directory: Path) -> None:
    """Validate all inputs before applying calibration to a disposable submission."""
    maps = {name: read_map(directory / name) for name in ("accel_map.csv", "brake_map.csv")}
    offsets = parse_offsets((directory / "imu_bias.yaml").read_text(encoding="utf-8"), flat=True)
    param = submit / IMU_PARAM
    updated = replace_offsets(param.read_text(encoding="utf-8"), offsets)
    for name in maps:
        if not (submit / MAP_DIR / name).is_file():
            raise ValueError(f"submission is missing {MAP_DIR / name}")
    for name, data in maps.items():
        (submit / MAP_DIR / name).write_bytes(data)
    param.write_text(updated, encoding="utf-8")
