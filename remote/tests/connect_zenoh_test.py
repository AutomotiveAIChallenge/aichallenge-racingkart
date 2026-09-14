#!/usr/bin/env python3
"""Regression tests for the remote-side Zenoh connection command."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REMOTE_DIR = Path(__file__).resolve().parents[1]
CONNECT_ZENOH = REMOTE_DIR / "connect_zenoh.bash"


class ConnectZenohTest(unittest.TestCase):
    def test_a4_uses_port_7455_and_a4_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_bridge = Path(temp) / "zenoh-bridge-ros2dds"
            fake_bridge.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
            fake_bridge.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{temp}:{env['PATH']}"

            result = subprocess.run(
                [str(CONNECT_ZENOH), "A4"],
                cwd=REMOTE_DIR,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn("Target Vehicle: 'A4' - Port 7455", result.stdout)
        self.assertIn("tls/zenoh.dev.aichallenge-board.jsae.or.jp:7455", result.stdout)
        self.assertIn("/A4", result.stdout)


if __name__ == "__main__":
    unittest.main()
