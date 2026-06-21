# MPC-Native Longitudinal Safety Implementation

## Purpose

The longitudinal safety layer must evaluate the path that the vehicle will actually follow. The MPC controller does not use `/planning/scenario_planning/trajectory` as its execution path; it computes its own prediction internally. For that reason, ego risk evaluation now uses the MPC prediction as the source of truth.

## Data Flow

- Ego future path: `/mpc/predicted_path`, published by `multi_purpose_mpc_ros` as `nav_msgs/Path`.
- Opponent current state: `/v2x/vehicle_positions`.
- Opponent future path: projected from the V2X current position along `/planning/scenario_planning/trajectory`.
- Safety output: `/control/command/control_cmd`, filtered from `/control/command/control_cmd_raw`.
- Debug output: `/safety/longitudinal_debug/markers` and `/safety/longitudinal_state`.

## Safety Rule

The safety filter compares the MPC ego prediction and each opponent trajectory prediction at matching time steps. If the MPC prediction avoids the opponent, the filter stays in `CLEAR` or `AVOID` and does not clamp speed. If the MPC prediction intersects an opponent trajectory, the filter applies the existing staged response:

- `AVOID_WITH_SLOWDOWN` for a predicted collision that is far enough to slow down.
- `BRAKE_FOR_COMMIT` for a near predicted collision that requires braking.
- `EMERGENCY_STOP` only for the current forward hard-stop distance check.

This keeps lane selection inside the MPC lane planner. The safety filter does not choose lanes; it only judges whether the MPC-selected prediction is safe enough longitudinally.

## Debugging

RViz markers now show both sides of the decision:

- The MPC ego prediction is drawn in the current safety-state color.
- Opponent trajectory predictions are drawn separately.
- Warning, brake, and predicted collision points are shown on the MPC prediction.
- The text marker reports the ego source, opponent source, state, reason, TTC, collision distance, and speed/acceleration limits.

## Validation

The implementation was checked with:

- `python3 -m py_compile`
- `colcon test --packages-select longitudinal_safety_filter`
- `make autoware-build`
