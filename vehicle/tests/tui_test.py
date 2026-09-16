#!/usr/bin/env python3
"""Unit tests for the probing and sizing helpers in vehicle/tui.py.

Builds real directory trees in a temp dir; never touches docker or curses.
Run with python3 -m unittest (no third-party runner).
"""
import curses
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
    stack_containers,
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
    ROLE_STAFF,
    DONE,
    FAILED,
    PENDING,
    STEP_PREFLIGHT,
    STEP_CHECK_DRIVER,
    STEP_CHECK_AUTOWARE,
    STEP_AUTOWARE_DOWN,
    STAFF_STEPS,
    STEP_CALIBRATION,
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
        n_steps = len(STAFF_STEPS)
        self.assertEqual(min_lines(n_steps, extra=1), min_lines(n_steps) + 1)


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


class TestStackContainers(unittest.TestCase):
    def test_counts_every_running_container_on_the_host(self):
        root = Path("/vehicle/repo")
        result = subprocess.CompletedProcess([], 0, "first\nsecond\n", "")
        with mock.patch("tui._run", return_value=result) as run:
            self.assertEqual(stack_containers(root), 2)
        run.assert_called_once_with(["docker", "ps", "--quiet"], root)


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
        self.assertIn("Update the accel/brake maps and IMU bias", screen.rows[3])
        for idx in range(3, len(PARTICIPANT_STEPS) + 3):
            self.assertNotIn("running:", screen.rows[idx])
            self.assertNotIn("stopped:", screen.rows[idx])
            self.assertNotIn("rosbag", screen.rows[idx])


class TestRecommendedPlacement(unittest.TestCase):
    def draw(self, cols, lines=None):
        if lines is None:
            lines = min_lines(len(PARTICIPANT_STEPS))
        screen = FakeScreen(lines=lines, cols=cols)
        console = Console(screen, steps=PARTICIPANT_STEPS, role=ROLE_PARTICIPANT)
        console.cursor = 0
        console.failures = ["error details"]
        console.log = ["latest output"]
        with mock.patch("tui.curses.doupdate"):
            console.draw()
        return console, screen

    def test_recommendation_is_at_the_right_edge_of_the_update_row(self):
        _, screen = self.draw(cols=80)
        row = screen.rows[3]
        self.assertIn(step_by_id(STEP_CALIBRATION).title, row)
        self.assertTrue(row.endswith("(Recommended)"))
        self.assertEqual(len(row), 79)
        self.assertIn("2 ?  autoware-vehicle", screen.rows[4])
        self.assertEqual(sum("(Recommended)" in row for row in screen.rows.values()), 1)

    def test_minimum_terminal_wraps_label_and_keeps_failures_and_log_visible(self):
        _, screen = self.draw(cols=MIN_COLS)
        self.assertIn(step_by_id(STEP_CALIBRATION).title, screen.rows[3])
        self.assertEqual(screen.rows[4].strip(), "(Recommended)")
        self.assertEqual(len(screen.rows[4]), MIN_COLS - 1)
        self.assertIn("2 ?  autoware-vehicle", screen.rows[5])
        self.assertIn("4 OK autoware-vehicle down", screen.rows[7])
        self.assertEqual(screen.rows[9], "error details")
        self.assertEqual(screen.rows[11], "latest output")

    def test_wrapped_label_does_not_become_a_separate_keyboard_selection(self):
        console, _ = self.draw(cols=MIN_COLS)
        with mock.patch.object(console, "run_step") as run:
            console.handle_key(ord("\n"))
            run.assert_called_once_with(STEP_CALIBRATION)
        console.handle_key(curses.KEY_DOWN)
        with mock.patch.object(console, "run_step") as run:
            console.handle_key(ord("\n"))
            run.assert_called_once_with(STEP_UP)


class TestShouldReobserve(unittest.TestCase):
    def test_not_while_a_step_is_running(self):
        # observe() blocks the draw thread, and the step's exit re-observes.
        self.assertFalse(should_reobserve(True, 1000.0, 0.0))

    def test_not_before_the_interval_elapses(self):
        self.assertFalse(should_reobserve(False, 1.9, 0.0))

    def test_at_the_interval(self):
        self.assertTrue(should_reobserve(False, 2.0, 0.0))

    def test_after_the_interval(self):
        # An external `make down_all` while idle has to show up on its own.
        self.assertTrue(should_reobserve(False, 10.0, 0.0))


