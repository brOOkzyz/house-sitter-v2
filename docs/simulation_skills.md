# Simulation-only Smart-home Skills

## Scope and safety boundary

The base deterministic runtime and the bridge default/`--dry-run` mode are local review software: their requests, plans, results, and actions are `simulation_only: true`, `review_only: true`, and `executable: false`. The optional simulation bridge may mark an execution record executable only within Gazebo Sim; it never supports a real robot.

- Synthetic semantic labels are not ground truth.
- Accepted-goal coordinates come from existing demo artifacts; skill code does not hard-code or generate poses.
- The existing sequence preflight validates exact map identity, region/goal source and provenance, strict demo flags, and complete selector evidence.
- The layer does not rerun polygon validation, rasterization, distance transforms, or final map safety assertions.
- Validation establishes schema and internal consistency, not file-provenance authentication. Complete internally consistent imitations are outside the local review threat model.
- Manipulation, device control, alarm/sensor checks, escort/visitor interaction, battery, charging, timing, and recovery are simulated records only.
- The base runtime and default/`--dry-run` mode require no ROS or Nav2 and send zero action goals. Only optional `--execute-simulation` may use ROS 2/Nav2 `NavigateToPose` inside Gazebo Sim; `cmd_vel`, real hardware, dock/undock, manipulator, real IoT, and real sensor commands remain unsupported.

## Architecture

- `skill_catalog.py`: immutable declarations, parameters, action allowlists, and policy metadata.
- `skill_planner.py`: strict artifact preflight, alias resolution, shared route/item/routine/policy builders, and accepted-goal binding.
- `skill_runtime.py`: synchronous logical events and bounded failure/timeout/cancel/preemption/recovery behavior.
- `home_simulation_state.py`: deterministic battery, restrictions, blocked goals, items, alarms, queue, checkpoint, and device state.
- `skill_artifacts.py`: stable JSON/JSONL/Markdown rendering and sibling-temporary atomic publication.

The runtime uses step states `pending`, `running`, `succeeded`, `failed`, `timed_out`, `cancelled`, and `skipped`. Retry uses `attempt` and is capped at one retry. Pause and resume use checkpoint metadata. Events have continuous `logical_event_order` and no timestamp.

Central policy values are:

- low battery threshold: 20%
- critical battery threshold: 10%
- ordinary priority range: 0–99 (default 50)
- emergency priority: 100 (fixed for queued `emergency_response` and `emergency_task_preemption`)
- maximum retries: 1
- equal-priority queue ordering: FIFO by `insertion_order`

## Capability catalog

| # | capability | category | required parameters |
| ---: | --- | --- | --- |
| 1 | `patrol_home` | area access | — |
| 2 | `inspect_area` | area access | `area` |
| 3 | `check_all_rooms` | area access | — (individual synthetic checks plus aggregate summary) |
| 4 | `visit_nearest_area` | area access | — |
| 5 | `go_to_safe_waiting_area` | area access | — (`area=charging_area`) |
| 6 | `escort_to_area` | area access | `destination` |
| 7 | `visitor_greeting` | area access | — (`area=living_room`) |
| 8 | `deliver_item` | item service | `item`, `source`, `destination` |
| 9 | `fetch_item` | item service | `item`, `source` |
| 10 | `fetch_and_return` | item service | `item`, `source` |
| 11 | `handover_at_safe_point` | item service | `item`, `destination` |
| 12 | `collect_items` | item service | `items`, `source` |
| 13 | `move_item_to_storage` | item service | `item`, `source`, `destination` |
| 14 | `bedtime_routine` | smart-home routine | — |
| 15 | `leave_home_routine` | smart-home routine | — |
| 16 | `morning_routine` | smart-home routine | — |
| 17 | `security_check_routine` | smart-home routine | — |
| 18 | `emergency_response` | safety/emergency | injected `alarm_region` |
| 19 | `go_to_alarm_source` | safety/emergency | injected `alarm_region` |
| 20 | `find_nearest_safe_zone` | safety/emergency | — |
| 21 | `restricted_area_guard` | safety/emergency | `area` |
| 22 | `unsafe_goal_rejection` | safety/emergency | `goal` |
| 23 | `safe_wait` | safety/emergency | — (`area=charging_area`, `duration_seconds=0`) |
| 24 | `emergency_task_preemption` | safety/emergency | injected `alarm_region` |
| 25 | `retry_failed_step` | reliability/recovery | — (`area=living_room`) |
| 26 | `skip_failed_step` | reliability/recovery | — (`area=living_room`) |
| 27 | `fallback_to_safe_goal` | reliability/recovery | `area` |
| 28 | `resume_interrupted_task` | reliability/recovery | `checkpoint_id` |
| 29 | `abort_and_return` | reliability/recovery | — |
| 30 | `blocked_goal_replan` | reliability/recovery | `area` |
| 31 | `task_checkpoint` | reliability/recovery | — (`checkpoint_id=checkpoint-001`) |
| 32 | `return_to_charger` | battery/resource | — |
| 33 | `low_battery_abort` | battery/resource | — |
| 34 | `battery_aware_planning` | battery/resource | — |
| 35 | `charge_then_resume` | battery/resource | — |
| 36 | `energy_efficient_order` | battery/resource | — |
| 37 | `confirm_ambiguous_target` | human interaction | `target` |
| 38 | `semantic_alias_resolution` | human interaction | `target` |
| 39 | `explain_rejection` | human interaction | `reason_code` |
| 40 | `task_status_report` | human interaction | — |
| 41 | `cancel_current_task` | human interaction | — |
| 42 | `change_task_priority` | human interaction | `task_id`, `new_priority` |
| 43 | `pause_current_task` | task management | — |
| 44 | `resume_current_task` | task management | `checkpoint_id` |
| 45 | `queue_task` | task management | `queued_skill` |
| 46 | `list_queued_tasks` | task management | — |
| 47 | `preview_skill_plan` | task management | `target_skill` |
| 48 | `list_capabilities` | task management | — |
| 49 | `explain_skill_plan` | task management | `target_skill` |
| 50 | `export_task_trace` | task management | — |

