cmake_minimum_required(VERSION 3.8)
project(pilot_net_controller)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

# Install Python package
ament_python_install_package(${PROJECT_NAME})

# Install entry point script for the node
install(PROGRAMS
  pilot_net_controller/pilot_net_controller_node.py
  DESTINATION lib/${PROJECT_NAME}
)

# Install launch, config and checkpoint files to share
install(DIRECTORY
  launch
  config
  ckpt
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
