cmake_minimum_required(VERSION 3.8)
project(autostart_orchestrator_py)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

install(PROGRAMS
  autostart_orchestrator_py/autostart_orchestrator_node.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
