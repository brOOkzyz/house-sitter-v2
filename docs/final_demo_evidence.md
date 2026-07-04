Final Demo Evidence
===================

## Scope

This project is simulation-only. Physical robot deployment is outside the project scope.

## Latest Successful Gemini Dry-Run Result

- Command: `python3 scripts/run_llm_demo.py "visit the hallway"`
- Provider: `gemini`
- Model: `gemini-2.5-flash`
- Result: JSON plan generated, verifier passed, dry-run completed
- Plan source: `gemini_planner`

## Latest Successful Simulation Full-Stack Demo Result

- Summary file: `logs/latest_sim_full_stack_demo_summary.txt`
- Result: `PASS`
- Reason: `simulation-only full-stack demo completed`
- Constraint summary: Gazebo GUI forbidden, direct `/cmd_vel` forbidden, movement through Nav2 actions only

## Latest Successful LLM-to-Simulation Nav2 Demo Result

- Command: `python3 scripts/run_llm_sim_nav2_demo.py "visit the hallway" --execute-sim`
- Plan source: `gemini_planner`
- Result: verifier passed, simulation execution request created, `compute_path_to_pose` PASS, `navigate_to_pose` SUCCEEDED
- Direct `/cmd_vel`: not used

## Latest Pytest Result

- Command: `python3 -m pytest`
- Result: `30 passed`

## Relevant Log Files

- `logs/latest_sim_full_stack_demo_summary.txt`
- `logs/latest_sim_nav2_micro_smoke_summary.txt`
- `logs/sim_nav2_micro_smoke_20260704_160048.json`
- `logs/sim_nav2_micro_smoke_20260704_164522.json`
- `logs/latest_llm_sim_nav2_visual.png`
- `logs/latest_rgb_camera_snapshot.png`
- `docs/assets/final_demo_evidence.png`
- `docs/final_demo_evidence_summary.txt`

## Safety Notes

- LLM output is never executed directly.
- Verifier approval is required before execution.
- The safety layer chooses the simulation goal from `/amcl_pose` and `/map`.
- No direct `/cmd_vel` is used.
