# MPC prediction jaggedness analysis

## Current observation

The latest OSQP cache/update change reduced MPC CPU load, but the MPC prediction visualization looks jagged. The issue should be treated as a consistency problem, not only as a rendering problem, because the current code has multiple places where the optimized sequence, published command, and displayed prediction can diverge.

Runtime measurements after `make dev`:

- `/mpc/prediction`: about 4.1 Hz.
- `/control/command/control_cmd`: about 29 Hz wall-time reception, about 19.6 Hz by message stamp in the sampled window.
- `/control/command/control_cmd_raw`: same rate as `control_cmd`.
- `/localization/kinematic_state`: about 42 Hz wall-time reception.
- Prediction marker point count: 10 points.
- Mean prediction segment length: about 1.11 m.
- Max prediction segment length: about 1.86 m.
- Mean internal heading jump between adjacent predicted segments: about 0.116 rad.
- Max internal heading jump between adjacent predicted segments: about 0.418 rad.
- Same prediction-index frame-to-frame jump: mean about 1.24 m, max about 5.69 m.

The marker is therefore sparse both in time and in space: it is published at only about 4 Hz, and it contains only 10 sphere markers. That alone can make RViz look discontinuous. However, the code-level contradictions below are more important because they can create real prediction discontinuity.

## Code-level contradictions

### 1. The MPC input variable is curvature, but several constraints/comments treat it as steering angle

The MPC input vector is effectively `[v, kappa]`, because the bounds are built as:

```python
[-tan(delta_max) / wheel_base, tan(delta_max) / wheel_base]
```

and the solution is later converted by:

```python
delta = arctan(kappa * wheel_base)
```

But the steering-rate constraint matrix is applied directly to the second optimization variable before this conversion. This means it constrains curvature difference, not steering angle difference. The comment says steering angle, but the matrix is operating on curvature.

This is not just a naming issue. A steering-rate limit should satisfy:

```text
delta = atan(kappa * L)
d_delta/d_kappa = L / (1 + (kappa L)^2)
```

so the correct curvature-rate bound is state-dependent:

```text
|kappa_next - kappa_current| <= delta_rate * dt / (d_delta/d_kappa)
```

The current bound uses a constant value based on steering-rate units. It is only approximately valid around small steering angles.

### 2. The first optimized input is not constrained against the previously applied input

The horizon constraint currently limits differences between consecutive horizon inputs:

```text
u_1 - u_0
u_2 - u_1
...
```

It does not constrain:

```text
u_0 - previously_applied_u
```

After the solve, the first steering output is clipped against `previous_steering`. Therefore:

- OSQP solves a problem whose first input may jump.
- The displayed prediction is generated from that un-clipped OSQP solution.
- The actual command is clipped after the solve.

This makes the prediction and the command internally inconsistent. A jagged `/mpc/prediction` can therefore be the optimizer's un-applied solution, not the trajectory that the vehicle will actually execute.

### 3. Prediction is generated before post-MPC steering filtering is reflected back into the state sequence

The code sequence is:

1. Solve OSQP.
2. Convert full control sequence from curvature to steering.
3. Clip only the first steering command by previous steering.
4. Store `current_control = control_signals`.
5. Convert `dec.x` into `current_prediction`.

The prediction is based on `dec.x`, which corresponds to the raw optimizer result. The post-solve clipped command is not rolled forward through the model and is not used to recompute the prediction. This is a direct explanation for "prediction is jagged while command is lighter/smoother".

### 4. Steering cost is zero, so the optimizer is under-regularized in steering

The current configuration is:

```yaml
R: [100000.0, 0.0]
```

This heavily penalizes velocity command changes, but does not penalize steering/curvature magnitude. Steering is then shaped mostly by lateral/yaw tracking and constraints. With OSQP warm-start/update, the solver workspace no longer resets every cycle, which is good for CPU, but any under-regularized degree of freedom becomes more visible because equivalent or near-equivalent solutions can move around between cycles.

This does not mean OSQP cache/update is conceptually wrong. It means the QP must be made well-posed enough for cached updates.

### 5. The infeasibility/retry heuristic treats zero curvature as suspicious

The current check is:

```python
use_control_signals = control_signals[1::2]
if not np.all(use_control_signals):
    ...
```

