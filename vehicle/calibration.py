"""Vehicle calibration files and IMU offsets (standard library only)."""
from __future__ import annotations

import csv
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

AXES = ("x", "y", "z")
MAP_DIR = Path("aichallenge_submit_launch/data")
IMU_PARAM = Path("imu_corrector/config/imu_corrector.param.yaml")
DEFAULT_BIAS_DIR = Path(__file__).resolve().parent / ".calibration"
DEFAULT_MAP_DIR = (
    Path(__file__).resolve().parent.parent
    / "aichallenge/workspace/src/aichallenge_system/aichallenge_awsim_adapter/data"
)
_OFFSET_LINE = re.compile(
    r"^(?P<prefix>\s*angular_velocity_offset_(?P<axis>[xyz])\s*:\s*)"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"(?P<suffix>\s*(?:#.*)?)$"
)


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


def apply_maps(submit: Path, names: list[str]) -> bool:
    """Validate and recommend common maps, then apply the approved bytes."""
    error = None
    try:
        maps = {name: read_map(DEFAULT_MAP_DIR / name) for name in names}
    except (OSError, ValueError) as exc:
        error = exc
        print(f"⚠️ Common accel/brake maps unavailable: {exc}")
    if not confirm_application("実車用の共通 accel/brake map", recommended=error is None):
        return False
    if error is not None:
        raise error
    for name, data in maps.items():
        atomic_write(submit / MAP_DIR / name, data.decode("utf-8"))
    return True


def detect_vehicle_id(repo_root: Path) -> str:
    """Use the same environment/.env/hostname resolution as setup_check.sh."""
    result = subprocess.run(
        ["bash", "-c", 'REPO_ROOT=$1; source "$2"; detect_vehicle_id', "bash",
         str(repo_root), str(Path(__file__).resolve().parent / "vehicle_ports.sh")],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def read_saved_bias(vehicle_id: str) -> dict[str, float]:
    """Read this vehicle's stored offsets; never fall back to zero or another kart."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", vehicle_id):
        raise ValueError("set a valid VEHICLE_ID before applying saved IMU bias")
    return parse_offsets(
        (DEFAULT_BIAS_DIR / vehicle_id / "imu_bias.yaml").read_text(encoding="utf-8"), flat=True
    )


def confirm_update(prompt: str, *, default_yes: bool = False) -> bool:
    """Enter accepts the displayed default; EOF always retains current settings."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return default_yes if not answer else answer in ("y", "yes")


def confirm_application(settings: str, *, recommended: bool) -> bool:
    """Recommend applying validated settings, retaining participant approval."""
    if recommended:
        prompt = (
            f"{settings}を適用します。\n"
            "通常はこちらを選択してください。参加者の承認を確認してください。\n\n"
            "Y: 推奨設定を適用する (Recommended)\n"
            "n: 提出物の値を保持する\n"
            "[Y/n]: "
        )
    else:
        prompt = (
            f"{settings}の適用元に問題があります。適用を選ぶとエラーになります。\n"
            "提出物の値を保持する場合は n を選択してください。\n"
            "適用しますか？ [y/N]: "
        )
    return confirm_update(prompt, default_yes=recommended)


def apply_saved_imu_bias(submit: Path, vehicle_id: str) -> bool:
    """Apply a vehicle profile after approval without changing the saved profile."""
    param = submit / IMU_PARAM
    if not param.is_file():
        print(f"⚠️ Submission has no {IMU_PARAM}; skipping IMU update.")
        return False
    before = param.read_text(encoding="utf-8")
    try:
        current = parse_offsets(before)
    except ValueError as exc:
        print(f"⚠️ No supported IMU offsets; skipping IMU update: {exc}")
        return False
    # Read before the prompt to show current/saved values and the difference.
    # Missing profiles do not prevent declining; approval can never apply a fallback.
    saved = None
    error = None
    try:
        saved = read_saved_bias(vehicle_id)
    except (OSError, ValueError) as exc:
        error = exc
        print(f"⚠️ Saved IMU bias unavailable for {vehicle_id or '(unset)'}: {exc}")
    if saved is not None:
        print(f"IMU 角速度バイアス [rad/s] / VEHICLE_ID={vehicle_id}")
        print("軸    現在値        保存値        差分(保存値−現在値)")
        for axis in AXES:
            print(f"{axis}  {current[axis]:+.6f}  {saved[axis]:+.6f}  {saved[axis] - current[axis]:+.6f}")
    if not confirm_application(
        f"車両 {vehicle_id or '(未設定)'} の保存済み IMU バイアス",
        recommended=error is None,
    ):
        print("IMU update declined; participant offsets retained.")
        return False
    if error is not None:
        raise ValueError(f"cannot apply saved IMU bias for {vehicle_id or '(unset)'}: {error}") from error
    if param.read_text(encoding="utf-8") != before:
        raise ValueError("IMU settings changed during approval; retry the update")
    atomic_write(param, replace_offsets(before, saved))
    print(f"Updated IMU offsets from vehicle/.calibration/{vehicle_id}/imu_bias.yaml")

    return True
