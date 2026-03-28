cmake_minimum_required(VERSION 3.8)
project(heading_pose_initializer)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(visualization_msgs REQUIRED)
find_package(std_srvs REQUIRED)

add_executable(heading_pose_initializer_node
  src/heading_pose_initializer_node.cpp
)
ament_target_dependencies(heading_pose_initializer_node
  rclcpp
  geometry_msgs
  visualization_msgs
  std_srvs
)

install(TARGETS heading_pose_initializer_node
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY data
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
