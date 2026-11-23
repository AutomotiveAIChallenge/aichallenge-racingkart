#!/usr/bin/env python3
"""Apply a saved vehicle IMU bias to the submission with participant approval."""
import argparse
from pathlib import Path
import subprocess
import sys

from calibration import apply_saved_imu_bias, detect_vehicle_id

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUBMIT = REPO_ROOT / "aichallenge/workspace/src/aichallenge_submit"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submit-dir", type=Path, default=DEFAULT_SUBMIT)
    args = parser.parse_args()
    try:
        return 0 if apply_saved_imu_bias(args.submit_dir, detect_vehicle_id(REPO_ROOT)) else 5
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"❌ Cannot apply saved IMU bias: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
