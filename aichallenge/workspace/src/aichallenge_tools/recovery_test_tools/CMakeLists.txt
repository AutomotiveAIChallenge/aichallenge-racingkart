cmake_minimum_required(VERSION 3.8)
project(recovery_test_tools)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  recovery_test_tools/analyze_recovery_bag.py
  recovery_test_tools/command_profile_node.py
  recovery_test_tools/watch_recovery_topics.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
