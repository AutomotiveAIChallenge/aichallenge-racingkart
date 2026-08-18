cmake_minimum_required(VERSION 3.8)
project(racing_kart_msgs)

# NOTE: これは racing_kart_interface の racing_kart_msgs の「部分複製」で、
#       racing_kart_manager が使う VehicleDebug だけを生成する。
#       詳細と注意点は README.md を参照。

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/VehicleDebug.msg"
  DEPENDENCIES
    "builtin_interfaces"
  ADD_LINTER_TESTS
)

ament_auto_package()
