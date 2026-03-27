cmake_minimum_required(VERSION 3.5)
project(aichallenge_system_launch)

find_package(ament_cmake_auto REQUIRED)
ament_auto_find_build_dependencies()

set(_domain_bridge_template
  ${CMAKE_CURRENT_SOURCE_DIR}/config/domain/vehicle_bridge_config.yaml.in
)
set(_domain_bridge_out_dir ${CMAKE_CURRENT_BINARY_DIR}/config/domain)
file(MAKE_DIRECTORY ${_domain_bridge_out_dir})
set(TO_DOMAIN 0)
foreach(FROM_DOMAIN RANGE 1 4)
  set(VEHICLE_NS d${FROM_DOMAIN})
  set(V2X_OTHER_GNSS_TOPICS "")
  foreach(_other_domain RANGE 1 4)
    if(NOT _other_domain EQUAL FROM_DOMAIN)
      string(APPEND V2X_OTHER_GNSS_TOPICS
        "  d${_other_domain}/sensing/gnss/nav_sat_fix:\n"
        "    type: sensor_msgs/msg/NavSatFix\n"
        "    remap: /v2x/gnss/nav_sat_fix\n"
        "    reversed: True\n"
        "\n"
      )
    endif()
  endforeach()
  set(_domain_bridge_out
    ${_domain_bridge_out_dir}/d${FROM_DOMAIN}_bridge_config.yaml
  )
  configure_file(${_domain_bridge_template} ${_domain_bridge_out} @ONLY)
  install(
    FILES ${_domain_bridge_out}
    DESTINATION share/${PROJECT_NAME}/config/domain
  )
endforeach()

install(PROGRAMS
  script/control_mode_adapter.py
  script/object_marker.py
  script/motion_analytics.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_auto_package(INSTALL_TO_SHARE launch config)
