# collision_guard

Independent **longitudinal safety layer** that sits between the controller (MPC)
and the vehicle. It republishes the controller's `AckermannControlCommand`
unchanged unless another kart or a wall is close ahead in the ego travel
corridor, in which case it **caps the commanded speed** so the ego can always
stop before the obstacle, and — in the worst case — commands an **emergency
brake**. Steering (lateral) is always passed through untouched, so the guard
never fights the controller's racing line.

## Data flow

```
MPC  --/control/command/control_cmd_mpc-->  collision_guard  --/control/command/control_cmd-->  vehicle
                                              ^  /localization/kinematic_state (ego)
                                              ^  /v2x/vehicle_positions        (karts)
                                              ^  /scan                         (walls, optional)
```

Wiring is done in `aichallenge_submit_launch/launch/control/mpc.launch.xml`
(the MPC output is remapped to `.../control_cmd_mpc` and the guard is inserted).
Disable the whole layer with `use_collision_guard:=false`.

## Logic

For each incoming command, the guard finds the nearest obstacle ahead inside a
longitudinal corridor (forward distance > 0, |lateral| < `corridor_half_width`):

- **Karts** from `/v2x/vehicle_positions` (map frame), projected into the ego
  frame. Points within `self_ignore_radius` of the ego are treated as "self".
- **Walls** from `/scan` (narrow dead-ahead cone), used only as a last-resort AEB.

Safe speed = `sqrt(2 * brake_decel * max(clearance - standstill_gap, 0))`.
If the commanded speed exceeds it, the speed is capped and a braking
acceleration is commanded. If clearance < `emergency_gap`, full emergency stop.

## Wall guard is OFF by default

`use_scan_guard` / `use_scan_wall` default to **false**. The only available
`/scan` source in the MPC stack is `laserscan_generator`, which (as wired here)
emits spurious near-zero center readings that false-trigger the AEB — observed as
repeated `EMERGENCY BRAKE: obstacle 0.10 m ahead` during clean laps. The racing
line + MPC already keep the ego off the walls (0 wall collisions in testing), so
the wall branch stays disabled until a clean scan source is available. The V2X
kart guard is geometrically sound and stays enabled.

## Key parameters (`config/collision_guard.param.yaml`)

| param | meaning |
|-------|---------|
| `brake_decel` | decel used to compute the safe speed |
| `standstill_gap` | gap at which target speed reaches 0 |
| `emergency_gap` | clearance below which a full stop is commanded |
| `corridor_half_width` | lateral half-width of the "ahead" corridor |
| `time via standstill_gap` | (adaptive slowdown is distance-based) |
