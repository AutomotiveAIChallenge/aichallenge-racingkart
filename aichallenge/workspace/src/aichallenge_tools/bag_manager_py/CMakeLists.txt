cmake_minimum_required(VERSION 3.8)
project(bag_manager_py)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

# Install Python package
ament_python_install_package(${PROJECT_NAME})

# Install entry point script for the node
install(PROGRAMS
  bag_manager_py/bag_manager_node.py
  DESTINATION lib/${PROJECT_NAME}
)

# Install launch, config and checkpoint files to share
install(DIRECTORY
  launch
  config
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
