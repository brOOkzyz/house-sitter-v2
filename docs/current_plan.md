Current Development Plan
========================

## Project Direction

`house_sitter_v2` is a simulation-only LLM-assisted house-sitter robot prototype.

Current pipeline:

```text
user prompt
-> JSON-only LLM planner
-> verified JSON task plan
-> dry-run or simulation-only executor
-> task report
```

Physical robot deployment is outside the project scope.

## Multi-step Semantic Navigation

The plan schema remains at version `1.0` and accepts one to five ordered `navigate_to_waypoint` steps. Gemini produces structured semantic intent only and never supplies physical coordinates, ROS commands, or direct control values. Each semantic destination or alias is resolved using the local user-configured registry in `config/semantic_waypoints.json`, and simulation requests use canonical labels only.

Before request construction, `PlanVerifier` performs schema and allowlist checks, local alias normalization, canonical grounding, and mandatory verification for every step. Verification is atomic at plan level: one invalid step rejects the entire plan, creates zero execution requests, and prevents partial execution. The project remains simulation-only.

## User-annotated Semantic Area Data

`config/semantic_waypoints.json` now distinguishes explicit `unmapped` simulation-only placeholders from `mapped` `user_labelled_map_area` entries. A mapped area must contain a user annotation polygon in a named map frame and identify its annotated map through `source.type: user_annotation` and `source.map_id`. LLM output remains limited to semantic labels and aliases; it cannot provide geometry, coordinates, frames, or navigation goals.

The current production registry deliberately has no room polygons because no user-confirmed room annotations have been supplied. `tests/fixtures/semantic_areas_test.json` contains fictional coordinates solely for unit tests and is never loaded by default. Offline safe-goal selection is a separate review-only workflow: it selects no executable Nav2 target and never changes the production registry.

### Offline annotation prototype

`scripts/annotate_semantic_areas.py --map maps/minimal_slam_map.yaml` is a local Tkinter workflow for choosing an existing canonical label and clicking polygon boundary vertices on a read-only ROS map image. Backspace removes the most recent vertex, Escape clears the unsaved polygon, Enter validates it, and Export Draft writes only `local_annotations/semantic_areas_draft.json`. `local_annotations/` is Git-ignored and the tool refuses to overwrite the production registry.

The `--inspect-map` mode prints YAML/image paths, PGM dimensions, resolution, origin, bounds, negate, and occupancy thresholds without starting the GUI or writing a file. Manual annotation still requires a user-supplied `map_id` and does not yet have the automatic workflow's strict map-identity binding. Annotation polygons remain local validated data and are not used for Nav2 poses, ROS, Gazebo, or navigation execution.

### Automatic candidate proposals

`scripts/auto_propose_semantic_areas.py` provides a separate, non-GUI offline review workflow. It classifies occupied/free/unknown cells using the ROS YAML thresholds and `negate`, never promotes unknown cells to free space, erodes connections narrower than `--doorway-width-m`, regrows components, extracts/simplifies contours, and validates each resulting polygon through the semantic-area validator. `--minimum-area-m2` filters noise and `--simplify-tolerance-m` controls Ramer–Douglas–Peucker contour simplification.

The command writes `local_annotations/auto_area_proposals.png` and `local_annotations/auto_area_proposals.json`, or prints no files with `--dry-run`. Proposal names are intentionally conservative: occupancy geometry alone cannot reliably identify kitchen, bedroom, or living room. Without repository-backed location evidence candidates are `unassigned`; only an elongated region may receive a low-confidence hallway suggestion, never a confirmed label. Outputs require human review, are Git-ignored, never modify production configuration, and do not generate a Nav2 goal or execute navigation.

`legacy` remains the default proposal mode. `--proposal-mode hole-aware-cells` is an explicit opt-in for review-only observed free-space cells: it partitions clearance-safe free space around obstacle holes, then requires the existing simple-polygon validator and final raster safety check. These cells are not rooms, remain `canonical_label: null` / `suggested_label: unassigned` / `status: proposed`, and cannot create a production registry draft.

Hole-aware output separates all safe candidates from the selected review batch. `safe_candidates.json` preserves every validator- and raster-safety-passed cell, while `auto_area_proposals.json` is only the batch selected for human inspection. `--maximum-proposal-count 0` selects all safe candidates; otherwise it limits review density only. `largest-first` is the compatible default and prioritizes larger safe candidates, so it will commonly achieve greater area coverage within a fixed batch. `spatial-balanced` is a deterministic opt-in for geographical representation: it does not maximize area coverage and does not guarantee greater coverage than `largest-first`. Neither strategy changes candidate geometry, polygon validation, or raster safety; they affect only the human-review batch and its order. Both preview files are Git-ignored local artifacts and do not establish semantic labels.

The TurtleBot 4 warehouse map is retained only for technical validation of occupancy geometry and the offline review pipeline. It must not be interpreted as a residential semantic map or used as evidence for kitchen, bedroom, `living_room`, or other household labels. Its generated zones remain unassigned review artifacts, not rooms, and are never written to the production semantic registry.

### Offline safe-goal review

