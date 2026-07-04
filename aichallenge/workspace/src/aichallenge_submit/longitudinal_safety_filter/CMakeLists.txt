cmake_minimum_required(VERSION 3.8)
project(longitudinal_safety_filter)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(ament_cmake_pytest REQUIRED)

ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  longitudinal_safety_filter/longitudinal_safety_filter_node.py
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY
  launch
  config
  DESTINATION share/${PROJECT_NAME}
)

ament_add_pytest_test(test_safety_filter_logic test/test_safety_filter_logic.py
  APPEND_ENV PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
)

ament_package()
