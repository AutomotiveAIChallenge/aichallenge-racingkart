# race_control

Race judging tools. First component: **lap_counter** — counts laps and lap
times by detecting start-line crossings.

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