Capabilities 1–24 and 32–42 are user-facing simulated workflows or policy queries. Capabilities 25–31 are bounded runtime/recovery policies. Capabilities 43–50 are task-management and inspection operations. `list_simulation_skills.py --json` and the `list_capabilities` skill return the same deterministic 50-entry machine-readable metadata: name, category, description, user-callable/classification fields, required/optional parameters, supported state, builder, flags, and policies. The human-readable list uses one compact row per capability while retaining those same review fields.

## Aliases and fixed routes

Deterministic aliases include `living room` / `living_room` / `客厅`, `kitchen` / `厨房`, `bedroom` / `卧室`, and `charging area` / `charging_area` / `充电区`. `room` and `房间` deliberately match both living room and bedroom and therefore return `confirmation_required`; no first-match guess is permitted.

Fixed routes are:

- `patrol_home`, `bedtime_routine`, `security_check_routine`: living room → kitchen → bedroom → charging area
- `leave_home_routine`: kitchen → living room → bedroom → charging area
- `morning_routine`: charging area → bedroom → kitchen → living room

Only `energy_efficient_order` permits deterministic nearest-neighbour reordering. The current accepted-artifact contract exposes one unique goal per label, so blocking that goal returns `NO_ALTERNATE_SAFE_GOAL`; it never substitutes another region or creates a coordinate. A successful same-region alternative would require a separately reviewed upstream artifact-contract change and is not claimed by this version.

`patrol_home` visits and reviews the fixed route. `check_all_rooms` uses the same fixed visit order but additionally emits `report_home_check_summary`, which names every checked region and remains synthetic (`real_sensor_detection: false`). If an earlier check fails, this summary is cancelled rather than claiming a complete check.

Queued ordinary tasks use priorities 0–99. Only queueing `emergency_response` or `emergency_task_preemption` assigns fixed priority 100 regardless of the caller's ordinary priority; every other capability, including safety-policy skills, remains ordinary. Queue entries have state-owned monotonic IDs (`task-000001`, `task-000002`, …), separate from caller request IDs and not caller-overridable. `change_task_priority` changes one queued normal task by that generated `task_id`; active tasks return `ACTIVE_TASK_PRIORITY_IMMUTABLE`, allowlisted emergency tasks return `EMERGENCY_PRIORITY_IMMUTABLE`, and ordinary requests cannot use 100. FIFO is retained for equal priorities. Checkpoint IDs are non-empty trimmed strings of at most 128 characters; CLI tuple-like and typed checkpoint literals are rejected.

`preview_skill_plan` and `explain_skill_plan` require `target_skill` plus the target skill's normal parameters. Recursive preview/explain targets are rejected. Both compile the same target plan using normal artifact and policy checks without runtime execution or state mutation. Preview exposes pending target steps with no events; explain adds per-step action, target region/goal, reason, parameter sources, safety/policy, and critical/skippable/retryable fields.

## CLI examples

