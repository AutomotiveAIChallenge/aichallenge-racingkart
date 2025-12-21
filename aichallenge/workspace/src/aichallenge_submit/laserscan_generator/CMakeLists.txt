cmake_minimum_required(VERSION 3.8)
project(laserscan_generator)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake_auto REQUIRED)

ament_auto_find_build_dependencies()

ament_auto_add_executable(laserscan_generator_node
  src/laserscan_generator_node.cpp
)

ament_auto_package(INSTALL_TO_SHARE 
  launch 
  map 
  config
)
