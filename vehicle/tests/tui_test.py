#!/usr/bin/env python3
"""Unit tests for the probing and sizing helpers in vehicle/tui.py.

Builds real directory trees in a temp dir; never touches docker or curses.
Run with python3 -m unittest (no third-party runner).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tui import probe_workspace, terminal_too_small  # noqa: E402
from tui_core import build_done  # noqa: E402


class TestProbeWorkspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "aichallenge" / "workspace"
        self.ws.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def make_install(self):
        install = self.ws / "install"
        install.mkdir()
        (install / "setup.bash").write_text("# built\n")

    def make_submission(self):
        submit = self.ws / "src" / "aichallenge_submit"
        submit.mkdir(parents=True)
        (submit / "some_package").mkdir()

    def test_empty_workspace(self):
        ws = probe_workspace(self.root, frozenset())
        self.assertFalse(ws.install_setup_bash)
        self.assertIsNone(ws.install_mtime)
        self.assertIsNone(ws.submit_mtime)

    def test_detects_built_install(self):
        self.make_install()
        ws = probe_workspace(self.root, frozenset())
        self.assertTrue(ws.install_setup_bash)
        self.assertIsNotNone(ws.install_mtime)

    def test_install_dir_without_setup_bash_is_not_built(self):
        (self.ws / "install").mkdir()
        ws = probe_workspace(self.root, frozenset())
        self.assertFalse(ws.install_setup_bash)

    def test_populated_submit_dir_has_an_mtime(self):
        # submit_dir_populated is gone: aichallenge_submit/ ships tracked
        # packages, so its presence proves nothing about a download having
        # run (see tui_core's _MEASURED comment). submit_mtime is still
        # sampled, though -- build_done() needs it to judge staleness.
        self.make_submission()
        ws = probe_workspace(self.root, frozenset())
        self.assertIsNotNone(ws.submit_mtime)

    def test_empty_submit_dir_has_no_mtime(self):
        (self.ws / "src" / "aichallenge_submit").mkdir(parents=True)
        ws = probe_workspace(self.root, frozenset())
        self.assertIsNone(ws.submit_mtime)

    def test_built_workspace_reads_as_built(self):
        self.make_submission()
        self.make_install()  # created after src/, so it is newer
        self.assertTrue(build_done(probe_workspace(self.root, frozenset())))

    def test_passes_services_through(self):
        ws = probe_workspace(self.root, frozenset({"driver"}))
        self.assertEqual(ws.services_running, frozenset({"driver"}))

    def test_missing_workspace_dir_does_not_raise(self):
        empty = self.root / "nowhere"
        empty.mkdir()
        ws = probe_workspace(empty, frozenset())
        self.assertFalse(ws.install_setup_bash)
        self.assertIsNone(ws.submit_mtime)


class TestTerminalSize(unittest.TestCase):
    def test_exact_minimum_is_allowed(self):
        self.assertFalse(terminal_too_small(80, 24))

    def test_larger_is_allowed(self):
        self.assertFalse(terminal_too_small(120, 40))

    def test_too_narrow_is_rejected(self):
        self.assertTrue(terminal_too_small(79, 24))

    def test_too_short_is_rejected(self):
        self.assertTrue(terminal_too_small(80, 23))


if __name__ == "__main__":
    unittest.main()
