#!/usr/bin/env python3
"""Swap aichallenge/workspace/src/aichallenge_submit/ with a password-protected zip.

Operators place every team's submission as <id>.zip under the .submissions
directory beforehand. On the vehicle the operator types the team id and the
zip password; the archive's top-level aichallenge_submit/ replaces the one in
the workspace.

The zip must be encrypted with traditional PKZIP encryption (`zip -er
<id>.zip aichallenge_submit`): Python's zipfile cannot decrypt AES entries.
Extraction happens into a sibling temp directory first, so a wrong password or
a malformed archive leaves the current aichallenge_submit/ untouched.
Before replacement, vehicle/.calibration/<VEHICLE_ID>/ supplies the accel/brake
maps and three IMU gyro offsets. Missing or invalid calibration also leaves
the existing submission untouched.

Failures print a line starting with the FAIL marker so vehicle/tui.py retains
them in its failures pane.
"""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from calibration import DEFAULT_CALIBRATION_DIR, apply_calibration, vehicle_id

FAIL = "❌"
SUBMIT_DIR = "aichallenge_submit"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ZIP_DIR = REPO_ROOT / "vehicle" / ".submissions"
DEFAULT_OUTPUT = REPO_ROOT / "aichallenge" / "workspace" / "src"


def fail(msg: str) -> int:
    print(f"{FAIL} {msg}", file=sys.stderr)
    return 1


def list_ids(zip_dir: Path) -> list:
    return sorted(p.stem for p in zip_dir.glob("*.zip"))


def check_layout(zf: zipfile.ZipFile) -> str:
    """Return an error message if the archive is not a bare aichallenge_submit/ tree."""
    prefix = SUBMIT_DIR + "/"
    tops = set()
    for name in zf.namelist():
        if name.startswith("/") or ".." in Path(name).parts:
            return f"unsafe path in zip: {name}"
        tops.add(name.split("/", 1)[0])
        if not name.startswith(prefix):
            return f"zip must contain only {prefix} at top level, found: {', '.join(sorted(tops))}"
    if not tops:
        return "zip is empty"
    return ""


def _restore_unix_permissions(zf: zipfile.ZipFile, output: Path) -> None:
    """zipfile.extractall() drops the Unix mode bits zip stores per entry.

    Without this, a submission's launch scripts silently lose their execute
    bit on extraction and fail at run time with no obvious cause. The mode
    lives in the upper 16 bits of external_attr and is 0 for entries added on
    a non-Unix system (nothing to restore then). Plain chmod: the files are
    already owned by the current user, so this never needs sudo.
    """
    for info in zf.infolist():
        mode = (info.external_attr >> 16) & 0o777
        if not mode:
            continue
        path = output / info.filename
        if path.is_file():
            os.chmod(path, mode)


def extract(zip_path: Path, password: str, output: Path, calibration_dir: Path) -> int:
    target = output / SUBMIT_DIR
    with zipfile.ZipFile(zip_path) as zf:
        err = check_layout(zf)
        if err:
            return fail(err)
        # Same parent as the target so the final move is a rename, not a copy.
        with tempfile.TemporaryDirectory(prefix=".submit-", dir=output) as tmp:
            try:
                zf.extractall(tmp, pwd=password.encode())
            except RuntimeError as exc:
                # zipfile reports a wrong password as RuntimeError("Bad password ...").
                return fail(f"cannot decrypt {zip_path.name}: {exc}")
            except NotImplementedError as exc:
                return fail(
                    f"unsupported zip encryption/compression in {zip_path.name}: {exc} "
                    "(create with `zip -er`, not AES)"
                )
            except zipfile.BadZipFile as exc:
                return fail(f"corrupt zip {zip_path.name}: {exc}")
            _restore_unix_permissions(zf, Path(tmp))
            extracted = Path(tmp) / SUBMIT_DIR
            try:
                apply_calibration(extracted, calibration_dir)
            except (OSError, ValueError) as exc:
                return fail(f"cannot apply vehicle calibration from {calibration_dir}: {exc}")
            if target.exists():
                shutil.rmtree(target)
            os.replace(extracted, target)
    print(f"replaced {target} with {zip_path.name}")
    print(f"applied vehicle calibration: {calibration_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", default=os.environ.get("SUBMISSION_ID", ""),
                    help="team id (<id>.zip); prompted when omitted (env: SUBMISSION_ID)")
    ap.add_argument("--zip-dir", type=Path, default=DEFAULT_ZIP_DIR,
                    help=f"directory holding <id>.zip files (default: {DEFAULT_ZIP_DIR})")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help=f"directory whose {SUBMIT_DIR}/ gets replaced (default: {DEFAULT_OUTPUT})")
    args = ap.parse_args()

    try:
        calibration_dir = DEFAULT_CALIBRATION_DIR / vehicle_id(REPO_ROOT)
    except (OSError, ValueError) as exc:
        return fail(str(exc))

    ids = list_ids(args.zip_dir)
    if not ids:
        return fail(f"no *.zip in {args.zip_dir}")
    print(f"available: {' '.join(ids)}")
    team = args.id or input("ID: ").strip()
    if team not in ids:
        return fail(f"no {team}.zip in {args.zip_dir}")
    password = getpass.getpass("Password: ")
    if not password:
        return fail("empty password")
    try:
        return extract(args.zip_dir / f"{team}.zip", password, args.output, calibration_dir)
    except (OSError, zipfile.BadZipFile) as exc:
        return fail(f"extract failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
