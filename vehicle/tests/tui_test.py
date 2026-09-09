#!/usr/bin/env python3
"""Unit tests for the probing and sizing helpers in vehicle/tui.py.

Builds real directory trees in a temp dir; never touches docker or curses.
Run with python3 -m unittest (no third-party runner).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tui import (  # noqa: E402
    MIN_COLS,
    MIN_LINES,
    is_failure_line,
    probe_workspace,
    service_badge,
    should_reobserve,
    terminal_too_small,
    workspace_is_pristine,
    wrap_line,
)
from tui_core import REQUIRED_SERVICES, STEPS, build_done  # noqa: E402


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
        self.assertIsNone(ws.install_mtime)
        self.assertIsNone(ws.submit_mtime)

    def test_detects_built_install(self):
        self.make_install()
        ws = probe_workspace(self.root, frozenset())
        self.assertIsNotNone(ws.install_mtime)

    def test_install_dir_without_setup_bash_is_not_built(self):
        (self.ws / "install").mkdir()
        ws = probe_workspace(self.root, frozenset())
        self.assertIsNone(ws.install_mtime)

    def test_populated_submit_dir_has_an_mtime(self):
        # submit_dir_populated is gone: aichallenge_submit/ ships tracked
        # packages, so its presence proves nothing about a download having
        # run (see the submission step's comment in tui_core). submit_mtime is still
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
        self.assertIsNone(ws.install_mtime)
        self.assertIsNone(ws.submit_mtime)


class TestWorkspaceIsPristine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ws = self.root / "aichallenge" / "workspace"
        (self.ws / "src").mkdir(parents=True)
        (self.ws / "src" / "tracked.txt").write_text("x")
        (self.ws / ".gitignore").write_text("/build\n/install\n/log\n")
        self._git("init", "-q")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "init")

    def _git(self, *args):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=str(self.root), check=True, capture_output=True,
        )

    def test_fresh_checkout_is_pristine(self):
        self.assertTrue(workspace_is_pristine(self.root))

    def test_ignored_build_artifacts_make_it_dirty(self):
        # cleanup は build/ install/ log/ も消す対象なので、ignored でも「済」ではない。
        # 空ディレクトリでも false: git を呼ぶ前にディレクトリの有無で判っている。
        for name in ("build", "install", "log"):
            with self.subTest(name=name):
                (self.ws / name).mkdir()
                self.assertFalse(workspace_is_pristine(self.root))
                (self.ws / name).rmdir()

    def test_untracked_file_makes_it_dirty(self):
        (self.ws / "src" / "new.txt").write_text("")
        self.assertFalse(workspace_is_pristine(self.root))

    def test_modified_tracked_file_makes_it_dirty(self):
        (self.ws / "src" / "tracked.txt").write_text("changed")
        self.assertFalse(workspace_is_pristine(self.root))

    def test_changes_outside_the_workspace_do_not_count(self):
        # cleanup の責務は workspace 配下だけ。他のローカル変更は見ない。
        (self.root / "notes.txt").write_text("")
        self.assertTrue(workspace_is_pristine(self.root))

    def test_not_a_repo_is_not_pristine(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertFalse(workspace_is_pristine(Path(other)))


class TestTerminalSize(unittest.TestCase):
    def test_exact_minimum_is_allowed(self):
        self.assertFalse(terminal_too_small(MIN_COLS, MIN_LINES))

    def test_larger_is_allowed(self):
        self.assertFalse(terminal_too_small(80, 24))

    def test_too_narrow_is_rejected(self):
        self.assertTrue(terminal_too_small(MIN_COLS - 1, MIN_LINES))

    def test_too_short_is_rejected(self):
        self.assertTrue(terminal_too_small(MIN_COLS, MIN_LINES - 1))

    def test_minimum_leaves_a_row_for_failures_and_a_row_for_log(self):
        # header 1 + STEPS + failures 見出し 1 + failures 1 + log 見出し 1 + log 1。
        self.assertGreaterEqual(MIN_LINES, 1 + len(STEPS) + 4)


class TestServiceBadge(unittest.TestCase):
    def test_all_down(self):
        self.assertEqual(service_badge(frozenset()), "----")

    def test_all_up(self):
        self.assertEqual(
            service_badge(frozenset(REQUIRED_SERVICES)), "dazr"
        )

    def test_partial(self):
        self.assertEqual(
            service_badge(frozenset({"driver", "autoware"})), "da--"
        )

    def test_position_follows_required_services(self):
        # A reordering of REQUIRED_SERVICES must not silently scramble the
        # badge: position is what carries the meaning.
        for idx, name in enumerate(REQUIRED_SERVICES):
            with self.subTest(service=name):
                badge = service_badge(frozenset({name}))
                self.assertEqual(badge[idx], name[0])
                self.assertEqual(badge.count("-"), len(REQUIRED_SERVICES) - 1)


class TestIsFailureLine(unittest.TestCase):
    def test_marker_at_start(self):
        self.assertTrue(is_failure_line("\u274c CAN can0 not found"))

    def test_indented_marker_is_not_counted(self):
        # A check that quotes the marker inside example output or a hint must
        # not be counted as a failure of its own.
        self.assertFalse(is_failure_line("   \u274c indented"))

    def test_ok_line_is_not_a_failure(self):
        self.assertFalse(is_failure_line("\u2705 all good"))

    def test_warning_is_not_a_failure(self):
        self.assertFalse(is_failure_line("\u26a0\ufe0f  warning"))

    def test_plain_line(self):
        self.assertFalse(is_failure_line("Building package foo"))

    def test_empty(self):
        self.assertFalse(is_failure_line(""))


class TestWrapLine(unittest.TestCase):
    def test_short_line_passes_through(self):
        self.assertEqual(wrap_line("short", 20), ["short"])

    def test_long_line_splits(self):
        self.assertEqual(wrap_line("aaa bbb ccc", 7), ["aaa bbb", "ccc"])

    def test_blank_line_survives_as_one_row(self):
        # Dropping it would make the log lose its paragraph breaks.
        self.assertEqual(wrap_line("", 10), [""])
        self.assertEqual(wrap_line("   ", 10), [""])

    def test_zero_width_yields_nothing(self):
        self.assertEqual(wrap_line("anything", 0), [])


class TestShouldReobserve(unittest.TestCase):
    def test_not_while_a_step_is_running(self):
        # observe() blocks the draw thread, and the step's exit re-observes.
        self.assertFalse(should_reobserve(True, 1000.0, 0.0))

    def test_not_before_the_interval_elapses(self):
        self.assertFalse(should_reobserve(False, 1.9, 0.0))

    def test_at_the_interval(self):
        self.assertTrue(should_reobserve(False, 2.0, 0.0))

    def test_after_the_interval(self):
        # An external `make down` while idle has to show up on its own.
        self.assertTrue(should_reobserve(False, 10.0, 0.0))


if __name__ == "__main__":
    unittest.main()
