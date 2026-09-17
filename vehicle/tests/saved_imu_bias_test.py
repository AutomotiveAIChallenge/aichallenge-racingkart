"""Saved-profile application without Docker, ROS or real vehicle settings."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibration import (  # noqa: E402
    IMU_PARAM, apply_saved_imu_bias, confirm_update, detect_vehicle_id, parse_offsets, save_bias,
)
from apply_imu_bias import main  # noqa: E402

PARAM = """/**:
  ros__parameters:
    angular_velocity_offset_x: 0.0 # keep comment
    angular_velocity_offset_y: 0.0
    angular_velocity_offset_z: 1e-3
    angular_velocity_stddev_xx: 0.123 # participant setting
"""
OFFSETS = {"x": 0.001, "y": -0.002, "z": 0.003}


class SavedBiasTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.submit = self.root / "submit"
        self.param = self.submit / IMU_PARAM
        self.param.parent.mkdir(parents=True)
        self.param.write_text(PARAM)
        self.profiles = self.root / "profiles"
        self.source = self.profiles / "A2/imu_bias.yaml"
        save_bias(self.source, OFFSETS)
        profile_patch = patch("calibration.DEFAULT_BIAS_DIR", self.profiles)
        profile_patch.start()
        self.addCleanup(profile_patch.stop)

    def apply(self, answer="y", vehicle="A2"):
        with patch("builtins.input", side_effect=[answer]), contextlib.redirect_stdout(io.StringIO()):
            return apply_saved_imu_bias(self.submit, vehicle)

    def test_approved_profile_updates_only_offsets_preserving_source_and_mode(self):
        source = self.source.read_bytes()
        self.param.chmod(0o444)
        self.assertTrue(self.apply())
        text = self.param.read_text()
        self.assertEqual(parse_offsets(text), OFFSETS)
        self.assertIn("# keep comment", text)
        self.assertIn("angular_velocity_stddev_xx: 0.123 # participant setting", text)
        self.assertEqual(self.param.stat().st_mode & 0o777, 0o444)
        self.assertEqual(self.source.read_bytes(), source)

    def test_decline_or_eof_keeps_participant_offsets(self):
        for answer in ("n", "unexpected", EOFError()):
            with self.subTest(answer=answer):
                self.assertFalse(self.apply(answer))
                self.assertEqual(self.param.read_text(), PARAM)

    def test_enter_applies_recommended_profile_after_showing_vehicle_and_values(self):
        output = io.StringIO()
        def answer(prompt):
            self.assertIn("車両 A2", prompt)
            self.assertIn("(Recommended)", prompt)
            self.assertIn("[Y/n]", prompt)
            self.assertIn("参加者の承認", prompt)
            self.assertNotIn("上書き", prompt)
            self.assertIn("z  +0.001000  +0.003000  +0.002000", output.getvalue())
            return ""
        with patch("builtins.input", side_effect=answer), contextlib.redirect_stdout(output):
            self.assertTrue(apply_saved_imu_bias(self.submit, "A2"))
        self.assertEqual(parse_offsets(self.param.read_text()), OFFSETS)

    def test_unavailable_profile_is_not_recommended_and_enter_retains_settings(self):
        for content in (None, "invalid"):
            with self.subTest(content=content):
                self.source.unlink(missing_ok=True)
                if content is not None:
                    self.source.write_text(content)
                with patch("builtins.input", return_value="") as prompt, contextlib.redirect_stdout(io.StringIO()):
                    self.assertFalse(apply_saved_imu_bias(self.submit, "A2"))
                self.assertIn("[y/N]", prompt.call_args.args[0])
                self.assertNotIn("Recommended", prompt.call_args.args[0])
                self.assertEqual(self.param.read_text(), PARAM)

    def test_unset_vehicle_id_is_not_recommended(self):
        with patch("builtins.input", return_value="") as prompt, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(apply_saved_imu_bias(self.submit, ""))
        self.assertNotIn("Recommended", prompt.call_args.args[0])
        self.assertEqual(self.param.read_text(), PARAM)

    def test_other_confirmation_callers_keep_default_no(self):
        with patch("builtins.input", return_value=""):
            self.assertFalse(confirm_update("[y/N]: "))

    def test_missing_invalid_or_nonfinite_source_never_writes(self):
        for content in (None, "invalid", "angular_velocity_offset_x: 1\n",
                        "angular_velocity_offset_x: 1e999\nangular_velocity_offset_y: 0\nangular_velocity_offset_z: 0\n"):
            with self.subTest(content=content):
                self.source.unlink(missing_ok=True)
                if content is not None:
                    self.source.write_text(content)
                self.assertFalse(self.apply("n"))
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.param.read_text(), PARAM)

    def test_missing_unknown_or_unsafe_id_never_uses_another_profile(self):
        for vehicle in ("", "A9", "../A2", "/A2"):
            with self.subTest(vehicle=vehicle), self.assertRaises(ValueError):
                self.apply(vehicle=vehicle)
            self.assertEqual(self.param.read_text(), PARAM)

    def test_selected_vehicle_profile_is_used(self):
        offsets = {"x": -.01, "y": .02, "z": -.03}
        save_bias(self.profiles / "A4/imu_bias.yaml", offsets)
        self.assertTrue(self.apply(vehicle="A4"))
        self.assertEqual(parse_offsets(self.param.read_text()), offsets)

    def test_missing_or_custom_target_skips_update(self):
        for content in (None, "custom IMU settings"):
            with self.subTest(content=content):
                self.param.unlink(missing_ok=True)
                if content is not None:
                    self.param.write_text(content)
                with patch("builtins.input") as prompt, contextlib.redirect_stdout(io.StringIO()):
                    self.assertFalse(apply_saved_imu_bias(self.submit, "A2"))
                    prompt.assert_not_called()
                if content is not None:
                    self.assertEqual(self.param.read_text(), content)

    def test_write_failure_keeps_target_and_profile(self):
        with patch("calibration.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.param.read_text(), PARAM)
        self.assertEqual(parse_offsets(self.source.read_text(), flat=True), OFFSETS)

    def test_values_are_displayed_before_approval_and_concurrent_edit_is_preserved(self):
        output = io.StringIO()
        edited = PARAM.replace("0.123", "0.456")
        def approve(prompt):
            self.assertIn("保存値", output.getvalue())
            self.assertIn("z  +0.001000  +0.003000  +0.002000", output.getvalue())
            self.param.write_text(edited)
            return "y"
        with patch("builtins.input", side_effect=approve), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, "changed during approval"):
                apply_saved_imu_bias(self.submit, "A2")
        self.assertEqual(self.param.read_text(), edited)

    def test_cli_reports_applied_skipped_and_failed(self):
        argv = ["apply_imu_bias.py", "--submit-dir", str(self.submit)]
        for answer, vehicle, code in (("y", "A2", 0), ("", "A2", 0), ("n", "A2", 5), ("y", "A9", 3)):
            with self.subTest(code=code), patch.object(sys, "argv", argv), \
                    patch("apply_imu_bias.detect_vehicle_id", return_value=vehicle), \
                    patch("builtins.input", return_value=answer), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(), code)

    def test_vehicle_id_resolution_prefers_environment_then_last_dotenv_assignment(self):
        (self.root / ".env").write_text('VEHICLE_ID=A2\nexport VEHICLE_ID="A4"\n')
        with patch.dict(os.environ, {"VEHICLE_ID": "A3"}):
            self.assertEqual(detect_vehicle_id(self.root), "A3")
        with patch.dict(os.environ, {"VEHICLE_ID": ""}):
            self.assertEqual(detect_vehicle_id(self.root), "A4")

    def test_vehicle_id_resolution_falls_back_to_shared_hostname_mapping(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        hostname = bin_dir / "hostname"
        hostname.write_text("#!/bin/sh\necho ECU-RK-00\n")
        hostname.chmod(0o755)
        with patch.dict(os.environ, {"VEHICLE_ID": "", "PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            self.assertEqual(detect_vehicle_id(self.root), "A7")


if __name__ == "__main__":
    unittest.main()
