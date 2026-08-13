#!/usr/bin/env python3
"""Unit tests for the non-interactive selection logic in download_submission.py.

No network and no third-party test runner: python3 -m unittest.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from download_submission import SubmissionLister  # noqa: E402


class TestSelectLatest(unittest.TestCase):
    def test_empty_list_returns_none(self):
        self.assertIsNone(SubmissionLister().select_latest([]))

    def test_picks_max_submission_time(self):
        subs = [
            {"sourceFilePath": "a/old.tar.gz", "submission_time": 1000},
            {"sourceFilePath": "a/new.tar.gz", "submission_time": 3000},
            {"sourceFilePath": "a/mid.tar.gz", "submission_time": 2000},
        ]
        self.assertEqual(
            SubmissionLister().select_latest(subs)["sourceFilePath"], "a/new.tar.gz"
        )

    def test_missing_submission_time_treated_as_zero(self):
        subs = [
            {"sourceFilePath": "a/no-time.tar.gz"},
            {"sourceFilePath": "a/timed.tar.gz", "submission_time": 1},
        ]
        self.assertEqual(
            SubmissionLister().select_latest(subs)["sourceFilePath"], "a/timed.tar.gz"
        )

    def test_single_entry_without_time_is_returned(self):
        subs = [{"sourceFilePath": "a/only.tar.gz"}]
        self.assertEqual(
            SubmissionLister().select_latest(subs)["sourceFilePath"], "a/only.tar.gz"
        )


if __name__ == "__main__":
    unittest.main()