For curvature, zero is a valid solution on straight or near-straight sections. This check can trigger unnecessary re-solves or safety-margin relaxation based on a physically valid zero-curvature output. Even if it does not trigger constantly, the condition is logically wrong.

### 6. Prediction visualization is sparse by construction

`update_prediction()` publishes points for:

```python
range(2, N)
```

With `N = 12`, that produces only 10 points. The publisher then emits sphere markers only every:

```python
loop % (control_rate // 4) == 0
```

At `control_rate = 30`, this is roughly 4.3 Hz. The visualization is not a dense line and is not synchronized with every control cycle.

This is a visualization limitation, but it does not explain all of the observed frame-to-frame prediction jumps.

## Most likely root causes

1. **Prediction-command mismatch**: the first steering command is clipped after optimization, but the prediction remains based on the raw un-clipped OSQP solution.
2. **Missing first-input rate constraint**: the QP does not constrain `u0` against the previously applied steering/curvature.
3. **Steering under-regularization**: `R[1] = 0.0` makes the curvature sequence less stable than it should be for a cached/warm-started QP.
4. **Unit mismatch in steering-rate constraint**: the QP variable is curvature, while the limit is specified in steering-angle units.
5. **Sparse marker output**: `/mpc/prediction` is only a low-rate 10-point sphere visualization.

## Recommended correction order

1. Add a first-input rate constraint inside the QP:
   - constrain `kappa_0` against the previous applied steering converted to curvature.
   - stop relying on post-solve clipping as the primary rate limiter.

2. Keep the post-solve limiter only as a final hardware safety clamp:
   - it should rarely activate.
   - if it activates often, the QP constraints are wrong.

3. Add a small non-zero steering/curvature cost:
   - for example start with a very small `R[1]`, not a large value.
   - goal is not to slow response, but to remove non-unique steering solutions.

4. Fix units:
   - either use curvature-rate constraints consistently, derived from steering-rate limits.
   - or change the optimization variable to steering angle, which is a larger refactor.

5. Fix the invalid zero-curvature retry check:
   - use OSQP status and `dec.x is None` instead of `np.all(control_signals[1::2])`.

6. Improve prediction visualization separately:
   - publish every control cycle or at least 10 Hz.
   - use `LINE_STRIP` or interpolate between predicted points.
   - include points from `n = 0` or `n = 1` if useful for debugging.

## Important conclusion

The current jaggedness should not be blamed directly on the OSQP cache/update mechanism. The cache/update change exposed existing inconsistencies that were partly hidden by repeated solver setup. The MPC problem needs to be made internally consistent so the cached solver receives a well-posed QP each cycle.

The highest-value fix is to move the steering-rate constraint into the QP correctly, including the first input relative to the previously applied command. That aligns the optimized prediction with the command actually sent to the vehicle.

## Follow-up verification: restore original horizon

To separate "OSQP cache/update" from "MPC size reduction", `N` and `control_rate` were restored to the previous stable values while keeping the OSQP cache/update implementation:

```yaml
N: 20
control_rate: 40.0
```

Observed result:

- Runtime config confirmed: `N = 20`, `control_rate = 40.0`.
- `/mpc/prediction` point count returned from 10 to 18.
- Prediction segment mean/max: about 1.05 m / 1.74 m.
- Internal prediction heading jump mean/max: about 0.105 rad / 0.314 rad.
- Same prediction-index frame-to-frame jump mean/max: about 1.58 m / 4.23 m.
- MPC CPU returned to about 53%.
- MPC node stayed alive and published commands.

Interpretation:

- The shortened prediction was caused by reducing `N` from 20 to 12. This was not an OSQP cache/update effect.
- The previous "light" configuration changed the MPC problem, so it was not a pure implementation-only optimization.
- Keeping the original horizon restores the old prediction length but also restores most of the CPU load.
- A trial with OSQP `warm_starting=False` was rejected because the MPC process exited during startup in this environment. It was not kept.

Practical conclusion:

- For correctness, keep `N = 20` and `control_rate = 40.0` until the QP formulation issues are fixed.
- Further CPU reduction should not come from shortening the horizon first. It should come from reducing Python-side matrix construction cost, fixing the QP formulation, and then carefully re-testing smaller horizons.
