cmake_minimum_required(VERSION 3.8)
project(awsim_state_manager_py)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/WaitForState.srv"
)

ament_export_dependencies(rosidl_default_runtime)

install(PROGRAMS
  awsim_state_manager_py/awsim_state_manager_node.py
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY
  launch
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
