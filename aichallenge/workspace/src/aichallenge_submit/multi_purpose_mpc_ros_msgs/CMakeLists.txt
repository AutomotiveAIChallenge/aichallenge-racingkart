cmake_minimum_required(VERSION 3.5.0)
project(multi_purpose_mpc_ros_msgs)

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

# Generate messages
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/AckermannControlBoostCommand.msg"
  "msg/BorderCells.msg"
  "msg/PathConstraints.msg"
  DEPENDENCIES autoware_auto_control_msgs
)

ament_auto_package()