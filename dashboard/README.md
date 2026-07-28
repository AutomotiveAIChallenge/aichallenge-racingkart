# Racing Kart — Experiment & Performance Dashboard

Traces every tuning experiment against lap time and 5-lap completion, so it's clear
**what actually moved the needle**. Data is auto-parsed from run logs.

> Earlier versions of this page asserted a 30 km/h / 1.0 m/s² competition rule and a
> 43 s "physics floor". Neither is documented anywhere in this repo and both are
> contradicted by measurement (the kart tops out at 37 km/h and laps at 39.5 s), so
> they were removed on 2026-07-27. **Confirm the real limits against the official
> rules before submitting.**

## Files
- `parse_runs.py` — scans `output/*/d*/autoware.log`, extracts each run's config,
  lap times and guard activity, and injects the result into `dashboard.html`.
- `dashboard.html` — self-contained page (open in a browser or publish as an Artifact).
  Reads its data from an embedded `<script id="run-data">` block that the parser rewrites.
- `run_data.json` — the parsed payload (also written out for reuse).

## Refresh after a run
```bash
python3 dashboard/parse_runs.py            # update dashboard.html in place
python3 dashboard/parse_runs.py --print    # ...and print the JSON
python3 dashboard/parse_runs.py --min-laps 2 --since 20260719   # focus recent, drop stubs
python3 dashboard/parse_runs.py --target-s 40   # per-lap time target (default 40 s)
```
Then reload `dashboard.html` (or re-publish the Artifact).

## What it extracts (from `autoware.log`)
| Field | Source line |
|-------|-------------|
| `v_max`, `a_max`, `a_min`, `ay_max`, `width`, `wp_id_offset` | MPC config echo (`ay_max: 19.0` …) |
| `Q[0]` | first element of the `Q: [...]` config line |
| `use_speed_profile` | `use_speed_profile: true` (shown as `fwd-bwd` vs `kappa-pred`) |
| `ref_vel` corners (s4/s6/s8) | `ref_vel:` lines (indices 4/6/8) |
| guard state | `collision_guard up (v2x=…, scan=…)` |
| guard events | `EMERGENCY BRAKE`, `slow: cap` counts |
| lap times | `Lap N completed! Lap time: X s` (+ ROS stamp) |
| **live param changes** | `<param> was updated to '<value>'` (+ ROS stamp) |
| **change vs previous** | auto-diff of config against the previous row |
| **result** | `ok` / `best` / `partial` / `fail` (stall heuristic) |

Commented-out preset blocks in `config.yaml` are echoed to the log too, but they
carry a leading `#`, so only the live values are picked up.

`best` = fastest flying lap of the session (lap 1 = standing start, excluded).
`fail` = completed < target *and* the sim kept running well past the last lap
(stall / off-line), e.g. the v_max 34 experiment.

### One log can become several rows
Limits are tunable at runtime (`ros2 param set /mpc_controller ay_max 13.0`), and a
long run where `ay_max` was walked 10 → 13 → 16 → 19 is **four experiments, not one**.
The parser splits each log at the moments the node logged an accepted change and
emits one row per config segment, suffixed `· a`, `· b`, … with `(live)` on the
change text. Laps are assigned by completion time, so the first lap of a segment
straddles the change — it is dropped from that segment's best, exactly like a
standing-start lap. Note the node applies a param **2-3 laps after** the
`param set` returns, so trust the log line, not the command.

Rows are ordered by the log's own first ROS timestamp, not by directory name, so a
hand-named `LOG_DIR` (`output/20260726-w170`) still lands in the right place.

## Collisions and manual labels — `run_meta.json`
Collisions are **not** in `autoware.log` (they live in the AWSIM container's
`Player.log`). Capture them and record per run with a `run_meta.json` next to the
log (`output/<ts>/d1/run_meta.json`):
```json
{ "collisions": 0, "change": "custom note", "label": "my name",
  "result": "partial", "note": "why", "exclude": false, "keep": true }
```
- `collisions` — shown in the table (else `—`).
- `change` / `label` — override the auto-derived text (`change` applies to the first segment).
- `result` — override the classifier, e.g. a run AWSIM ended on its own is not a
  control `fail`. `note` — free text carried into the JSON.
- `exclude` — drop this run. `keep` — force-keep a short run past `--min-laps`.

## Publishing as an Artifact (claude.ai)
`dashboard.html` is CSP-safe (no external assets). Publish it to get a private,
shareable URL; re-publish the same file to update it in place.
