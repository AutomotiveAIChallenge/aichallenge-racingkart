from pathlib import Path
from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch.actions import (
    DeclareLaunchArgument,
    OpaqueFunction,
)

from launch_ros.actions import Node, SetParameter


def launch_setup(context, *args, **kwargs):
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_obstacle_avoidance = LaunchConfiguration("use_obstacle_avoidance")
    use_boost_acceleration = LaunchConfiguration("use_boost_acceleration")
    use_stats = LaunchConfiguration("use_stats")
    use_cpp_mpc = LaunchConfiguration("use_cpp_mpc")

    config_path = (
        Path(get_package_share_directory("multi_purpose_mpc_ros"))
        / "config"
        / "config.yaml"
    )

    ref_vel_path = (
        Path(get_package_share_directory("multi_purpose_mpc_ros"))
        / "config"
        / "ref_vel.yaml"
    )

    mpc_controller = Node(
        package="multi_purpose_mpc_ros",
        executable="run_mpc_controller.bash",
        name="mpc_controller",
        output="both",
        emulate_tty=True,  # https://github.com/ros2/launch/issues/188
        sigterm_timeout="10",
        arguments=[
            "--config_path",
            str(config_path),
            "--ref_vel_path",
            str(ref_vel_path),
            "--ros-args",
            "--log-level",
            "info",
        ],
        parameters=[
            {"use_boost_acceleration": use_boost_acceleration},
            {"use_obstacle_avoidance": use_obstacle_avoidance},
            {"use_stats": use_stats},
        ],
        condition=UnlessCondition(use_cpp_mpc),
    )

    mpc_controller_cpp = Node(
        package="multi_purpose_mpc_ros",
        executable="mpc_controller_cpp",
        name="mpc_controller",
        output="both",
        emulate_tty=True,  # https://github.com/ros2/launch/issues/188
        sigterm_timeout="10",
        arguments=[
            "--ros-args",
            "--log-level",
            "info",
        ],
        parameters=[
            {"config_path": str(config_path)},
            {"ref_vel_config_path": str(ref_vel_path)},
            {"use_boost_acceleration": use_boost_acceleration},
            {"use_obstacle_avoidance": use_obstacle_avoidance},
            {"use_stats": use_stats},
        ],
        condition=IfCondition(use_cpp_mpc),
    )

    boost_commander = Node(
        package="multi_purpose_mpc_ros",
        executable="boost_commander",
        name="boost_commander",
        output="both",
        emulate_tty=True,  # https://github.com/ros2/launch/issues/188
        arguments=[
            "--ros-args",
            "--log-level",
            "info",
        ],
        condition=IfCondition(use_boost_acceleration),
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
        parameters=[
            {"use_boost_acceleration": use_boost_acceleration},
            {"use_obstacle_avoidance": use_obstacle_avoidance},
        ],
        condition=IfCondition(use_obstacle_avoidance),
    )

    return [
        SetParameter('use_sim_time', use_sim_time),
        mpc_controller, mpc_controller_cpp, boost_commander, path_constraints_provider]


def generate_launch_description():
    arg_configs = [
        # (arg_name, default_value, description)
        ("use_sim_time", "true", "Use simulation time or not"),
        (
            "use_boost_acceleration",
            "false",
            "Use the boost acceleration for AWSIM simulation",
        ),
        (
            "use_obstacle_avoidance",
            "false",
            "Use the functionality of obstacle avoidance",
        ),
        (
            "use_stats",
            "false",
            "Use the execution statistics",
        ),
        (
            "use_cpp_mpc",
            "true",
            "Use the C++ MPC controller instead of the Python controller",
        ),
    ]

    declared_arguments = [
        DeclareLaunchArgument(name, default_value=default, description=description)
        for name, default, description in arg_configs
    ]

    return LaunchDescription(
        declared_arguments + [OpaqueFunction(function=launch_setup)]
    )
