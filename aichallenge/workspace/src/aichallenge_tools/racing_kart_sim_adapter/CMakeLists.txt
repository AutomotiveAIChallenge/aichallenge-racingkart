cmake_minimum_required(VERSION 3.8)
project(racing_kart_sim_adapter)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_arbiter_core
    test/test_arbiter_core.cpp
    src/arbiter_core.cpp
  )
  target_include_directories(test_arbiter_core PRIVATE include)
  ament_target_dependencies(test_arbiter_core autoware_auto_vehicle_msgs)
endif()

ament_auto_package(INSTALL_TO_SHARE
  launch
)
