#!/usr/bin/env python3
"""Regression test for the capture_service_timeout_sec bound (review on #328).

``_capture()`` must not exceed roughly one ``capture_service_timeout_sec``
even when the service becomes available near the end of ``wait_for_service``
and then never responds: the deadline must be computed once and only the
*remaining* time handed to the trigger call, exactly like the
``initial_pose_service_timeout_sec`` path it mirrors.

There is no rclpy in this environment (and the package's other harness,
``test_autostart_orchestrator.py``, needs a running ROS graph / colcon test),
so this stubs the small surface ``autostart_orchestrator_node.py`` touches at
import time, then bypasses ``Node.__init__`` entirely and drives ``_capture``
directly against fake Trigger service clients. No real ROS process involved.
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest


def _install_rclpy_stubs() -> None:
    if getattr(sys.modules.get("rclpy"), "_th_stub", False):
        return  # already installed earlier in this test session

    rclpy_mod = types.ModuleType("rclpy")
    rclpy_mod._th_stub = True

    class _Context:
        def on_shutdown(self, _cb):
            pass

    _context = _Context()
    rclpy_mod.get_default_context = lambda: _context
    rclpy_mod.ok = lambda: True
    rclpy_mod.shutdown = lambda: None

    class _NullLogger:
        def info(self, *_a, **_kw):
            pass

        def warn(self, *_a, **_kw):
            pass

        def error(self, *_a, **_kw):
            pass

    class _FakeParam:
        def __init__(self, value):
            self.value = value

    class _Node:
        def __init__(self, _name):
            self._th_params = {}

        def declare_parameter(self, name, default=None, descriptor=None):
            self._th_params.setdefault(name, default)

        def get_parameter(self, name):
            return _FakeParam(self._th_params.get(name))

        def create_subscription(self, *_a, **_kw):
            return object()

        def create_client(self, *_a, **_kw):
            return object()

        def create_publisher(self, *_a, **_kw):
            return object()

        def get_logger(self):
            return _NullLogger()

    node_mod = types.ModuleType("rclpy.node")
    node_mod.Node = _Node

    cbg_mod = types.ModuleType("rclpy.callback_groups")

    class _ReentrantCallbackGroup:
        pass

    cbg_mod.ReentrantCallbackGroup = _ReentrantCallbackGroup

    rcl_interfaces_mod = types.ModuleType("rcl_interfaces")
    rcl_interfaces_msg_mod = types.ModuleType("rcl_interfaces.msg")

    class _ParameterDescriptor:
        def __init__(self, **_kw):
            pass

    rcl_interfaces_msg_mod.ParameterDescriptor = _ParameterDescriptor

    std_msgs_mod = types.ModuleType("std_msgs")
    std_msgs_msg_mod = types.ModuleType("std_msgs.msg")

    class _Bool:
        def __init__(self):
            self.data = False

    class _String:
        def __init__(self):
            self.data = ""

    std_msgs_msg_mod.Bool = _Bool
    std_msgs_msg_mod.String = _String

    std_srvs_mod = types.ModuleType("std_srvs")
    std_srvs_srv_mod = types.ModuleType("std_srvs.srv")

    class _TriggerRequest:
        pass

    class _TriggerResponse:
        def __init__(self, success=True, message=""):
            self.success = success
            self.message = message

    class _Trigger:
        Request = _TriggerRequest
        Response = _TriggerResponse

    std_srvs_srv_mod.Trigger = _Trigger

    sys.modules["rclpy"] = rclpy_mod
    sys.modules["rclpy.node"] = node_mod
    sys.modules["rclpy.callback_groups"] = cbg_mod
    sys.modules["rcl_interfaces"] = rcl_interfaces_mod
    sys.modules["rcl_interfaces.msg"] = rcl_interfaces_msg_mod
    sys.modules["std_msgs"] = std_msgs_mod
    sys.modules["std_msgs.msg"] = std_msgs_msg_mod
    sys.modules["std_srvs"] = std_srvs_mod
    sys.modules["std_srvs.srv"] = std_srvs_srv_mod


_install_rclpy_stubs()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autostart_orchestrator_node as orch_mod  # noqa: E402


class _NullLogger:
    def info(self, *_a, **_kw):
        pass

    def warn(self, *_a, **_kw):
        pass

    def error(self, *_a, **_kw):
        pass


class _FakeParam:
    def __init__(self, value):
        self.value = value


def _make_node(capture_service_timeout_sec: float, client) -> "orch_mod.AutostartOrchestrator":
    """Build an AutostartOrchestrator instance without running Node.__init__
    (which would need a real rclpy graph and would start the worker thread)."""
    node = orch_mod.AutostartOrchestrator.__new__(orch_mod.AutostartOrchestrator)
    params = {"capture_service_timeout_sec": capture_service_timeout_sec}
    node.get_parameter = lambda name: _FakeParam(params[name])  # type: ignore[method-assign]
    node.get_logger = lambda: _NullLogger()  # type: ignore[method-assign]
    node._cli_capture = client
    node._capture_started = False
    return node


class _NeverResolvingFuture:
    def add_done_callback(self, _cb):
        pass  # deliberately never calls back -> event never set


class _AbsentServiceClient:
    """The service never appears (RViz crashed, or run_rviz:=false)."""

    def wait_for_service(self, timeout_sec=None):
        if timeout_sec is not None:
            time.sleep(timeout_sec)
        return False

    def call_async(self, _req):  # pragma: no cover - must not be reached
        raise AssertionError("call_async must not run when the service never appears")


class _NonRespondingLateClient:
    """The service becomes available near the end of wait_for_service, and
    then never responds to the trigger call."""

    def __init__(self, appear_after_fraction: float):
        self._appear_after_fraction = appear_after_fraction

    def wait_for_service(self, timeout_sec=None):
        if timeout_sec is not None:
            time.sleep(timeout_sec * self._appear_after_fraction)
        return True

    def call_async(self, _req):
        return _NeverResolvingFuture()

    def remove_pending_request(self, _future):
        pass


def test_absent_service_is_bounded_by_the_timeout():
    timeout = 0.2
    node = _make_node(timeout, _AbsentServiceClient())

    start = time.monotonic()
    node._capture(start=True)
    elapsed = time.monotonic() - start

    assert elapsed < timeout * 1.5
    assert node._capture_started is False


def test_capture_wait_and_call_share_one_deadline_not_two():
    """This is the case the review flagged: a service that appears at the
    end of wait_for_service() and then never responds. Passing the *full*
    timeout to both wait_for_service and the trigger call can block for
    almost 2x capture_service_timeout_sec; passing only the remaining time
    to the call bounds the whole step by ~1x.
    """
    timeout = 0.3
    node = _make_node(timeout, _NonRespondingLateClient(appear_after_fraction=0.9))

    start = time.monotonic()
    node._capture(start=True)
    elapsed = time.monotonic() - start

    assert elapsed < timeout * 1.5, (
        f"capture step took {elapsed:.3f}s, more than 1.5x capture_service_timeout_sec "
        f"({timeout}s) -- wait_for_service and the trigger call are not sharing one deadline"
    )
    assert node._capture_started is False


def test_negative_timeout_keeps_the_legacy_unbounded_wait_contract():
    """timeout <= 0 must still map to timeout_arg=None (unbounded wait), same
    as the initial-pose timeout path; this does not exercise a real
    unbounded wait, only that a fast client is still called correctly."""

    class _FastClient:
        def wait_for_service(self, timeout_sec=None):
            assert timeout_sec is None
            return True

        def call_async(self, _req):
            future = types.SimpleNamespace()
            fut_result = orch_mod.Trigger.Response(success=True, message="ok")

            def add_done_callback(cb):
                cb(types.SimpleNamespace(result=lambda: fut_result))

            future.add_done_callback = add_done_callback
            return future

    node = _make_node(0.0, _FastClient())
    node._capture(start=True)
    assert node._capture_started is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
