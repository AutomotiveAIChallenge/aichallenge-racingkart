cmake_minimum_required(VERSION 3.8)
project(race_control)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

install(PROGRAMS
  race_control/lap_counter_node.py
  race_control/route_safety_monitor.py
  race_control/route_safety_visualizer.py
  race_control/lanelet_map.py
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY config launch map
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
