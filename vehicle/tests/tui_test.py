#!/usr/bin/env python3
"""Unit tests for the probing and sizing helpers in vehicle/tui.py.

Builds real directory trees in a temp dir; never touches docker or curses.
Run with python3 -m unittest (no third-party runner).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tui import (  # noqa: E402
    Console,
    MIN_COLS,
    header_title,
    vehicle_id,
    MIN_LINES,
    driver_image_date,
    is_failure_line,
    min_lines,
    repo_commit,
    version_line,
    service_status_lines,
    should_reobserve,
    terminal_too_small,
    wrap_line,
)
from tui_core import (  # noqa: E402
    PARTICIPANT_STEPS,
    REQUIRED_SERVICES,
    ROLE_PARTICIPANT,
    STAFF_STEPS,
    STEP_UP,
    STEP_DRIVER,
    STEP_DRIVER_DOWN,
    STEP_ROSBAG,
    STEP_ROSBAG_DOWN,
    STEP_TEARDOWN,
    STEP_ZENOH,
    STEP_ZENOH_DOWN,
    STEPS,
    Workspace,
    step_by_id,
)


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
        # header 1 + services 2 + STEPS + failures 見出し 1 + failures 1 + log 見出し 1 + log 1。
        self.assertGreaterEqual(MIN_LINES, 3 + len(STEPS) + 4)


class TestStepNote(unittest.TestCase):
    def _row(self, step, idx=0):
        note = f"  ({step.note})" if step.note else ""
        return f"{idx + 1} OK {step.title}{note}"

    def test_the_steps_whose_timing_is_not_obvious_carry_a_note(self):
        for step_id in (
            STEP_DRIVER, STEP_ZENOH,
            STEP_DRIVER_DOWN, STEP_ZENOH_DOWN,
            STEP_ROSBAG, STEP_ROSBAG_DOWN, STEP_TEARDOWN,
        ):
            with self.subTest(step_id=step_id):
                self.assertTrue(step_by_id(step_id).note)

    def test_a_step_without_a_note_renders_the_title_alone(self):
        step = step_by_id(STEP_UP)
        self.assertEqual(self._row(step), f"1 OK {step.title}")

    def test_every_row_fits_min_cols(self):
        # 注釈を足しても端末の最低幅に収まること。はみ出すと行末が切れる。
        for steps in (PARTICIPANT_STEPS, STAFF_STEPS):
            for idx, step in enumerate(steps):
                with self.subTest(step_id=step.step_id):
                    self.assertLessEqual(len(self._row(step, idx)), MIN_COLS)

    def test_notes_are_ascii(self):
        # 画面の幅計算は文字数なので、全角が混ざると行末がずれる。
        for step in STEPS:
            with self.subTest(step_id=step.step_id):
                self.assertTrue(step.note.isascii())


class TestHeaderTitle(unittest.TestCase):
    def test_shows_the_vehicle_then_the_role(self):
        self.assertEqual(header_title("staff", "A2"), "[A2] vehicle console [staff]")

    def test_longest_form_fits_min_cols_with_the_hints(self):
        # header は「タイトル + 区切り 1 + ヒント 10」。最長の役割と ID で測る。
        longest = header_title(ROLE_PARTICIPANT, "test")
        self.assertLessEqual(len(longest) + 1 + len("↑↓ enter q"), MIN_COLS)


class TestVehicleId(unittest.TestCase):
    def test_environment_wins_over_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text("VEHICLE_ID=A3\n")
            with mock.patch.dict(os.environ, {"VEHICLE_ID": "A6"}):
                self.assertEqual(vehicle_id(Path(tmp)), "A6")

    def test_reads_the_env_file_when_the_environment_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text('# VEHICLE_ID=A1\nexport VEHICLE_ID="A3" \n')
            with mock.patch.dict(os.environ, {"VEHICLE_ID": ""}):
                self.assertEqual(vehicle_id(Path(tmp)), "A3")

    def test_last_assignment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text("VEHICLE_ID=A1\nVEHICLE_ID=A2\n")
            with mock.patch.dict(os.environ, {"VEHICLE_ID": ""}):
                self.assertEqual(vehicle_id(Path(tmp)), "A2")

    def test_unknown_reads_as_a_dash_not_as_a_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"VEHICLE_ID": ""}):
                self.assertEqual(vehicle_id(Path(tmp)), "-")


class TestVersionLine(unittest.TestCase):
    def test_shows_image_date_and_commit(self):
        self.assertEqual(
            version_line("2026-09-01", "bd9c626"),
            "driver image: 2026-09-01  aic commit: bd9c626",
        )

    def test_missing_values_read_as_unknown_not_as_blank(self):
        self.assertEqual(
            version_line(None, None),
            "driver image: unknown  aic commit: unknown",
        )

    def test_longest_form_fits_min_cols(self):
        self.assertLessEqual(len(version_line("2026-09-01", "bd9c626")), MIN_COLS)

    def test_labels_say_which_repository_each_value_comes_from(self):
        line = version_line("2026-09-01", "bd9c626")
        self.assertIn("driver image:", line)
        self.assertIn("aic commit:", line)

    def test_staff_row_is_accounted_for_in_the_minimum_height(self):
        self.assertEqual(min_lines(8, extra=1), min_lines(8) + 1)


class TestVersionProbes(unittest.TestCase):
    def test_commit_of_a_real_repo_is_a_short_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "f").write_text("x")
            subprocess.run(["git", "add", "f"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@e", "-c", "user.name=t",
                 "commit", "-qm", "c"],
                cwd=root, check=True,
            )
            commit = repo_commit(root)
            self.assertIsNotNone(commit)
            self.assertRegex(commit, r"^[0-9a-f]{7,}$")

    def test_commit_outside_a_repo_is_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(repo_commit(Path(tmp)))

    def test_unknown_image_is_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                driver_image_date(Path(tmp), image="no-such-image:does-not-exist")
            )


class TestServiceStatusLines(unittest.TestCase):
    def test_all_down(self):
        self.assertEqual(
            service_status_lines(frozenset()),
            ("running: -", "stopped: driver autoware zenoh rosbag"),
        )

    def test_all_up(self):
        self.assertEqual(
            service_status_lines(frozenset(REQUIRED_SERVICES)),
            ("running: driver autoware zenoh rosbag", "stopped: -"),
        )

    def test_partial_keeps_required_services_order(self):
        self.assertEqual(
            service_status_lines(frozenset({"zenoh", "driver"})),
            ("running: driver zenoh", "stopped: autoware rosbag"),
        )

    def test_names_are_not_abbreviated(self):
        joined = " ".join(service_status_lines(frozenset({"driver"})))
        for name in REQUIRED_SERVICES:
            self.assertIn(name, joined)

    def test_longest_form_fits_min_cols(self):
        for line in service_status_lines(frozenset()) + service_status_lines(
            frozenset(REQUIRED_SERVICES)
        ):
            self.assertLessEqual(len(line), MIN_COLS - 1)


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


class FakeScreen:
    """Just enough of a curses window for Console.draw() to run headless.

    Records what was written at each row so tests can inspect the header
    without a real terminal (curses.doupdate() needs one).
    """

    def __init__(self, lines=24, cols=80):
        self._lines = lines
        self._cols = cols
        self.rows = {}

    def getmaxyx(self):
        return self._lines, self._cols

    def erase(self):
        self.rows = {}

    def addnstr(self, y, x, text, n, attr=0):
        self.rows[y] = text[:n]

    def noutrefresh(self):
        pass


class TestServiceLinePlacement(unittest.TestCase):
    """The service status appears exactly once: two lines under the header."""

    def _console(self, services_running=frozenset()):
        from unittest import mock

        screen = FakeScreen()
        console = Console(screen, steps=PARTICIPANT_STEPS, role=ROLE_PARTICIPANT)
        console.ws = Workspace(services_running=services_running)
        with mock.patch("tui.curses.doupdate"):
            console.draw()
        return console, screen

    def test_rows_1_and_2_are_running_and_stopped(self):
        _, screen = self._console(frozenset({"driver", "autoware"}))
        self.assertEqual(screen.rows[1].rstrip(), "running: driver autoware")
        self.assertEqual(screen.rows[2].rstrip(), "stopped: zenoh rosbag")

    def test_header_has_no_service_names(self):
        _, screen = self._console(frozenset(REQUIRED_SERVICES))
        for name in REQUIRED_SERVICES:
            self.assertNotIn(name, screen.rows[0])

    def test_steps_start_on_fourth_row_without_service_names(self):
        _, screen = self._console(frozenset(REQUIRED_SERVICES))
        self.assertIn("check preflight", screen.rows[3])
        for idx in range(3, len(PARTICIPANT_STEPS) + 3):
            self.assertNotIn("running:", screen.rows[idx])
            self.assertNotIn("stopped:", screen.rows[idx])
            self.assertNotIn("rosbag", screen.rows[idx])

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


class TestStreamInput(unittest.TestCase):
    def test_background_step_leaves_console_input_unread(self):
        # Give an isolated console pending input, then run a child that reads
        # stdin (as docker compose exec does, even with -T).
        script = """
import json
import os
import sys
from tui import Console
from tui_core import Step

console = Console(None)
step = Step("probe", "probe", (
    sys.executable, "-c",
    "import os, sys; print(repr(os.read(0, 1))); sys.exit(7)",
))
console._stream(step)
events = []
while not console.log_queue.empty():
    events.append(console.log_queue.get_nowait())
print(json.dumps({"events": events, "pending_input": os.read(0, 1).decode()}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            input="q", text=True, capture_output=True, check=True, timeout=10,
        )
        observed = json.loads(result.stdout)
        self.assertEqual(observed["pending_input"], "q")
        self.assertEqual(observed["events"], [
            ["line", "b''"],
            ["exit", ["probe", 7]],
        ])


if __name__ == "__main__":
    unittest.main()
