#!/usr/bin/env python3
"""Unit tests for AutostartOrchestrator post-process orchestration.

ROS ノード構築（rclpy spin）を回避するため object.__new__ でインスタンスを作り、
重いサブステップ（_capture / _stop_rosbag_with_postprocess）はスタブする。
ノードモジュールが先頭で rclpy を import するため、コンテナ内（rclpy が import 可能）で実行する。
"""

from __future__ import annotations

import threading
import time

from autostart_orchestrator_node import AutostartOrchestrator


class _FakeLogger:
    def __init__(self) -> None:
        self.warns: list[str] = []

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def info(self, msg: str) -> None:
        pass


class _FakeParam:
    def __init__(self, value) -> None:
        self.value = value


def _make_node(timeout_value=60.0):
    node = AutostartOrchestrator.__new__(AutostartOrchestrator)
    node._capture_stop_lock = threading.Lock()
    node._capture_stop_thread = None
    logger = _FakeLogger()
    node.get_logger = lambda: logger
    node.get_parameter = lambda name: _FakeParam(timeout_value)
    return node, logger


def test_capture_stop_runs_in_parallel_with_rosbag_postprocess():
    node, _logger = _make_node()
    events = {}
    sleep_s = 0.3

    def fake_capture(start):
        assert start is False
        events["capture_start"] = time.monotonic()
        time.sleep(sleep_s)
        events["capture_end"] = time.monotonic()

    def fake_rosbag(enable_motion):
        assert enable_motion is True
        events["rosbag_start"] = time.monotonic()
        time.sleep(sleep_s)
        events["rosbag_end"] = time.monotonic()

    node._capture = fake_capture
    node._stop_rosbag_with_postprocess = fake_rosbag

    t0 = time.monotonic()
    node._finalize_recordings(enable_rosbag=True, enable_capture=True, enable_motion_analytics=True)
    elapsed = time.monotonic() - t0

    assert elapsed < sleep_s * 1.8  # parallel, not serial (~2*sleep)
    assert events["capture_start"] < events["rosbag_end"]  # overlapped
    assert "capture_end" in events  # joined before return
    assert node._capture_stop_thread is None  # reference cleared after join


def test_serial_rosbag_motion_order_preserved():
    node, _logger = _make_node()
    calls = []
    node._capture = lambda start: calls.append(("capture", start))
    node._stop_rosbag_with_postprocess = lambda m: calls.append(("rosbag", m))

    node._finalize_recordings(enable_rosbag=True, enable_capture=True, enable_motion_analytics=True)

    assert ("rosbag", True) in calls
    assert sum(1 for c in calls if c[0] == "rosbag") == 1
    assert sum(1 for c in calls if c[0] == "capture") == 1


def test_capture_only_when_rosbag_disabled():
    node, _logger = _make_node()
    calls = []
    node._capture = lambda start: calls.append(("capture", start))
    node._stop_rosbag_with_postprocess = lambda m: calls.append(("rosbag", m))

    node._finalize_recordings(enable_rosbag=False, enable_capture=True, enable_motion_analytics=True)

    assert calls == [("capture", False)]
    assert node._capture_stop_thread is None


def test_no_capture_thread_when_capture_disabled():
    node, _logger = _make_node()
    calls = []
    node._capture = lambda start: calls.append(("capture", start))
    node._stop_rosbag_with_postprocess = lambda m: calls.append(("rosbag", m))

    node._finalize_recordings(enable_rosbag=True, enable_capture=False, enable_motion_analytics=False)

    assert ("capture", False) not in calls
    assert ("rosbag", False) in calls
    assert node._capture_stop_thread is None


def test_join_times_out_without_hanging():
    node, logger = _make_node(timeout_value=0.1)

    def slow_capture(start):
        time.sleep(0.6)

    node._capture = slow_capture
    node._stop_rosbag_with_postprocess = lambda m: None

    t0 = time.monotonic()
    node._finalize_recordings(enable_rosbag=True, enable_capture=True, enable_motion_analytics=False)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.4  # returned around the join timeout, not capture's full sleep
    assert any("did not finish" in w for w in logger.warns)


def test_rosbag_exception_still_joins_capture():
    node, _logger = _make_node()
    captured = {"done": False}

    def fake_capture(start):
        time.sleep(0.1)
        captured["done"] = True

    def boom(_m):
        raise RuntimeError("rosbag stop failed")

    node._capture = fake_capture
    node._stop_rosbag_with_postprocess = boom

    try:
        node._finalize_recordings(enable_rosbag=True, enable_capture=True, enable_motion_analytics=True)
    except RuntimeError:
        pass

    assert captured["done"] is True  # joined via finally even though rosbag raised
