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
