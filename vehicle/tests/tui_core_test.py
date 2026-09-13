#!/usr/bin/env python3
"""Unit tests for vehicle/tui_core.py.

No curses, no subprocess, no filesystem: the Workspace snapshot is built by
hand. Run with python3 -m unittest (no third-party runner).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tui_core import (  # noqa: E402
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    STEP_AUTOWARE_DOWN,
    STEP_BUILD,
    STEP_CLEAN,
    PARTICIPANT_STEPS,
    ROLE_PARTICIPANT,
    ROLE_STAFF,
    STAFF_STEPS,
    STEP_DOWNLOAD,
    STEP_DRIVER,
    STEP_DRIVER_DOWN,
    STEP_ZENOH,
    STEP_ZENOH_DOWN,
    STEP_ROSBAG,
    STEP_ROSBAG_DOWN,
    STEP_PREFLIGHT,
    STEP_RUNTIME,
    STEP_SUBMISSION,
    STEP_TEARDOWN,
    STEP_UP,
    Workspace,
    build_done,
    is_runnable,
    step_by_id,
    step_status,
    steps_for_role,
    has_unmet_requirement,
)

ALL_UP = frozenset({"driver", "autoware", "zenoh", "rosbag"})


def built_ws(**kwargs):
    """A workspace with a submission present and a fresh install/."""
    base = dict(
        submit_mtime=100.0,
        install_mtime=200.0,
    )
    base.update(kwargs)
    return Workspace(**base)


class TestSteps(unittest.TestCase):
    def test_participant_steps_in_execution_order(self):
        self.assertEqual(
            [s.step_id for s in PARTICIPANT_STEPS],
            [
                STEP_PREFLIGHT,
                STEP_SUBMISSION,
                STEP_BUILD,
                STEP_UP,
                STEP_RUNTIME,
                STEP_AUTOWARE_DOWN,
                STEP_CLEAN,
            ],
        )

    def test_staff_sees_exactly_download_per_service_up_down_teardown(self):
        staff = [s.step_id for s in steps_for_role(ROLE_STAFF)]
        self.assertEqual(
            staff,
            [
                STEP_DOWNLOAD,
                STEP_DRIVER,
                STEP_ZENOH,
                STEP_DRIVER_DOWN,
                STEP_ZENOH_DOWN,
                # rosbag は記録の開始・終了なので down all の直前にまとめる
                STEP_ROSBAG,
                STEP_ROSBAG_DOWN,
                STEP_TEARDOWN,
            ],
        )

    def test_staff_has_no_participant_steps_and_no_preflight(self):
        staff = {s.step_id for s in steps_for_role(ROLE_STAFF)}
        participant_ids = {s.step_id for s in PARTICIPANT_STEPS}
        self.assertFalse(staff & participant_ids)
        self.assertNotIn(STEP_PREFLIGHT, staff)

    def test_participant_never_touches_the_infra_or_the_whole_stack(self):
        # driver / zenoh / rosbag の起動・停止、download、make down は運営の仕事。
        participant = {s.step_id for s in steps_for_role(ROLE_PARTICIPANT)}
        self.assertFalse(
            participant
            & {
                STEP_DRIVER,
                STEP_ZENOH,
                STEP_ROSBAG,
                STEP_DRIVER_DOWN,
                STEP_ZENOH_DOWN,
                STEP_ROSBAG_DOWN,
                STEP_DOWNLOAD,
                STEP_TEARDOWN,
            }
        )

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):
            steps_for_role("admin")

    def test_autoware_steps_touch_only_the_autoware_container(self):
        # 参加者の autoware / autoware down は driver / zenoh / rosbag を動かしたまま
        # autoware だけを上げ下げする。土台を触るのは運営の driver / zenoh / rosbag。
        self.assertEqual(step_by_id(STEP_UP).command, ("make", "autoware-vehicle"))
        self.assertEqual(
            step_by_id(STEP_AUTOWARE_DOWN).command,
            ("docker", "compose", "down", "autoware"),
        )
        self.assertEqual(step_by_id(STEP_DRIVER).command, ("make", "driver"))
        self.assertEqual(step_by_id(STEP_ZENOH).command, ("make", "zenoh"))
        self.assertEqual(step_by_id(STEP_ROSBAG).command, ("make", "rosbag"))
        self.assertEqual(
            step_by_id(STEP_DRIVER_DOWN).command, ("docker", "compose", "down", "driver")
        )
        self.assertEqual(
            step_by_id(STEP_ZENOH_DOWN).command, ("docker", "compose", "down", "zenoh")
        )
        self.assertEqual(
            step_by_id(STEP_ROSBAG_DOWN).command, ("docker", "compose", "down", "rosbag")
        )

    def test_cleanup_step_clears_the_workspace(self):
        # cleanup は「ディレクトリを消す」担当。スタックの停止は down が持つ。
        self.assertEqual(step_by_id(STEP_CLEAN).command, ("make", "workspace-clean"))
        self.assertEqual(step_by_id(STEP_TEARDOWN).command, ("make", "down"))

    def test_extract_step_is_interactive(self):
        # extract_submission.py asks for the team id and a hidden password; the
        # console has to release the terminal for it.
        self.assertTrue(step_by_id(STEP_SUBMISSION).interactive)

    def test_extract_step_extracts_the_submission_from_a_zip(self):
        self.assertEqual(
            step_by_id(STEP_SUBMISSION).command, ("make", "submission-extract")
        )

    def test_preflight_step_is_not_interactive(self):
        self.assertFalse(step_by_id(STEP_PREFLIGHT).interactive)

    def test_runtime_step_is_interactive(self):
        # check_imu_bias() が停止確認の y/N プロンプトを出す。console が端末を
        # 明け渡さないと、curses の getch() とプロンプトの read が同じ tty を
        # 取り合い応答不能でハングする。
        self.assertTrue(step_by_id(STEP_RUNTIME).interactive)

    def test_download_step_is_interactive_and_staff_only(self):
        self.assertTrue(step_by_id(STEP_DOWNLOAD).interactive)
        self.assertEqual(step_by_id(STEP_DOWNLOAD).command, ("make", "download"))

    def test_step_by_id_rejects_unknown(self):
        with self.assertRaises(KeyError):
            step_by_id("no-such-step")


class TestBuildDone(unittest.TestCase):
    def test_install_newer_than_src_is_built(self):
        self.assertTrue(build_done(built_ws()))

    def test_equal_mtime_counts_as_built(self):
        self.assertTrue(build_done(built_ws(install_mtime=100.0)))

    def test_install_older_than_src_is_stale(self):
        self.assertFalse(build_done(built_ws(install_mtime=50.0)))

    def test_no_install_is_not_built(self):
        self.assertFalse(build_done(Workspace(submit_mtime=100.0)))

    def test_missing_mtime_is_not_built(self):
        ws = Workspace(install_mtime=1.0)
        self.assertFalse(build_done(ws))

    def test_empty_workspace_is_not_built(self):
        self.assertFalse(build_done(Workspace()))


class TestStepStatus(unittest.TestCase):
    def test_preflight_starts_pending(self):
        self.assertEqual(step_status(STEP_PREFLIGHT, Workspace(), {}), PENDING)

    def test_preflight_reflects_session_result(self):
        ws = Workspace()
        self.assertEqual(step_status(STEP_PREFLIGHT, ws, {STEP_PREFLIGHT: DONE}), DONE)
        self.assertEqual(
            step_status(STEP_PREFLIGHT, ws, {STEP_PREFLIGHT: FAILED}), FAILED
        )

    def test_submission_pending_with_empty_session_even_if_dir_has_content(self):
        # aichallenge_submit/ ships tracked packages, so it always has
        # content on a checkout -- that must not read as "downloaded".
        ws = Workspace(submit_mtime=100.0)
        self.assertEqual(step_status(STEP_SUBMISSION, ws, {}), PENDING)

    def test_submission_done_when_session_records_done(self):
        ws = Workspace(submit_mtime=100.0)
        self.assertEqual(
            step_status(STEP_SUBMISSION, ws, {STEP_SUBMISSION: DONE}), DONE
        )

    def test_submission_failed_when_session_records_failed(self):
        ws = Workspace(submit_mtime=100.0)
        self.assertEqual(
            step_status(STEP_SUBMISSION, ws, {STEP_SUBMISSION: FAILED}), FAILED
        )

    def test_build_measured_from_the_workspace(self):
        self.assertEqual(step_status(STEP_BUILD, built_ws(), {}), DONE)
        self.assertEqual(
            step_status(STEP_BUILD, built_ws(install_mtime=50.0), {}), PENDING
        )

    def test_up_done_when_autoware_runs_even_without_the_infra(self):
        # autoware-vehicle が上げるのは autoware だけ。土台の有無はバッジで見せる。
        ws = built_ws(services_running=frozenset({"autoware"}))
        self.assertEqual(step_status(STEP_UP, ws, {}), DONE)

    def test_up_pending_when_autoware_is_missing(self):
        ws = built_ws(services_running=frozenset({"driver", "zenoh", "rosbag"}))
        self.assertEqual(step_status(STEP_UP, ws, {}), PENDING)

    def test_per_service_up_done_only_when_that_service_runs(self):
        # 各サービスの up は自分だけを見る。他が動いていても関係ない。
        ws = built_ws(services_running=frozenset({"driver"}))
        self.assertEqual(step_status(STEP_DRIVER, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ZENOH, ws, {}), PENDING)
        self.assertEqual(step_status(STEP_ROSBAG, ws, {}), PENDING)

    def test_per_service_down_pending_while_that_service_runs(self):
        ws = built_ws(services_running=frozenset({"driver"}))
        self.assertEqual(step_status(STEP_DRIVER_DOWN, ws, {}), PENDING)
        self.assertEqual(step_status(STEP_ZENOH_DOWN, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ROSBAG_DOWN, ws, {}), DONE)

    def test_per_service_down_done_when_nothing_runs(self):
        ws = built_ws()
        self.assertEqual(step_status(STEP_DRIVER_DOWN, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ZENOH_DOWN, ws, {}), DONE)
        self.assertEqual(step_status(STEP_ROSBAG_DOWN, ws, {}), DONE)

    def test_teardown_done_when_nothing_runs(self):
        self.assertEqual(step_status(STEP_TEARDOWN, built_ws(), {}), DONE)

    def test_teardown_pending_while_services_run(self):
        ws = built_ws(services_running=ALL_UP)
        self.assertEqual(step_status(STEP_TEARDOWN, ws, {}), PENDING)

    def test_teardown_pending_while_another_project_still_runs(self):
        # default プロジェクトの 4 サービスが落ちていても、simulator や -p 2 の
        # autoware が残っていれば make down はまだ済んでいない。
        ws = built_ws(services_running=frozenset(), stack_containers=1)
        self.assertEqual(step_status(STEP_TEARDOWN, ws, {}), PENDING)

    def test_autoware_down_done_when_autoware_is_not_running(self):
        # driver だけ生きていても autoware が落ちていれば済んでいる。
        ws = built_ws(services_running=frozenset({"driver", "zenoh"}))
        self.assertEqual(step_status(STEP_AUTOWARE_DOWN, ws, {}), DONE)

    def test_autoware_down_pending_while_autoware_runs(self):
        ws = built_ws(services_running=frozenset({"autoware"}))
        self.assertEqual(step_status(STEP_AUTOWARE_DOWN, ws, {}), PENDING)

    def test_clean_done_when_workspace_is_pristine(self):
        self.assertEqual(
            step_status(STEP_CLEAN, Workspace(workspace_pristine=True), {}), DONE
        )

    def test_clean_pending_by_default(self):
        # 観測できなかった場合に「済」と出してはいけない。
        self.assertEqual(step_status(STEP_CLEAN, Workspace(), {}), PENDING)

    def test_clean_pending_while_workspace_is_dirty(self):
        self.assertEqual(step_status(STEP_CLEAN, built_ws(), {}), PENDING)

    def test_measured_step_ignores_a_stale_session_entry(self):
        # An external `make down` must show through even though this session
        # recorded the stack as up.
        ws = built_ws(services_running=frozenset())
        self.assertEqual(step_status(STEP_UP, ws, {STEP_UP: DONE}), PENDING)

    def test_running_wins_over_everything(self):
        ws = built_ws(services_running=ALL_UP)
        self.assertEqual(step_status(STEP_UP, ws, {STEP_UP: RUNNING}), RUNNING)


class TestRunnable(unittest.TestCase):
    """Prerequisites are advisory: only a step's own RUNNING status blocks it."""

    def test_preflight_always_runnable(self):
        self.assertTrue(is_runnable(STEP_PREFLIGHT, Workspace(), {}))

    def test_teardown_always_runnable(self):
        self.assertTrue(is_runnable(STEP_TEARDOWN, Workspace(), {}))

    def test_submission_runnable_even_with_preflight_unmet(self):
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), {}))

    def test_submission_runnable_even_after_preflight_failed(self):
        session = {STEP_PREFLIGHT: FAILED}
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), session))

    def test_submission_runnable_after_preflight_passes(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), session))

    def test_build_runnable_without_a_submission(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_BUILD, Workspace(), session))

    def test_up_runnable_even_when_not_built(self):
        session = {STEP_PREFLIGHT: DONE, STEP_SUBMISSION: DONE}
        ws = Workspace(submit_mtime=100.0)
        self.assertTrue(is_runnable(STEP_UP, ws, session))

    def test_up_runnable_once_built(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_UP, built_ws(), session))

    def test_restart_with_empty_session_still_allows_up_once_built(self):
        # Console restarted: the session is empty, so STEP_SUBMISSION reads PENDING again.
        # STEP_BUILD and STEP_UP are measured from disk, so they still report built and up.
        ws = built_ws()
        self.assertEqual(step_status(STEP_SUBMISSION, ws, {}), PENDING)
        self.assertEqual(step_status(STEP_BUILD, ws, {}), DONE)
        self.assertTrue(is_runnable(STEP_UP, ws, {}))

    def test_runtime_runnable_even_when_stack_is_not_up(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertTrue(is_runnable(STEP_RUNTIME, built_ws(), session))

    def test_runtime_runnable_once_the_stack_is_up(self):
        session = {STEP_PREFLIGHT: DONE}
        ws = built_ws(services_running=ALL_UP)
        self.assertTrue(is_runnable(STEP_RUNTIME, ws, session))

    def test_a_failed_step_stays_runnable(self):
        # That is how retry works.
        session = {STEP_PREFLIGHT: DONE, STEP_SUBMISSION: FAILED}
        self.assertTrue(is_runnable(STEP_SUBMISSION, Workspace(), session))

    def test_a_running_step_is_not_runnable(self):
        # No launching a second overlapping run of the same step. This is
        # the one case prerequisites cannot override.
        session = {STEP_PREFLIGHT: DONE, STEP_SUBMISSION: RUNNING}
        self.assertFalse(is_runnable(STEP_SUBMISSION, Workspace(), session))


class TestHasUnmetRequirement(unittest.TestCase):
    def test_false_when_there_are_no_requirements(self):
        self.assertFalse(has_unmet_requirement(STEP_PREFLIGHT, Workspace(), {}))
        self.assertFalse(has_unmet_requirement(STEP_TEARDOWN, Workspace(), {}))

    def test_false_when_the_prerequisite_is_done(self):
        session = {STEP_PREFLIGHT: DONE}
        self.assertFalse(has_unmet_requirement(STEP_SUBMISSION, Workspace(), session))

    def test_true_when_the_prerequisite_is_pending(self):
        self.assertTrue(has_unmet_requirement(STEP_SUBMISSION, Workspace(), {}))

    def test_a_failed_prerequisite_counts_as_unmet(self):
        session = {STEP_PREFLIGHT: FAILED}
        self.assertTrue(has_unmet_requirement(STEP_SUBMISSION, Workspace(), session))

    def test_build_reports_submission_as_unmet_without_a_session_entry(self):
        self.assertTrue(has_unmet_requirement(STEP_BUILD, Workspace(), {}))

if __name__ == "__main__":
    unittest.main()
