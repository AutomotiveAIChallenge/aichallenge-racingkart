from pathlib import Path
from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.substitutions import LaunchConfiguration
from launch.actions import (
    DeclareLaunchArgument,
    OpaqueFunction,
)

from launch_ros.actions import Node


def launch_setup(context, *args, **kwargs):
    use_sim_time = LaunchConfiguration("use_sim_time")

    config_path = (
        Path(get_package_share_directory("multi_purpose_mpc_ros"))
        / "config"
        / "config.yaml"
    )

    path_constraints_provider = Node(
        package="multi_purpose_mpc_ros",
        executable="path_constraints_provider.bash",
        name="path_constraints_provider",
        output="both",
        emulate_tty=True,  # https://github.com/ros2/launch/issues/188
        arguments=[
            "--config_path",
            str(config_path),
            "--ros-args",
            "--log-level",
            "info",
        ],
        parameters=[use_sim_time]
    )

    return [
        path_constraints_provider
    ]


def generate_launch_description():
    arg_configs = [
        # (arg_name, default_value, description)
        ("use_sim_time", "true", "Use simulation time or not"),
    ]

    declared_arguments = [
        DeclareLaunchArgument(name, default_value=default, description=description)
        for name, default, description in arg_configs
    ]

    return LaunchDescription(
        declared_arguments + [OpaqueFunction(function=launch_setup)]
    )
