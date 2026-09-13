"""Exercise measurement persistence using mocked ROS inputs."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration import parse_offsets, save_bias  # noqa: E402
from calibration_test import OFFSETS, PARAM  # noqa: E402


def load_checker():
    modules = {name: ModuleType(name) for name in (
        "rclpy", "rclpy.node", "rclpy.qos", "sensor_msgs", "sensor_msgs.msg",
        "autoware_vehicle_msgs", "autoware_vehicle_msgs.msg",
    )}
    modules["rclpy.node"].Node = object
    modules["rclpy.qos"].qos_profile_sensor_data = object()
    modules["sensor_msgs.msg"].Imu = object
    modules["autoware_vehicle_msgs.msg"].VelocityReport = object
    spec = importlib.util.spec_from_file_location(
        "mocked_imu_checker", Path(__file__).resolve().parents[1] / "check_imu_bias.py"
    )
    checker = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(checker)
    return checker


class MeasurementPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.checker = load_checker()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.param = self.root / "param.yaml"
        self.param.write_text(PARAM)
        self.bias = self.root / "A2/imu_bias.yaml"
        save_bias(self.bias, {"x": 0, "y": 0, "z": 0})
        self.node = MagicMock()
        self.node.sample_count = 10
        self.node.velocity_seen = False
        self.node.stats.return_value = {axis: (value, 0.001) for axis, value in OFFSETS.items()}

    def run_measurement(self, duration="0"):
        argv = ["check_imu_bias.py", "--duration", duration, "--warmup", "0",
                "--param-yaml", str(self.param), "--bias-output", str(self.bias)]
        with patch.object(self.checker, "rclpy", MagicMock()), \
                patch.object(self.checker, "ImuBiasChecker", return_value=self.node), \
                patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            return self.checker.main()

    def test_success_saves_bias_for_next_submission_and_updates_parameter(self):
        self.assertEqual(self.run_measurement(), 0)
        self.assertEqual(parse_offsets(self.param.read_text()), OFFSETS)
        self.assertEqual(parse_offsets(self.bias.read_text(), flat=True), OFFSETS)

    def test_noisy_or_insufficient_samples_do_not_update_either_file(self):
        for noisy in (True, False):
            with self.subTest(noisy=noisy):
                before = (self.param.read_bytes(), self.bias.read_bytes())
                self.node.sample_count = 10 if noisy else 1
                self.node.stats.return_value = {axis: (value, 1) for axis, value in OFFSETS.items()}
                self.assertEqual(self.run_measurement(), 4 if noisy else 3)
                self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)

    def test_invalid_target_does_not_save_measurement(self):
        self.param.write_text("invalid params")
        before = self.bias.read_bytes()
        self.assertEqual(self.run_measurement(), 3)
        self.assertEqual(self.bias.read_bytes(), before)

    def test_movement_during_sampling_does_not_update_either_file(self):
        before = (self.param.read_bytes(), self.bias.read_bytes())
        self.node.velocity_seen = True
        self.node.max_abs_velocity = 1.0
        self.assertEqual(self.run_measurement(duration="1"), 3)
        self.assertEqual((self.param.read_bytes(), self.bias.read_bytes()), before)

    def test_save_failure_is_reported_and_preserves_previous_saved_bias(self):
        before = self.bias.read_bytes()
        with patch.object(self.checker, "save_bias", side_effect=OSError("read only")):
            self.assertEqual(self.run_measurement(), 3)
        self.assertEqual(self.bias.read_bytes(), before)
        self.assertEqual(parse_offsets(self.param.read_text()), OFFSETS)


if __name__ == "__main__":
    unittest.main()
