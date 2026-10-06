"""path_constraints_provider must be constructible.

MPC.__init__ gained ``max_steering_rate`` and ``wp_id_offset`` before
``use_obstacle_avoidance`` / ``use_path_constraints_topic``, but the provider
still passed ten positional arguments ending in ``True, True``: construction
raised ``TypeError: missing 2 required positional arguments``, so the node died
at start-up every time it was launched (mpc_controller.launch.py with
use_obstacle_avoidance:=true).
"""
import os

import ros_stubs

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ros_stubs.install(PKG_DIR)

from multi_purpose_mpc_ros.core.MPC import MPC  # noqa: E402
from multi_purpose_mpc_ros.path_constraints_provider import (  # noqa: E402
    PathConstraintsProvider,
)

CONFIG = os.path.join(PKG_DIR, "config", "config.yaml")


def test_provider_constructs_with_the_shipped_config():
    node = PathConstraintsProvider(CONFIG)
    assert isinstance(node._mpc, MPC)
    # the two trailing ``True`` flags now land on the parameters they meant
    assert node._mpc.use_obstacle_avoidance is True
    assert node._mpc.use_path_constraints_topic is True
    assert not isinstance(node._mpc.max_steering_rate, bool)
    assert not isinstance(node._mpc.wp_id_offset, bool)
    assert "/aichallenge/objects" in [t for t, _ in node._stub_subscriptions]
