cmake_minimum_required(VERSION 3.8)
project(teleop_manager)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake_auto REQUIRED)
find_package(X11 REQUIRED)

ament_auto_find_build_dependencies()

ament_auto_add_executable(teleop_manager_node
  src/teleop_manager_node.cpp
)

ament_auto_add_executable(keyboard_to_joy_node
  src/keyboard_to_joy_node.cpp
)

ament_auto_add_executable(keyboard_x11_to_joy_node
  src/keyboard_x11_to_joy_node.cpp
)
target_include_directories(keyboard_x11_to_joy_node PRIVATE ${X11_INCLUDE_DIR})
target_link_libraries(keyboard_x11_to_joy_node ${X11_LIBRARIES})

ament_auto_package(INSTALL_TO_SHARE
  config
  launch
)