```bash
# List all 50 capabilities.
python3 scripts/list_simulation_skills.py --json

# Preview only: steps remain pending and the JSONL trace is empty.
python3 scripts/preview_simulation_skill.py \
  --skill patrol_home --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --output-dir local_annotations/patrol_preview

# Preview or explain a real parameterized target plan without executing it.
python3 scripts/preview_simulation_skill.py \
  --skill preview_skill_plan --param target_skill=deliver_item \
  --param item=medicine --param source=kitchen --param destination=bedroom \
  --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --output-dir local_annotations/deliver_preview

python3 scripts/run_simulation_skill.py \
  --skill explain_skill_plan --param target_skill=deliver_item \
  --param item=medicine --param source=kitchen --param destination=bedroom \
  --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --output-dir local_annotations/deliver_explanation

# Normal item-service simulation.
python3 scripts/run_simulation_skill.py \
  --skill deliver_item --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --param item=medicine --param source=kitchen --param destination=bedroom \
  --output-dir local_annotations/deliver_demo

# Failure with bounded retry.
python3 scripts/run_simulation_skill.py \
  --skill patrol_home --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --inject-event fail_step_order=3 --inject-event recovery_action=retry \
  --output-dir local_annotations/retry_demo

# Logical timeout (no sleep).
python3 scripts/run_simulation_skill.py \
  --skill patrol_home --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --inject-event timeout_step_order=3 --inject-event timeout_seconds=5 \
  --inject-event simulated_duration_seconds=8 \
  --output-dir local_annotations/timeout_demo

# User cancellation.
python3 scripts/run_simulation_skill.py \
  --skill patrol_home --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --inject-event cancel_before_step=3 \
  --output-dir local_annotations/cancel_demo

# Emergency preemption from an injected simulated alarm only.
python3 scripts/run_simulation_skill.py \
  --skill patrol_home --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --inject-event preempt_at_step=3 --inject-event alarm_region=kitchen \
  --inject-event alarm_type=simulated_smoke \
  --output-dir local_annotations/preemption_demo

# Simulated low-battery abort plus accepted charging-area recovery.
python3 scripts/run_simulation_skill.py \
  --skill patrol_home --semantic-regions REGIONS.json --safe-goals GOALS.json \
  --battery-percent 10 --output-dir local_annotations/low_battery_demo
```

Expected normal results are logical `succeeded` steps. Injected failure/timeout/cancel produce `failed`/`timed_out`/`cancelled` plus deterministic downstream cancellation. A current timeout is `timeout_exceeded`; every suffix step cancelled because of it is `upstream_timeout`. Emergency preemption cancels the ordinary suffix and records a separate emergency result. Low battery cancels the ordinary plan and records a recovery step to the artifact-provided charging-area goal. These statuses are not evidence of physical execution or real fault detection. `--restricted-region` and `--blocked-goal` may each appear only once; `--param` and `--inject-event` may repeat only for distinct keys. Every JSONL logical event is `synthetic: true`, `review_only: true`, `simulation_only: true`, and `executable: false`.

## Artifacts

Every preview or run publishes exactly five deterministic files into a previously absent output directory:

- `skill_request.json`: normalized request, simulated state inputs, and injections.
- `skill_plan.json`: pending compiled actions, accepted goal references, policy, and safety basis.
- `skill_result.json`: terminal logical states, counts, recovery/preemption/checkpoint metadata, and final local state.
- `skill_events.jsonl`: one stable logical event per line, without timestamps; each line carries explicit synthetic/review-only/simulation-only/non-executable flags.
- `skill_report.md`: human-readable plan rationale and explicit safety warnings.

All five are first completed in one sibling `TemporaryDirectory` and published by one `os.replace`. Existing targets are never overwritten. Temporary cleanup uses only the standard-library cleanup method as a best effort: if cleanup fails after a write or publication failure, the original failure is retained with a diagnostic note. It intentionally makes no hostile-concurrency no-residue guarantee.

## Future adapter boundary

## Optional Gazebo Sim/Nav2 execution bridge

`scripts/run_skill_in_gazebo.py` is an opt-in, simulation-only bridge. It consumes the existing compiler output rather than revalidating or recreating selector, map-identity, provenance, polygon, or raster checks. Navigation steps can be sent only through a Nav2 `NavigateToPose` action in the `map` frame with `use_sim_time`; no `cmd_vel` publisher exists. The default and `--dry-run` modes are ROS-free and send no goals. `--execute-simulation` is required to connect to an existing Gazebo Sim/Nav2 system. Non-navigation steps remain deterministic local simulation records.

It atomically publishes `execution_request.json`, `execution_plan.json`, `execution_events.jsonl`, `execution_result.json`, and `execution_report.md`. These retain `simulation_only: true`, `review_only: true`, and `real_robot_supported: false`; `executable: true` means only that an explicit Gazebo/Nav2 simulation action may be attempted.

**This project is simulation-only and does not support real-robot deployment.**

A future ROS 2 skill adapter could translate an independently reviewed subset of plans into real robot APIs only after a new authorization, current-map/costmap/footprint validation, lifecycle and cancellation design, and hardware safety review. No such adapter, message, action client, ROS package, or executable command is implemented in this phase.
