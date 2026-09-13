"""Submission/calibration integration tests without Docker or ROS."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibration import (  # noqa: E402
    DEFAULT_MAP_DIR, IMU_PARAM, MAP_DIR, atomic_write, parse_offsets, read_map,
    replace_offsets, save_bias,
)
from extract_submission import extract, main  # noqa: E402

PARAM = """/**:
  ros__parameters:
    angular_velocity_offset_x: 0.0     # x comment
    angular_velocity_offset_y: -0.0
    angular_velocity_offset_z: 1e-3
    angular_velocity_stddev_xx: 0.123 # participant setting
"""
OFFSETS = {"x": 0.001, "y": -0.002, "z": 0.003}
MAP = "default,0,2\n0,-0.3,-0.4\n0.1,0.1,0.2\n"


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calibration = self.root / "calibration" / "A2"
        self.calibration.mkdir(parents=True)
        self.maps = self.root / "awsim_adapter" / "data"
        self.maps.mkdir(parents=True)
        for name in ("accel_map.csv", "brake_map.csv"):
            (self.maps / name).write_text(MAP)
        map_patch = patch("calibration.DEFAULT_MAP_DIR", self.maps)
        map_patch.start()
        self.addCleanup(map_patch.stop)
        save_bias(self.calibration / "imu_bias.yaml", OFFSETS)
        self.output = self.root / "src"
        self.output.mkdir()
        self.target = self.output / "aichallenge_submit"
        self.target.mkdir()
        (self.target / "previous-team").write_text("keep on failure")
        self.archive = self.root / "submission.zip"
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(f"aichallenge_submit/{IMU_PARAM}", PARAM)
            for name in ("accel_map.csv", "brake_map.csv"):
                archive.writestr(f"aichallenge_submit/{MAP_DIR / name}", "participant map")
            script = zipfile.ZipInfo("aichallenge_submit/run.sh")
            script.create_system = 3
            script.external_attr = 0o100755 << 16
            archive.writestr(script, "#!/bin/sh\n")

    def run_extract(self, archive=None, password="password", answer="y"):
        with patch("builtins.input", return_value=answer), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return extract(archive or self.archive, password, self.output)

    def assert_existing_untouched(self):
        self.assertEqual((self.target / "previous-team").read_text(), "keep on failure")
        self.assertEqual(list(self.output.iterdir()), [self.target])

    def test_approved_maps_preserve_participant_imu_settings_and_execute_bit(self):
        self.assertEqual(self.run_extract(), 0)
        self.assertFalse((self.target / "previous-team").exists())
        for name in ("accel_map.csv", "brake_map.csv"):
            self.assertEqual((self.target / MAP_DIR / name).read_bytes(), MAP.encode())
        text = (self.target / IMU_PARAM).read_text()
        self.assertEqual(text, PARAM)
        self.assertIn("# x comment", text)
        self.assertIn("angular_velocity_stddev_xx: 0.123 # participant setting", text)
        self.assertEqual((self.target / "run.sh").stat().st_mode & 0o777, 0o755)

    def test_missing_calibration_files_leave_existing_submission(self):
        for name in ("accel_map.csv", "brake_map.csv"):
            with self.subTest(name=name):
                path = self.maps / name
                data = path.read_bytes()
                path.unlink()
                self.assertEqual(self.run_extract(), 1)
                self.assert_existing_untouched()
                path.write_bytes(data)

    def test_declined_maps_keep_participant_settings_even_without_calibration(self):
        shutil.rmtree(self.maps)
        shutil.rmtree(self.calibration)
        self.assertEqual(self.run_extract(answer="n"), 0)
        for name in ("accel_map.csv", "brake_map.csv"):
            self.assertEqual((self.target / MAP_DIR / name).read_text(), "participant map")
        self.assertEqual((self.target / IMU_PARAM).read_text(), PARAM)

    def test_eof_or_empty_answer_keeps_participant_maps(self):
        for answer in ("", "no", "unexpected", EOFError()):
            with self.subTest(answer=answer):
                input_patch = patch("builtins.input", side_effect=answer) if isinstance(answer, EOFError) else patch("builtins.input", return_value=answer)
                with input_patch, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(extract(self.archive, "password", self.output), 0)
                self.assertEqual((self.target / MAP_DIR / "accel_map.csv").read_text(), "participant map")

    def test_invalid_maps_leave_existing_submission(self):
        for text in ("", "default,0,2\n0,1\n0.1,1,2\n",
                     "default,0,2\n0,nan,1\n0.1,1,2\n",
                     "default,2,0\n0,1,2\n0.1,1,2\n"):
            with self.subTest(text=text):
                (self.maps / "accel_map.csv").write_text(text)
                self.assertEqual(self.run_extract(), 1)
                self.assert_existing_untouched()

    def test_missing_or_custom_imu_settings_do_not_prevent_extraction(self):
        for text in (None, "custom IMU settings"):
            with self.subTest(text=text):
                with zipfile.ZipFile(self.archive, "w") as archive:
                    archive.writestr("aichallenge_submit/other", "data")
                    if text is not None:
                        archive.writestr(f"aichallenge_submit/{IMU_PARAM}", text)
                self.assertEqual(self.run_extract(), 0)
                if text is not None:
                    self.assertEqual((self.target / IMU_PARAM).read_text(), text)

    def test_missing_target_map_is_skipped_but_present_map_can_be_updated(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(f"aichallenge_submit/{IMU_PARAM}", PARAM)
            archive.writestr(f"aichallenge_submit/{MAP_DIR / 'accel_map.csv'}", "map")
        self.assertEqual(self.run_extract(), 0)
        self.assertEqual((self.target / MAP_DIR / "accel_map.csv").read_bytes(), MAP.encode())
        self.assertFalse((self.target / MAP_DIR / "brake_map.csv").exists())

    def test_cli_extract_does_not_require_vehicle_id_or_saved_imu_bias(self):
        argv = ["extract_submission.py", "--id", "submission", "--zip-dir", str(self.root),
                "--output", str(self.output)]
        for env in ("", "A4", "A0"):
            with self.subTest(env=env), patch.dict(os.environ, {"VEHICLE_ID": env}), \
                    patch.object(sys, "argv", argv), \
                    patch("builtins.input", return_value="y"), \
                    patch("extract_submission.getpass.getpass", return_value="password"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                self.assertEqual((self.target / IMU_PARAM).read_text(), PARAM)

    def test_approved_readonly_maps_are_replaced_preserving_mode(self):
        with zipfile.ZipFile(self.archive) as archive:
            entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
        with zipfile.ZipFile(self.archive, "w") as archive:
            for entry, data in entries:
                if entry.filename.endswith("_map.csv"):
                    entry.external_attr = 0o100444 << 16
                archive.writestr(entry, data)
        self.assertEqual(self.run_extract(), 0)
        for name in ("accel_map.csv", "brake_map.csv"):
            target = self.target / MAP_DIR / name
            self.assertEqual(target.read_bytes(), MAP.encode())
            self.assertEqual(target.stat().st_mode & 0o777, 0o444)

    def test_extract_uses_adapter_maps_even_with_old_vehicle_map_copies(self):
        for name in ("accel_map.csv", "brake_map.csv"):
            (self.calibration / name).write_text("old vehicle map")
        self.assertEqual(self.run_extract(), 0)
        for name in ("accel_map.csv", "brake_map.csv"):
            self.assertEqual((self.target / MAP_DIR / name).read_bytes(), MAP.encode())

    @unittest.skipUnless(shutil.which("zip"), "zip is needed for encrypted-archive integration")
    def test_encrypted_zip_wrong_password_then_success(self):
        source = self.root / "archive-source"
        source.mkdir()
        with zipfile.ZipFile(self.archive) as archive:
            archive.extractall(source)
        encrypted = self.root / "encrypted.zip"
        subprocess.run(["zip", "-q", "-r", "-P", "test-only-password", str(encrypted),
                        "aichallenge_submit"], cwd=source, check=True)
        self.assertEqual(self.run_extract(encrypted, "wrong"), 1)
        self.assert_existing_untouched()
        self.assertEqual(self.run_extract(encrypted, "test-only-password"), 0)
        self.assertEqual((self.target / IMU_PARAM).read_text(), PARAM)

    def test_numeric_formats_and_duplicate_target_offsets(self):
        text = PARAM.replace("0.0     #", ".1     #").replace("-0.0", "-2.")
        self.assertEqual(parse_offsets(text), {"x": 0.1, "y": -2., "z": 0.001})
        with self.assertRaises(ValueError):
            replace_offsets(PARAM + "angular_velocity_offset_x: 1\n", OFFSETS)

    def test_atomic_bias_save_and_symlink_parameter_update_preserve_mode(self):
        param = self.root / "param.yaml"
        param.write_text(PARAM)
        param.chmod(0o640)
        link = self.root / "install.yaml"
        link.symlink_to(param)
        atomic_write(link, replace_offsets(link.read_text(), OFFSETS))
        self.assertTrue(link.is_symlink())
        self.assertEqual(parse_offsets(param.read_text()), OFFSETS)
        self.assertEqual(param.stat().st_mode & 0o777, 0o640)
        self.assertEqual(parse_offsets((self.calibration / "imu_bias.yaml").read_text(), flat=True), OFFSETS)

    def test_failed_atomic_save_leaves_previous_bias(self):
        path = self.calibration / "imu_bias.yaml"
        before = path.read_bytes()
        with patch("calibration.os.replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                atomic_write(path, "replacement")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.calibration.iterdir()),
                         ["imu_bias.yaml"])

    def test_repository_maps_and_vehicle_profiles_match_supported_format(self):
        submit = Path(__file__).resolve().parents[2] / "aichallenge/workspace/src/aichallenge_submit"
        for name in ("accel_map.csv", "brake_map.csv"):
            self.assertTrue(read_map(submit / MAP_DIR / name))
            self.assertTrue(read_map(DEFAULT_MAP_DIR / name))
        profiles = Path(__file__).resolve().parents[1] / ".calibration"
        for vehicle in ("A2", "A3", "A4", "A6", "A7", "test"):
            with self.subTest(vehicle=vehicle):
                self.assertEqual(set(parse_offsets(
                    (profiles / vehicle / "imu_bias.yaml").read_text(), flat=True
                )), {"x", "y", "z"})


if __name__ == "__main__":
    unittest.main()
