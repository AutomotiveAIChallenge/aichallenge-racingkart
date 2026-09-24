#!/usr/bin/env python3
"""Confirm and apply accel/brake maps and IMU bias to the installed packages before startup."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from calibration import (
    IMU_PARAM, MAP_DIR, apply_maps, apply_saved_imu_bias,
    atomic_write, detect_vehicle_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INSTALL = REPO_ROOT / "aichallenge/workspace/install"
SETTING_PATHS = (MAP_DIR / "accel_map.csv", MAP_DIR / "brake_map.csv", IMU_PARAM)


def host_path(path: Path) -> Path:
    """Map absolute symlinks created by the build container back to the host mount."""
    resolved = path.resolve()
    container_mount = Path("/aichallenge")
    if resolved.is_relative_to(container_mount):
        return (REPO_ROOT / "aichallenge" / resolved.relative_to(container_mount)).resolve()
    return resolved


def installed_path(install: Path, relative: Path) -> Path:
    """Resolve the package share directory for isolated or merged colcon installs."""
    package, *parts = relative.parts
    isolated = host_path(install / package / "share" / package)
    merged = host_path(install / "share" / package)
    share = isolated if isolated.is_dir() else merged
    return host_path(share.joinpath(*parts))


def apply_calibration(install: Path, vehicle_id: str) -> None:
    """Stage both approvals, then update runtime files, following source symlinks."""
    if not (install / "setup.bash").is_file():
        raise ValueError(f"built workspace not found: {install / 'setup.bash'}")
    targets = {}
    before = {}
    for relative in SETTING_PATHS:
        path = installed_path(install, relative)
        if path.is_file():
            targets[relative] = path
            before[relative] = path.read_text(encoding="utf-8")
        else:
            print(f"⚠️ No installed {relative}; skipping its update.")

    with tempfile.TemporaryDirectory(prefix="vehicle-calibration-") as directory:
        stage = Path(directory)
        for relative, text in before.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        names = [path.name for path in targets if path.parent == MAP_DIR]
        if names:
            apply_maps(stage, names)
        apply_saved_imu_bias(stage, vehicle_id)
        updates = {
            relative: (stage / relative).read_text(encoding="utf-8")
            for relative in targets
            if (stage / relative).read_text(encoding="utf-8") != before[relative]
        }
        for relative in updates:
            if targets[relative].read_text(encoding="utf-8") != before[relative]:
                raise ValueError(f"settings changed during approval: {targets[relative]}")
        written = []
        try:
            for relative, text in updates.items():
                atomic_write(targets[relative], text)
                written.append(relative)
        except OSError:
            for relative in reversed(written):
                try:
                    atomic_write(targets[relative], before[relative])
                except OSError as exc:
                    print(f"❌ Cannot restore {targets[relative]}: {exc}", file=sys.stderr)
            raise
    if updates:
        print("✅ Approved settings applied; they take effect on the next Autoware startup.")
    else:
        print("Settings retained; confirmation complete.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL)
    args = parser.parse_args()
    try:
        services = subprocess.run(
            ["docker", "compose", "-f", str(REPO_ROOT / "docker-compose.yml"),
             "ps", "--status", "running", "--services"],
            check=True, capture_output=True, text=True,
        )
        if "autoware" in services.stdout.splitlines():
            raise ValueError("stop Autoware with 'autoware-vehicle down' before applying settings")
        apply_calibration(args.install_dir, detect_vehicle_id(REPO_ROOT))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"❌ Cannot apply accel/brake maps and IMU bias: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
