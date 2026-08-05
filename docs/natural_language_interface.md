# Offline natural-language skill interface

`scripts/parse_skill_request.py` is a deterministic, offline adapter for short Chinese and English requests. It recognizes only these simulation-only capabilities:

- `patrol_home`
- `check_all_rooms`
- `inspect_area`
- `go_to_safe_waiting_area`
- `return_to_charger`
- `pause_current_task`
- `resume_current_task`
- `cancel_current_task`

`inspect_area` accepts only the fixed aliases `客厅`/`living room`, `厨房`/`kitchen`, `卧室`/`bedroom`, and `充电区`/`charging area`. The adapter emits a structured result with the original and normalized text, selected capability, canonical parameters, confidence, explanation, and explicit simulation boundary flags. An accepted result contains an existing `SkillRequest`; it contains no map coordinates or raw safe-goal data.

Ambiguous areas, multiple supported intents, and incomplete requests return `needs_clarification`. Requests outside the vocabulary, including physical-device or real-robot control, return `unsupported_intent`. Empty, overlong, and non-string input raises a local input error. The optional `--validate-plan` mode loads supplied local artifacts and calls the existing planner read-only; it does not execute a skill or start ROS/Nav2.

```bash
python3 scripts/parse_skill_request.py --text "检查厨房"
python3 scripts/parse_skill_request.py --text "resume task checkpoint-001"
```

This project is simulation-only and does not support real-robot deployment. The current adapter uses no LLM, API, clock, random value, or network service. A future LLM provider may only choose from the reviewed capability and parameter vocabulary before the same catalog and planner validation; it must never generate coordinates or directly control a robot.

## Unified simulation pipeline

`scripts/run_natural_language_skill.py` connects this adapter to the existing planner and optional simulation execution bridge. It writes a single atomic directory containing `natural_language_request.json`, `natural_language_parse.json`, `skill_plan.json`, `pipeline_result.json`, and `pipeline_report.md`. Default operation and `--dry-run` are ROS-free and send zero action goals. Only explicit `--execute-simulation` can connect to an already-running Gazebo Sim/Nav2 environment, after the same parse and planner preflight has accepted the request. The pipeline does not add any coordinate generation or device-control path: navigation can use only planner-approved accepted safe-goal references.
