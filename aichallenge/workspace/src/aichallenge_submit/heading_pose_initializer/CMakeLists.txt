cmake_minimum_required(VERSION 3.8)
project(heading_pose_initializer)

find_package(ament_cmake REQUIRED)

install(PROGRAMS
  script/heading_pose_initializer_node.py
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY data
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
