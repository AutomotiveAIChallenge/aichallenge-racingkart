cmake_minimum_required(VERSION 3.8)
project(race_control)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

install(PROGRAMS
  race_control/lap_counter_node.py
  race_control/route_safety_monitor.py
  race_control/route_safety_visualizer.py
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY config launch map
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(race_control_pytest test
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  )
endif()

ament_package()
