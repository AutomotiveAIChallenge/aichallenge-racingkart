"""Minimal stand-ins for rclpy and the ROS message packages.

Lets node modules (``mpc_controller``, ``path_constraints_provider``) be
imported and partially exercised by pytest on a host without a ROS 2
installation.  Only what those modules touch at import / construction time is
provided; anything else resolves to a permissive dummy.
"""
import os
import sys
import types


class _Dummy:
    """Accepts any constructor arguments / attribute access / call."""

    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __call__(self, *args, **kwargs):
        return _Dummy()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Dummy()


class _DummyModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (_Dummy,), {})


class _Logger:
    def __init__(self):
        self.messages = []

    def _log(self, msg, *args, **kwargs):
        self.messages.append(str(msg))

    info = warn = warning = error = debug = _log


class Node:
    """Records subscriptions / parameter callbacks instead of talking to DDS."""

    def __init__(self, name, *args, **kwargs):
        self._stub_name = name
        self._stub_logger = _Logger()
        self._stub_params = {}
        self._stub_param_callbacks = []
        self._stub_subscriptions = []
        self._stub_publishers = []

    def get_logger(self):
        if not hasattr(self, "_stub_logger"):
            self._stub_logger = _Logger()
        return self._stub_logger

    def declare_parameter(self, name, value=None, *args, **kwargs):
        if not hasattr(self, "_stub_params"):
            self._stub_params = {}
        self._stub_params[name] = value
        return _Dummy(name=name, value=value)

    def get_parameter(self, name):
        value = getattr(self, "_stub_params", {}).get(name, False)
        return _Dummy(get_parameter_value=lambda: _Dummy(bool_value=bool(value)))

    def set_parameters(self, params):
        return []

    def add_on_set_parameters_callback(self, cb):
        if not hasattr(self, "_stub_param_callbacks"):
            self._stub_param_callbacks = []
        self._stub_param_callbacks.append(cb)

    def create_subscription(self, msg_type, topic, callback, qos, *args, **kwargs):
        if not hasattr(self, "_stub_subscriptions"):
            self._stub_subscriptions = []
        self._stub_subscriptions.append((topic, callback))
        return _Dummy()

    def create_publisher(self, msg_type, topic, qos, *args, **kwargs):
        if not hasattr(self, "_stub_publishers"):
            self._stub_publishers = []
        self._stub_publishers.append(topic)
        return _Dummy()

    def create_timer(self, *args, **kwargs):
        return _Dummy()

    def create_rate(self, *args, **kwargs):
        return _Dummy()

    def get_clock(self):
        return _Dummy()

    def destroy_node(self):
        pass


class Parameter:
    class Type:
        NOT_SET = 0
        BOOL = 1
        INTEGER = 2
        DOUBLE = 3
        STRING = 4

    def __init__(self, name, type_=None, value=None):
        self.name = name
        self.type_ = type_
        self.value = value


class SetParametersResult:
    def __init__(self, successful=True, reason=""):
        self.successful = successful
        self.reason = reason


class _QoSEnum:
    RELIABLE = BEST_EFFORT = SYSTEM_DEFAULT = "qos"
    TRANSIENT_LOCAL = VOLATILE = "qos"
    KEEP_LAST = KEEP_ALL = "qos"


def install(package_share_dir):
    """Register the stub modules.  ``package_share_dir`` is what
    ``get_package_share_directory('multi_purpose_mpc_ros')`` returns."""
    names = [
        "rclpy", "rclpy.node", "rclpy.parameter", "rclpy.qos", "rclpy.time",
        "rclpy.impl", "rclpy.impl.rcutils_logger", "rclpy.executors",
        "rclpy.signals", "rclpy.utilities",
        "ament_index_python", "ament_index_python.packages",
        "visualization_msgs", "visualization_msgs.msg",
        "std_msgs", "std_msgs.msg", "nav_msgs", "nav_msgs.msg",
        "geometry_msgs", "geometry_msgs.msg",
        "rcl_interfaces", "rcl_interfaces.msg",
        "autoware_auto_control_msgs", "autoware_auto_control_msgs.msg",
        "autoware_auto_planning_msgs", "autoware_auto_planning_msgs.msg",
        "v2x_msgs", "v2x_msgs.msg",
        "multi_purpose_mpc_ros_msgs", "multi_purpose_mpc_ros_msgs.msg",
    ]
    for name in names:
        sys.modules[name] = _DummyModule(name)
    sys.modules["rclpy.node"].Node = Node
    sys.modules["rclpy.parameter"].Parameter = Parameter
    sys.modules["rcl_interfaces.msg"].SetParametersResult = SetParametersResult
    qos = sys.modules["rclpy.qos"]
    qos.QoSProfile = _Dummy
    qos.QoSDurabilityPolicy = qos.QoSReliabilityPolicy = _QoSEnum
    qos.QoSHistoryPolicy = _QoSEnum
    sys.modules["rclpy"].ok = lambda: False
    share = os.path.abspath(package_share_dir)
    sys.modules["ament_index_python.packages"].get_package_share_directory = (
        lambda _pkg: share)
    for parent, child in [("rclpy", "node"), ("rclpy", "parameter"),
                          ("rclpy", "qos"), ("rclpy", "time")]:
        setattr(sys.modules[parent], child, sys.modules[f"{parent}.{child}"])
