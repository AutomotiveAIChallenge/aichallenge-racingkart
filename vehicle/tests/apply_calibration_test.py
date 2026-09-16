"""Exercise pre-start calibration against installed copies and container symlinks."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apply_calibration as command  # noqa: E402
from calibration import IMU_PARAM, MAP_DIR, atomic_write, parse_offsets, save_bias  # noqa: E402
from calibration_test import MAP, OFFSETS, PARAM  # noqa: E402


class ApplyCalibrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.install = self.root / "aichallenge/workspace/install"
        self.install.mkdir(parents=True)
        (self.install / "setup.bash").write_text("# built\n")
        self.original = {
            MAP_DIR / "accel_map.csv": "participant accel\n",
            MAP_DIR / "brake_map.csv": "participant brake\n",
            IMU_PARAM: PARAM,
        }
        self.targets = {}
        for relative, text in self.original.items():
            package, *parts = relative.parts
            target = self.install / package / "share" / package / Path(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            self.targets[relative] = target
        self.maps = self.root / "maps"
        self.maps.mkdir()
        for name in ("accel_map.csv", "brake_map.csv"):
            (self.maps / name).write_text(MAP)
        self.bias_dir = self.root / "bias"
        save_bias(self.bias_dir / "A2/imu_bias.yaml", OFFSETS)
        for name, value in (
            ("apply_calibration.REPO_ROOT", self.root),
            ("calibration.DEFAULT_MAP_DIR", self.maps),
            ("calibration.DEFAULT_BIAS_DIR", self.bias_dir),
        ):
            setting = patch(name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def run_apply(self, answers=("", ""), vehicle="A2"):
        with patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
            command.apply_calibration(self.install, vehicle)

    def assert_retained(self):
        for relative, text in self.original.items():
            self.assertEqual(self.targets[relative].read_text(), text)

    def test_approvals_update_runtime_copies_without_building(self):
        self.targets[IMU_PARAM].chmod(0o444)
        before_bias = (self.bias_dir / "A2/imu_bias.yaml").read_bytes()
        self.run_apply()
        self.assertEqual(self.targets[MAP_DIR / "accel_map.csv"].read_text(), MAP)
        self.assertEqual(self.targets[MAP_DIR / "brake_map.csv"].read_text(), MAP)
        self.assertEqual(parse_offsets(self.targets[IMU_PARAM].read_text()), OFFSETS)
        self.assertIn("angular_velocity_stddev_xx: 0.123", self.targets[IMU_PARAM].read_text())
        self.assertEqual(self.targets[IMU_PARAM].stat().st_mode & 0o777, 0o444)
        self.assertEqual((self.bias_dir / "A2/imu_bias.yaml").read_bytes(), before_bias)

    def test_container_absolute_symlinks_update_host_sources(self):
        source = self.root / "aichallenge/workspace/src/aichallenge_submit"
        for relative, target in self.targets.items():
            actual = source / relative
            actual.parent.mkdir(parents=True, exist_ok=True)
            actual.write_text(self.original[relative])
            target.unlink()
            container_target = Path("/aichallenge") / actual.relative_to(self.root / "aichallenge")
            target.symlink_to(container_target)
        self.run_apply()
        self.assertEqual(parse_offsets((source / IMU_PARAM).read_text()), OFFSETS)
        self.assertEqual((source / MAP_DIR / "accel_map.csv").read_text(), MAP)
        self.assertTrue(all(target.is_symlink() for target in self.targets.values()))

    def test_symlinked_config_directory_is_supported(self):
        config = self.targets[IMU_PARAM].parent
        source = self.root / "aichallenge/workspace/src/aichallenge_submit/imu_corrector/config"
        source.parent.mkdir(parents=True)
        config.rename(source)
        config.symlink_to("/aichallenge/workspace/src/aichallenge_submit/imu_corrector/config")
        self.run_apply()
        self.assertTrue(config.is_symlink())
        self.assertEqual(parse_offsets((source / IMU_PARAM.name).read_text()), OFFSETS)

    def test_merged_install_is_supported(self):
        for package in ("aichallenge_submit_launch", "imu_corrector"):
            original = self.install / package / "share" / package
            destination = self.install / "share" / package
            destination.parent.mkdir(exist_ok=True)
            original.rename(destination)
        self.run_apply()
        self.assertEqual(parse_offsets(command.installed_path(self.install, IMU_PARAM).read_text()), OFFSETS)

    def test_decline_and_eof_keep_both_groups(self):
        for answers in (("n", "n"), (EOFError(), EOFError())):
            with self.subTest(answers=answers):
                self.run_apply(answers)
                self.assert_retained()

    def test_map_and_imu_approvals_are_independent(self):
        self.run_apply(("n", "y"))
        self.assertEqual(self.targets[MAP_DIR / "accel_map.csv"].read_text(), "participant accel\n")
        self.assertEqual(parse_offsets(self.targets[IMU_PARAM].read_text()), OFFSETS)

    def test_unknown_vehicle_approval_does_not_partially_apply_maps(self):
        with self.assertRaises(ValueError):
            self.run_apply(("y", "y"), vehicle="A9")
        self.assert_retained()

    def test_late_write_failure_restores_already_written_map(self):
        def write(path, text):
            if path == self.targets[IMU_PARAM]:
                raise OSError("IMU write failed")
            atomic_write(path, text)
        with patch("apply_calibration.atomic_write", side_effect=write):
            with self.assertRaises(OSError):
                self.run_apply()
        self.assert_retained()

    def test_concurrent_edit_during_approval_is_preserved(self):
        target = self.targets[IMU_PARAM]
        def answer(prompt):
            if "IMU" in prompt:
                target.write_text(PARAM.replace("0.123", "0.456"))
            return "y"
        with patch("builtins.input", side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "changed during approval"):
                command.apply_calibration(self.install, "A2")
        self.assertIn("0.456", target.read_text())
        self.assertEqual(self.targets[MAP_DIR / "accel_map.csv"].read_text(), "participant accel\n")

    def test_missing_install_fails_before_prompt(self):
        (self.install / "setup.bash").unlink()
        with patch("builtins.input") as prompt, self.assertRaisesRegex(ValueError, "built workspace"):
            command.apply_calibration(self.install, "A2")
        prompt.assert_not_called()

    def test_cli_refuses_running_autoware_before_prompt(self):
        services = subprocess.CompletedProcess([], 0, "driver\nautoware\n", "")
        with patch.object(sys, "argv", ["apply_calibration.py", "--install-dir", str(self.install)]), \
                patch("apply_calibration.subprocess.run", return_value=services), \
                patch("builtins.input") as prompt, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(command.main(), 1)
        prompt.assert_not_called()
        self.assert_retained()


if __name__ == "__main__":
    unittest.main()
