# race_control

Race judging tools.

- **lap_counter** — counts laps and lap times by detecting start-line
  crossings (see below).
- **route_safety_monitor** — flags when the vehicle leaves the drivable route
  area, with an optional real-time OpenCV visualizer.

## lap_counter

## How it works

- At startup, the lanelet2 map (`.osm`, with `local_x`/`local_y` tags) is
  parsed once and the start line is derived from the entry edge of the lanelet
  given by `start_lanelet_id` (segment from the first left-bound node to the
  first right-bound node).
- Each `/localization/kinematic_state` message, the vehicle position's signed
  side relative to the start-line segment is computed; a side change while
  within the segment extent (+ `line_margin`) counts as a crossing.
- The first crossing starts lap 1; subsequent crossings increment the lap count
  and record the lap time (odometry stamp based). Crossings within
  `min_lap_time` seconds are ignored (debounce).

## Topics

| Topic | Type | Description |
|-------|------|-------------|
| `~/lap_count` | `std_msgs/Int32` | Completed lap count (0 after the first crossing) |
| `~/last_lap_time` | `std_msgs/Float64` | Most recent lap time [s] |
| `~/current_lap_time` | `std_msgs/Float64` | Elapsed time of the current lap [s] |
| `~/summary` | `std_msgs/String` | `lap=N lap_times=[...]` |

## Usage

```bash
ros2 launch race_control race_control.launch.xml
# or with an explicit map:
ros2 launch race_control race_control.launch.xml map_path:=/path/to/lanelet2_map.osm
```

Parameters: `config/lap_counter.param.yaml` (`start_lanelet_id`,
`min_lap_time`, `line_margin`, `odom_topic`).

## route_safety_monitor

Detects route deviation. At startup it parses the lanelet2 route map
(`map/route_area.osm`, `local_x`/`local_y` tags) into per-lanelet polygons
(left bound + reversed right bound). Every 0.5 s it runs a ray-casting
point-in-polygon test against the latest `/localization/kinematic_state`
position and publishes whether the vehicle is off-route.

### Topics

| Topic | Type | Description |
|-------|------|-------------|
| `/localization/kinematic_state` (sub) | `nav_msgs/Odometry` | Vehicle position |
| `/vehicle/emergency/is_route_deviation` (pub) | `std_msgs/Bool` | `true` while off-route |

### Usage

```bash
ros2 launch race_control route_safety_monitor.launch.xml
# with the real-time OpenCV visualizer:
ros2 launch race_control route_safety_monitor.launch.xml visualize:=true
```

The visualizer (`route_safety_visualizer.py`) opens an OpenCV window with the
route map, vehicle position, fade-out trail and a status HUD.
