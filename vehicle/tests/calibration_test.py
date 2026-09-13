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
    IMU_PARAM, MAP_DIR, atomic_write, parse_offsets, read_map,
    replace_offsets, save_bias, vehicle_id, write_new_offsets,
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
        for name in ("accel_map.csv", "brake_map.csv"):
            (self.calibration / name).write_text(MAP)
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

    def run_extract(self, archive=None, password="password"):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return extract(archive or self.archive, password, self.output, self.calibration)

    def assert_existing_untouched(self):
        self.assertEqual((self.target / "previous-team").read_text(), "keep on failure")
        self.assertEqual(list(self.output.iterdir()), [self.target])

    def test_extract_applies_maps_bias_and_preserves_other_settings_and_execute_bit(self):
        self.assertEqual(self.run_extract(), 0)
        self.assertFalse((self.target / "previous-team").exists())
        for name in ("accel_map.csv", "brake_map.csv"):
            self.assertEqual((self.target / MAP_DIR / name).read_bytes(), MAP.encode())
        text = (self.target / IMU_PARAM).read_text()
        self.assertEqual(parse_offsets(text), OFFSETS)
        self.assertIn("# x comment", text)
        self.assertIn("angular_velocity_stddev_xx: 0.123 # participant setting", text)
        self.assertEqual((self.target / "run.sh").stat().st_mode & 0o777, 0o755)

    def test_missing_calibration_files_leave_existing_submission(self):
        for name in ("accel_map.csv", "brake_map.csv", "imu_bias.yaml"):
            with self.subTest(name=name):
                path = self.calibration / name
                data = path.read_bytes()
                path.unlink()
                self.assertEqual(self.run_extract(), 1)
                self.assert_existing_untouched()
                path.write_bytes(data)

    def test_invalid_bias_leaves_existing_submission(self):
        path = self.calibration / "imu_bias.yaml"
        for text in ("", "angular_velocity_offset_x: .nan\n", "extra: 1\n",
                     path.read_text() + "angular_velocity_offset_x: 1\n"):
            with self.subTest(text=text):
                path.write_text(text)
                self.assertEqual(self.run_extract(), 1)
                self.assert_existing_untouched()

    def test_invalid_maps_leave_existing_submission(self):
        for text in ("", "default,0,2\n0,1\n0.1,1,2\n",
                     "default,0,2\n0,nan,1\n0.1,1,2\n",
                     "default,2,0\n0,1,2\n0.1,1,2\n"):
            with self.subTest(text=text):
                (self.calibration / "accel_map.csv").write_text(text)
                self.assertEqual(self.run_extract(), 1)
                self.assert_existing_untouched()

    def test_missing_or_invalid_target_parameters_leave_existing_submission(self):
        for text in (None, PARAM.replace("angular_velocity_offset_z", "other")):
            with self.subTest(text=text):
                with zipfile.ZipFile(self.archive, "w") as archive:
                    archive.writestr("aichallenge_submit/other", "data")
                    if text is not None:
                        archive.writestr(f"aichallenge_submit/{IMU_PARAM}", text)
                self.assertEqual(self.run_extract(), 1)
                self.assert_existing_untouched()

    def test_missing_target_map_leaves_existing_submission(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(f"aichallenge_submit/{IMU_PARAM}", PARAM)
            archive.writestr(f"aichallenge_submit/{MAP_DIR / 'accel_map.csv'}", "map")
        self.assertEqual(self.run_extract(), 1)
        self.assert_existing_untouched()

    def test_cli_selects_vehicle_profile_using_env_file_and_environment(self):
        other = self.calibration.parent / "A3"
        shutil.copytree(self.calibration, other)
        alternate = {"x": 0.004, "y": 0.005, "z": 0.006}
        save_bias(other / "imu_bias.yaml", alternate)
        (self.root / ".env").write_text("VEHICLE_ID=A2\n")
        argv = ["extract_submission.py", "--id", "submission", "--zip-dir", str(self.root),
                "--output", str(self.output)]
        for env, expected in (("", OFFSETS), ("A3", alternate)):
            with self.subTest(env=env), patch.dict(os.environ, {"VEHICLE_ID": env}), \
                    patch("extract_submission.REPO_ROOT", self.root), patch.object(sys, "argv", argv), \
                    patch("extract_submission.DEFAULT_CALIBRATION_DIR", self.calibration.parent), \
                    patch("extract_submission.getpass.getpass", return_value="password"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                self.assertEqual(parse_offsets((self.target / IMU_PARAM).read_text()), expected)

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
        self.assertEqual(parse_offsets((self.target / IMU_PARAM).read_text()), OFFSETS)

    def test_vehicle_selection_environment_overrides_last_env_assignment(self):
        (self.root / ".env").write_text("VEHICLE_ID=A3\n export VEHICLE_ID='A2'\n")
        with patch.dict(os.environ, {"VEHICLE_ID": ""}):
            self.assertEqual(vehicle_id(self.root), "A2")
        with patch.dict(os.environ, {"VEHICLE_ID": "A6"}):
            self.assertEqual(vehicle_id(self.root), "A6")

    def test_vehicle_selection_rejects_missing_and_unsafe_ids(self):
        for value in ("", "../A2", "/A2", "A2;anything"):
            with self.subTest(value=value), patch.dict(os.environ, {"VEHICLE_ID": value}):
                with self.assertRaises(ValueError):
                    vehicle_id(self.root)

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
        self.assertTrue(write_new_offsets(str(link), OFFSETS))
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
                         ["accel_map.csv", "brake_map.csv", "imu_bias.yaml"])

    def test_repository_maps_match_supported_format(self):
        submit = Path(__file__).resolve().parents[2] / "aichallenge/workspace/src/aichallenge_submit"
        for name in ("accel_map.csv", "brake_map.csv"):
            self.assertTrue(read_map(submit / MAP_DIR / name))


if __name__ == "__main__":
    unittest.main()
