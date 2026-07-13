cmake_minimum_required(VERSION 3.8)
project(recovery_supervisor)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

ament_auto_add_executable(recovery_supervisor_node
  src/recovery_supervisor_node.cpp
)

ament_auto_package(INSTALL_TO_SHARE
  config
)
