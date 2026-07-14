cmake_minimum_required(VERSION 3.8)
project(stuck_recovery_controller)

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

ament_auto_add_executable(stuck_recovery_controller_node
  src/stuck_recovery_controller_node.cpp
)

ament_auto_package()
