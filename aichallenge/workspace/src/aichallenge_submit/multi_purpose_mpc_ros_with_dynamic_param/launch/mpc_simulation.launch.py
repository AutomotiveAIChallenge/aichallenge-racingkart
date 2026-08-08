from pathlib import Path
from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    OpaqueFunction,
)

from launch_ros.actions import Node


def launch_setup(context, *args, **kwargs):
    config_path = (
        Path(get_package_share_directory("multi_purpose_mpc_ros"))
        / "config"
        / "sim_config.yaml"
    )

    mpc_simulation = Node(
        package="multi_purpose_mpc_ros",
        executable="run_mpc_simulation.bash",
        name="mpc_simulation",
        output="both",
        emulate_tty=True,  # https://github.com/ros2/launch/issues/188
        arguments=[
            "--config_path",
            str(config_path),
            "--ros-args",
            "--log-level",
            "info",
        ],
        parameters=[
            {"use_boost_acceleration": False},
            {"use_obstacle_avoidance": True},
        ],
    )

    return [
        mpc_simulation,
    ]


def generate_launch_description():
    arg_configs = [
        # (arg_name, default_value, description)
    ]

    declared_arguments = [
        DeclareLaunchArgument(name, default_value=default, description=description)
        for name, default, description in arg_configs
    ]

    return LaunchDescription(
        declared_arguments + [OpaqueFunction(function=launch_setup)]
    )
