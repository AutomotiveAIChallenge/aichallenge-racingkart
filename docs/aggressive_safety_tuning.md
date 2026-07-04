# Aggressive Safety Tuning Snapshot

## Purpose

The previous settings were conservative and tended to slow or block avoidance too early. This file keeps a snapshot of those safer settings before switching to a more aggressive race-oriented configuration.

## Previous V2X Avoidance Planner Settings

File: `aichallenge/workspace/src/aichallenge_submit/v2x_avoidance_planner/config/v2x_avoidance_planner.param.yaml`

```yaml
horizon_points: 90
transition_points: 12
candidate_offsets_m: [0.0, -0.5, 0.5, -1.0, 1.0, -1.5, 1.5]
initial_offset_ratio: 0.8
emergency_lateral_escape_distance_m: 6.0
previous_trajectory_reuse_duration_sec: 2.0
previous_trajectory_min_offset_m: 0.2
min_output_velocity_mps: 0.0
max_lateral_accel_mps2: 3.0
max_curvature_rate: 0.8
min_velocity_mps: 1.0
ego_radius_m: 0.55
wall_margin_m: 0.15
obstacle_radius_m: 1.2
obstacle_margin_m: 0.0
obstacle_timeout_sec: 2.5
offset_cost_weight: 1.0
smoothness_cost_weight: 0.35
obstacle_cost_weight: 1.0
reuse_previous_offset_cost_weight: 0.1
```

## Previous MPC Avoidance And Safety Settings

File: `aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/config/config.yaml`

```yaml
avoidance_planner:
  candidate_wall_margin_m: 0.1
  prediction_horizon_s: 3.0
  prediction_dt_s: 0.2
  ego_radius_m: 0.75
  obstacle_radius_m: 0.85
  collision_margin_m: 0.5
  min_hold_time_s: 1.5
  switch_cooldown_s: 1.0
  switch_margin_m: 0.5
  front_slow_distance_m: 3.0
  front_stop_distance_m: 1.0
  front_slow_speed_kmh: 10.0

longitudinal_safety:
  comfortable_decel_mps2: 1.6
  emergency_decel_mps2: 2.5
  max_brake_decel_mps2: 2.5
  latency_margin_s: 0.3
  distance_margin_m: 1.0
  hard_stop_distance_m: 1.0
  min_speed_mps: 0.2
```

## Previous External Longitudinal Safety Filter Settings

File: `aichallenge/workspace/src/aichallenge_submit/longitudinal_safety_filter/config/longitudinal_safety_filter.param.yaml`

```yaml
opponent_default_speed_mps: 5.0
prediction_horizon_s: 8.0
prediction_dt_s: 0.1
ego_radius_m: 0.75
obstacle_radius_m: 0.85
corridor_half_width_m: 1.2
avoid_with_slowdown_ttc_s: 4.0
brake_ttc_s: 2.0
comfortable_decel_mps2: 1.6
emergency_decel_mps2: 2.5
max_brake_decel_mps2: 2.5
latency_margin_s: 0.3
distance_margin_m: 3.0
hard_stop_distance_m: 1.0
min_race_speed_kmh: 5.0
fail_slow_speed_kmh: 5.0
```

## New Direction

- Shorten prediction and intervention ranges so the vehicle does not give up avoidance too early.
- Reduce obstacle and collision margins while keeping a small hard-stop guard.
- Allow quicker lane/offset switching and faster transitions.
- Keep emergency stop available, but make slowdown and braking less conservative.
