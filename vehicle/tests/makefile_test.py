#!/usr/bin/env python3
"""Regression tests for vehicle console Makefile targets."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


class TestVehicleTuiTarget(unittest.TestCase):
    def test_each_team_directory_gets_its_own_tmux_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            tmux = bin_dir / "tmux"
            tmux.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
            tmux.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

            sessions = []
            for team in ("team-0001", "team-0002"):
                team_dir = root / team
                team_dir.mkdir()
                result = subprocess.run(
                    ["make", "--no-print-directory", "-f", str(MAKEFILE), "vehicle-tui"],
                    cwd=team_dir,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                args = result.stdout.splitlines()
                self.assertEqual(
                    args,
                    ["new", "-A", "-s", f"aic-vehicle-{team}", "vehicle/tui.py"],
                )
                sessions.append(args[3])

            self.assertNotEqual(*sessions)


if __name__ == "__main__":
    unittest.main()
