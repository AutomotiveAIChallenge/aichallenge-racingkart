cmake_minimum_required(VERSION 3.8)
project(stuck_recovery_controller)

find_package(autoware_cmake REQUIRED)
autoware_package()

ament_auto_add_executable(stuck_recovery_controller_node
  src/stuck_recovery_controller.cpp
)

ament_auto_package()
