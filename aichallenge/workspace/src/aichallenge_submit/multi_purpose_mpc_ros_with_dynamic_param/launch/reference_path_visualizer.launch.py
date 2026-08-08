from pathlib import Path
from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    OpaqueFunction,
)

from launch_ros.actions import Node


def launch_setup(context, *args, **kwargs):
    config_path = (
        Path(get_package_share_directory("multi_purpose_mpc_ros"))
        / "config"
        / "config.yaml"
    )

    ref_path_visualizer = Node(
        package="multi_purpose_mpc_ros",
        executable="run_reference_path_visualizer.bash",
        name="ref_path_visualizer",
        output="both",
        emulate_tty=True,  # https://github.com/ros2/launch/issues/188
        arguments=[
            "--config_path",
            str(config_path),
            "--ros-args",
            "--log-level",
            "info",
        ],
    )

    return [ref_path_visualizer]


def generate_launch_description():

    return LaunchDescription(
        [OpaqueFunction(function=launch_setup)]
    )
