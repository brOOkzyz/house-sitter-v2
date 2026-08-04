House Sitter v2
================

`house_sitter_v2` is a simulation-only LLM-assisted house-sitter robot prototype. It lives alongside the older Create3 demo in the same workspace and does not modify the legacy files.

## Project Positioning

This project uses a constrained planning pipeline:

```text
natural-language prompt
-> JSON-only LLM planner
-> JSON task plan
-> verifier
-> executor
-> task report
-> simulation-only Nav2 safety layer
```

Physical robot deployment is outside the project scope.

## Multi-step Semantic Navigation

The existing schema version `1.0` now supports ordered plans of one to five `navigate_to_waypoint` steps. Gemini generates structured semantic intent only: it cannot provide physical coordinates, ROS commands, direct motion controls, or extra actions and parameters. Every destination is resolved through the local user-configured registry in `config/semantic_waypoints.json`; aliases are accepted only when explicitly configured there and are normalized to canonical labels before request construction.

`PlanVerifier` remains mandatory for every step. The complete plan is schema-checked, allowlist-checked, grounded, normalized, and verified before any simulation execution request is created. If one step is empty, unknown, unsafe, or otherwise invalid, the entire plan is rejected and no partial execution requests are retained. Execution requests contain canonical labels only. The project remains simulation-only.

## User-annotated Semantic Areas

Semantic labels and aliases are local registry data, not map knowledge supplied by Gemini or another LLM. The registry can represent either an `unmapped` `simulation_safe_nearby_goal` placeholder or a `mapped` `user_labelled_map_area`. A mapped entry requires a user-provided polygon in a named map frame and a `source` with `type: user_annotation` and a map identifier. The production registry currently contains only explicit unmapped placeholders: it has no room polygons or claimed kitchen, hallway, or bedroom coordinates.

Inspect this metadata without executing any robot action:

```bash
python3 scripts/inspect_semantic_areas.py
```

### Offline annotation prototype

Use the local map annotation GUI to create a user-owned draft, never to modify production configuration:

```bash
python3 scripts/annotate_semantic_areas.py --map maps/minimal_slam_map.yaml
```

Choose an existing canonical label, enter a manual `map_id`, then click polygon boundary vertices. Left-click adds a vertex; Backspace undoes one; Escape clears the unsaved polygon; Enter validates it. `Export Draft` writes a complete, registry-compatible draft to `local_annotations/semantic_areas_draft.json`, which is Git-ignored. The GUI does not start ROS, Gazebo, Nav2, or robot execution, and it cannot overwrite the production registry.

For a read-only map check without opening a GUI:

```bash
python3 scripts/annotate_semantic_areas.py --map maps/minimal_slam_map.yaml --inspect-map
```

Manual annotation still uses a manually supplied `map_id`; it does not yet carry the automatic workflow's strict map-identity binding. Its polygons remain local validated annotations and do not produce Nav2 poses.

### Automatic area proposals

The offline proposal mode segments only known free cells from a ROS occupancy map. It uses the YAML `negate`, free, and occupied thresholds; unknown cells are never treated as free. Narrow connections are temporarily eroded using `--doorway-width-m`, retained components are regrown into candidate regions, and their contours are simplified with `--simplify-tolerance-m`.

```bash
python3 scripts/auto_propose_semantic_areas.py \
  --map maps/minimal_slam_map.yaml \
  --registry config/semantic_waypoints.json \
  --map-id minimal_slam_map_v1 \
  --preview-output local_annotations/auto_area_proposals.png \
  --proposal-output local_annotations/auto_area_proposals.json
```

`--dry-run` prints candidates without writing files. The PNG and JSON are local, Git-ignored review artifacts. Automatic proposals carry strict map identity (canonical metadata, image SHA-256, and fingerprint). Automatic geometry does not establish room semantics: candidates without repository-backed location evidence are `unassigned`; an elongated region can only be a low-confidence `hallway` suggestion. Every automatic result requires human review, is not written into production configuration, and does not start ROS, Gazebo, Nav2, or navigation.

`legacy` is the default proposal mode and keeps the original connected-component contour workflow. The optional `--proposal-mode hole-aware-cells` performs a conservative partition of clearance-safe observed free space so that candidates containing obstacle holes are not accepted as one polygon. It is explicitly review-only: every output remains `canonical_label: null`, `suggested_label: unassigned`, and `status: proposed`; it is not a room annotation and cannot enter the production registry.

For hole-aware review, `safe_candidates.json` records every candidate that passed both polygon validation and raster safety. `auto_area_proposals.json` contains only the current human-review batch. `--maximum-proposal-count` controls that batch, not safety generation; `0` selects every safe candidate and may make previews dense. `--selection-strategy largest-first` is the compatible default and prioritizes larger safe candidates, so it will commonly provide greater area coverage at a fixed review-batch size. Explicit `spatial-balanced` instead prioritizes deterministic geographic representation; it does not maximize area coverage and does not guarantee greater coverage than `largest-first`. Both strategies preserve candidate geometry, polygon validation, and raster safety, and affect only the human-review batch and its order. The selected preview and `all_safe_candidates.png` are local, Git-ignored aids only; semantic names still require independent human evidence.

The TurtleBot 4 warehouse map is a technical validation map for occupancy geometry, polygon validation, deterministic partitioning, safety checks, and review-batch selection only. It is not a residential semantic map: its observed free-space zones are never evidence for kitchen, bedroom, `living_room`, or other household labels. They remain review-only, unassigned, and outside the production semantic registry.

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

Each accepted point passes the current geometry validator, bounds validation, full strict-interior raster evaluation, and final goal assertion. Raster counts and ratios are `null` when raster evaluation did not run. Accepted and rejected records carry locally computed `faster_safety_passed`; rejected records are always false. `goal_order` is final contiguous 1-based order, `source_candidate_order` is the original JSON-array position, and `source_selection_rank` preserves the source value or null. Duplicate polygons are rejected; distinct polygons selecting the same pixel retain the first stable `(proposal_id, partition_id)` candidate.

Results are review-only observed free-space goals, not rooms, not semantic annotations, and not executable navigation commands. The tool never modifies maps or the production registry, starts ROS/Gazebo/Nav2, or sends movement commands. The warehouse remains a technical validation map, never a residential semantic map.

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
- The simulation route does not require Gazebo GUI.

## Run Commands

Mock dry-run:

```bash
python3 scripts/run_llm_demo.py "Go to the hallway, then visit the kitchen, and finally return to the charging station"
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

By default, `run_llm_sim_nav2_demo.py` prints a verified simulation execution request. The existing simulation-only execution path is used only after explicit `--execute-sim`.

## Latest Verified State

The current verified simulation-only route includes:

```text
Gemini JSON plan generation: PASS
verifier: PASS
dry-run executor: PASS
headless Gazebo readiness: PASS
saved-map localization + Nav2 readiness: PASS
simulation-only undock: PASS
simulation-only micro Nav2 navigation: PASS
LLM-to-simulation Nav2 demo: PASS
pytest: run `python3 -m pytest -q` for the current result
```

Final demo evidence image:

- `docs/assets/final_demo_evidence.png`
- `docs/final_demo_evidence_summary.txt`

## Repository Layout

```text
house_sitter_v2/
├── README.md
├── AGENTS.md
├── config/
├── docs/
├── house_sitter_core/
├── maps/
├── scripts/
├── tests/
└── logs/
```