`scripts/select_safe_goals.py` is an offline preprocessing tool for human review, not an online navigation system. It reads an existing occupancy map and proposal report, revalidates every polygon against the current map, and deterministically selects review-only observed-free-space goals. Input safety booleans are provenance claims only and never authorize a goal. The report must carry matching strict `map_identity` metadata (canonical map metadata, image SHA-256, and fingerprint); missing or mismatched identity fails closed.

```bash
python3 scripts/select_safe_goals.py \
  --map maps/minimal_slam_map.yaml \
  --candidates local_annotations/auto_area_proposals.json \
  --candidate-source selected \
  --minimum-clearance-m 0.30 \
  --output-dir local_annotations/safe_goal_selection_run_001
```

The output directory must not already exist. Overwrite, force, backup, and rollback are deliberately unsupported: all three artifacts are created in a sibling temporary directory, then published by one rename; failures clean only the unpublished temporary directory. The tool writes exactly `safe_goal_candidates.json`, `rejected_safe_goals.json`, and `safe_goal_preview.png`.

Each accepted point passes the current geometry validator, bounds validation, full strict-interior raster evaluation, and final goal assertion. Raster counts and ratios are `null` when raster evaluation did not run. Accepted and rejected records carry locally computed `faster_safety_passed`; rejected records are always false. For synthetic demo records, `source_candidate_order` is the original candidate JSON 1-based position, `source_selection_rank` is the upstream `selection_rank` or `null`, `demo_assignment_order` is the fixed label assignment order 1-4, and `goal_order` is the final accepted-goal order 1-4. Duplicate polygons are rejected; distinct polygons selecting the same pixel retain the first stable `(proposal_id, partition_id)` candidate.

Results are review-only observed free-space goals, not rooms, not semantic annotations, and not executable navigation commands. The tool never modifies maps or the production registry, starts ROS/Gazebo/Nav2, or sends movement commands. The warehouse remains a technical validation map, never a residential semantic map.

### Synthetic dissertation-demo labels

`create_demo_semantic_map.py` adds a local, automatic demonstration-only bridge from existing map-bound safe candidates to four fixed arbitrary labels: `living_room`, `kitchen`, `bedroom`, and `charging_area`. It needs no user-drawn polygons and uses the existing spatial-balanced (when present) or largest-first candidate selection plus the existing current-map safe-goal checks.

The result is expressly synthetic and not ground truth: every record is `demo_only`, `synthetic_semantics`, `ground_truth: false`, review-only, and non-executable, with `automatic_synthetic_demo_assignment` provenance. It does not perform real room recognition or semantic mapping and never writes the production registry. Reliable deployed semantics must come from a user, dataset, or perception component. This phase still does not start ROS, Gazebo, or Nav2; it only demonstrates label -> safe goal -> simulation-task data flow through local Git-ignored artifacts.

## Completed Modules

- JSON-only LLM planning with atomic one-to-five-step semantic navigation
- Gemini Python SDK structured output provider with mock fallback
- Plan verifier
- Dry-run executor
- Task report
- SLAM map saved:
  - `maps/minimal_slam_map.yaml`
  - `maps/minimal_slam_map.pgm`
- Headless Gazebo readiness
- saved-map AMCL localization
- Nav2 readiness
- simulation-only undock
- simulation-only micro Nav2 navigation
- LLM-to-simulation Nav2 demo

## Safety Design

- LLM output is never executed directly.
- Gemini SDK structured output is not trusted directly.
- Every step must pass the mandatory verifier before any request is built.
- A single failed step rejects the whole plan; partial execution is not allowed.
- LLM cannot choose arbitrary coordinates.
- Room and area labels must already exist in `config/semantic_waypoints.json`.
- The simulation goal is generated by the safety layer from `/amcl_pose` and `/map`.
- `compute_path_to_pose` must pass before `navigate_to_pose`.
- No direct `/cmd_vel` is used.

## Verified Results

Latest confirmed simulation-only results:

```text
Gemini dry-run JSON planning: PASS
JSON verifier: PASS
dry-run execution: PASS
headless Gazebo readiness: PASS
saved-map AMCL localization: PASS
Nav2 readiness: PASS
simulation-only undock: PASS
simulation-only micro Nav2 navigation: PASS
LLM-to-simulation Nav2 demo: PASS
pytest: run `python3 -m pytest -q` for the current result
```

## Run Commands

Mock dry-run:

```bash
python3 scripts/run_llm_demo.py "Go through the corridor, then visit the lounge, and finally return to the charging station"
```

Gemini dry-run:

```bash
export LLM_PROVIDER=gemini
export GEMINI_MODEL=gemini-2.5-flash
read -s -p "Gemini API key: " GEMINI_API_KEY
echo
export GEMINI_API_KEY
python3 scripts/run_llm_demo.py "visit the hallway"
```

Simulation full-stack demo:

```bash
./scripts/run_sim_full_stack_demo.sh
```

LLM-to-simulation Nav2 demo:

```bash
python3 scripts/run_llm_sim_nav2_demo.py "visit the hallway"
```

## Current Scope Boundary

Allowed focus:

- simulation-only maintenance
- documentation updates
- verifier and safety checks
- read-only diagnostics

Out of scope:

- physical robot deployment
- direct velocity control
- arbitrary coordinate execution from LLM output
- new navigation-goal experiments without explicit confirmation

## Next Recommended Step

Keep the simulation-only route stable and extend request execution only after an explicit review of the atomic multi-step safety boundary.
