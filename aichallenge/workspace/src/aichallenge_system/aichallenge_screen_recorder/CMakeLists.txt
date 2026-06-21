cmake_minimum_required(VERSION 3.14)
project(aichallenge_screen_recorder)

find_package(autoware_cmake REQUIRED)
autoware_package()
find_package(OpenCV REQUIRED)
find_package(Qt5 REQUIRED Core Gui)
set(QT_LIBRARIES Qt5::Core Qt5::Gui)
set(CMAKE_AUTOMOC ON)
set(CMAKE_INCLUDE_CURRENT_DIR ON)

include_directories(
  ${OpenCV_INCLUDE_DIRS}
)

ament_auto_add_executable(screen_recorder_node
  src/screen_recorder_node.hpp
  src/screen_recorder_node.cpp
  src/encoder_worker.cpp
)
target_link_libraries(screen_recorder_node
  ${QT_LIBRARIES}
  ${OpenCV_LIBRARIES}
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_encoder_worker
    test/test_encoder_worker.cpp
    src/encoder_worker.cpp
  )
  target_include_directories(test_encoder_worker PRIVATE src)
  target_link_libraries(test_encoder_worker ${OpenCV_LIBRARIES})
endif()

ament_auto_package(
  INSTALL_TO_SHARE
    launch
)
