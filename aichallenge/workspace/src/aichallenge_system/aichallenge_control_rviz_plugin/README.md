# aichallenge_control_rviz_plugin

RViz2 panel plugin for the AI Challenge system.

## Panels

### `aichallenge_control_rviz_plugin/ControlModePanel`

Provides buttons for:

- `Auto Mode Start` / `Auto Mode Stop`: publish `std_msgs/Bool` to request autonomous control mode.
- `Initial Pose Set`: publish `/initialpose` using GNSS position and trajectory heading.

## Initial Pose Set specification

### Inputs

- GNSS pose (position + covariance)
  - Topic: `/sensing/gnss/pose_with_covariance`
  - Type: `geometry_msgs/msg/PoseWithCovarianceStamped`
- Trajectory
  - Topic: `/planning/scenario_planning/trajectory`
  - Type: `autoware_auto_planning_msgs/msg/Trajectory`

### Output

- Initial pose
  - Topic: `/initialpose`
  - Type: `geometry_msgs/msg/PoseWithCovarianceStamped`
  - Note: `/initialpose` is relayed to `/localization/initial_pose3d` by `aichallenge_system_launch`.

### Behavior (button click)

1. Use the latest received GNSS pose as the base.
2. Find the closest trajectory point to the GNSS position (2D distance on x-y).
3. Compute yaw from **adjacent** trajectory points:
   - Prefer the forward segment `p[i] -> p[i+1]`.
   - If the forward segment is degenerate (zero length) or `i` is the last point, use the backward segment `p[i-1] -> p[i]`.
   - If necessary, scan forward/backward until a non-zero segment is found.
4. Create an orientation quaternion from the computed yaw and overwrite `pose.pose.orientation`.
5. Keep GNSS covariance as-is, but overwrite yaw covariance:
   - `covariance[35] = 0.5` (i.e., `rot_z` variance in the 6x6 covariance matrix).
6. Publish to `/initialpose`.

### Frame handling

- If `header.frame_id` differs between GNSS and trajectory, the panel **still proceeds** (no TF transform is performed).

### Failure handling

- If GNSS or trajectory has not been received, or trajectory has fewer than 2 valid points/segments:
  - Do not publish `/initialpose`.
  - Show a status message in the panel and log a warning.