class TestRoleCheckLifecycle(unittest.TestCase):
    def test_observed_shutdown_forgets_success_until_check_runs_again(self):
        for role, steps, check, services in (
            (ROLE_PARTICIPANT, PARTICIPANT_STEPS, STEP_CHECK_AUTOWARE, {"autoware"}),
            (ROLE_STAFF, STAFF_STEPS, STEP_CHECK_DRIVER, {"driver", "zenoh"}),
        ):
            with self.subTest(role=role):
                console = Console(None, steps, role)
                console.session[check] = DONE
                # A different pane stops the services and subsequently starts them again.
                with mock.patch("tui.running_services", side_effect=[frozenset(), frozenset(services)]), \
                        mock.patch("tui.stack_containers", return_value=0):
                    console.observe()
                    self.assertNotIn(check, console.session)
                    console.observe()
                    self.assertNotIn(check, console.session)
                # A failed check on a stopped service must still show NG.
                console.session[check] = FAILED
                with mock.patch("tui.running_services", return_value=frozenset()), \
                        mock.patch("tui.stack_containers", return_value=0):
                    console.observe()
                self.assertEqual(console.session[check], FAILED)

    def test_only_staff_runs_preflight_on_open(self):
        from tui import _loop

        for role in (ROLE_PARTICIPANT, ROLE_STAFF):
            with self.subTest(role=role), mock.patch("tui.Console") as factory, \
                    mock.patch("tui.curses.curs_set"):
                screen = mock.Mock()
                screen.getch.return_value = ord("q")
                factory.return_value.handle_key.return_value = False
                self.assertEqual(_loop(screen, role), 0)
                if role == ROLE_STAFF:
                    factory.return_value.run_step.assert_called_once_with(STEP_PREFLIGHT)
                else:
                    factory.return_value.run_step.assert_not_called()

    def test_service_actions_invalidate_only_their_checks_even_if_the_action_fails(self):
        cases = (
            (STEP_DRIVER, {STEP_CHECK_DRIVER}),
            (STEP_ZENOH, {STEP_CHECK_DRIVER}),
            (STEP_DRIVER_DOWN, {STEP_CHECK_DRIVER}),
            (STEP_ZENOH_DOWN, {STEP_CHECK_DRIVER}),
            (STEP_UP, {STEP_CHECK_AUTOWARE}),
            (STEP_AUTOWARE_DOWN, {STEP_CHECK_AUTOWARE}),
            (STEP_TEARDOWN, {STEP_CHECK_DRIVER, STEP_CHECK_AUTOWARE}),
            (STEP_ROSBAG, set()),
            (STEP_ROSBAG_DOWN, set()),
        )
        for action, invalidated in cases:
            with self.subTest(action=action):
                console = Console(None)
                console.session = {STEP_CHECK_DRIVER: DONE, STEP_CHECK_AUTOWARE: DONE,
                                   STEP_PREFLIGHT: DONE, STEP_CALIBRATION: DONE}
                with mock.patch("tui.threading.Thread"), \
                        mock.patch.object(console, "_run_interactive") as interactive, \
                        mock.patch.object(console, "observe"):
                    console.run_step(action)
                    if step_by_id(action).interactive:
                        interactive.assert_called_once_with(step_by_id(action))
                        console.session[action] = FAILED
                    else:
                        interactive.assert_not_called()
                        console.log_queue.put(("exit", (action, 1)))
                        console.drain()
                for check in (STEP_CHECK_DRIVER, STEP_CHECK_AUTOWARE):
                    self.assertEqual(console.session.get(check, PENDING),
                                     PENDING if check in invalidated else DONE)
                self.assertEqual(console.session[STEP_PREFLIGHT], DONE)
                self.assertEqual(console.session[STEP_CALIBRATION], DONE)
                self.assertEqual(console.session[action], FAILED)

    def test_staff_minimum_terminal_shows_nine_steps_failures_and_log(self):
        screen = FakeScreen(lines=min_lines(len(STAFF_STEPS), extra=1), cols=MIN_COLS)
        console = Console(screen, STAFF_STEPS, ROLE_STAFF)
        console.failures = ["driver check failed"]
        console.log = ["latest output"]
        with mock.patch("tui.curses.doupdate"):
            console.draw()
        self.assertIn("1 -  check preflight", screen.rows[4])
        self.assertIn("4 ?  check driver / zenoh", screen.rows[7])
        self.assertIn("9 OK down all", screen.rows[12])
        self.assertIn("driver check failed", screen.rows.values())
        self.assertIn("latest output", screen.rows.values())


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
