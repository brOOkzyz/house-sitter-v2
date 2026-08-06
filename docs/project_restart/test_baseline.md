# Test Baseline After the Project Restart

## Scope and correction

The source-audit commit `a5a6f7167bc1937da735ad8551ce3dbf48d98f0f` changes only documentation relative to the Phase 1 baseline `6c069a26deaa6e1db88505bbda216d72cdc4ba52`.  It does **not** make the complete legacy suite pass.  Any previous statement that the complete `pytest` suite passed is corrected by this document.

The Phase 1 and new-mainline acceptance gate is:

```bash
PYTHONPATH="$PWD:$PWD/.venv_raptor_lite/lib/python3.12/site-packages" \
  rtk pytest -q tests/raptor_lite
```

It passed as `6 passed` during this audit.  New RaPToR-Lite work must keep this set passing and must add its own relevant tests.  The archived Gazebo/Nav2/Final Demo tests are not evidence that the new mainline has passed.

## Full-suite comparison

The supplied current-run log, `/home/brookz/.local/share/rtk/tee/1786044414_pytest.log`, reports `7 failed, 450 passed, 439 subtests passed`.  The first isolated worktree run at the Phase 1 baseline reproduced those same seven named failures and counts.  A repeat baseline run additionally exposed the unrelated legacy test `tests/test_supervisor_demo.py::SupervisorDemoTests::test_ready_attach_only_allows_pipeline_after_readiness` (`8 failed, 449 passed`), so the archived full suite is also not stable enough to be a new-mainline gate.  None of the seven supplied failures is a regression from the documentation audit.

| Test | Classification | Evidence and environment relation |
| --- | --- | --- |
| `tests/test_final_3d_house_sitter_demo.py::Final3DDemoTests::test_cli_dry_run_is_english_and_does_not_start_gui` | PRE-EXISTING; ENVIRONMENTAL | The dry-run exits 2 because the optional `ros_gz_sim` package is unavailable; this predates the audit. |
| `tests/test_final_3d_house_sitter_demo.py::Final3DDemoTests::test_preview_dry_run_needs_no_menu_input_and_writes_control_artifacts` | PRE-EXISTING; ENVIRONMENTAL | The same unavailable `ros_gz_sim` spawn dependency makes the preview exit 2; no audit code is involved. |
| `tests/test_gazebo_static_demo.py::GazeboStaticDemoTests::test_atomic_output_failures_leave_no_artifacts` | PRE-EXISTING; ENVIRONMENTAL | The supplied invocation placed the Phase 1 venv in `PYTHONPATH`; `/opt/ros/jazzy/bin/xacro` then cannot find its system `xacro==2.1.1` metadata and exits 1. |
| `tests/test_gazebo_static_demo.py::GazeboStaticDemoTests::test_cli_outputs_are_byte_deterministic_in_independent_processes` | PRE-EXISTING; ENVIRONMENTAL | Same `PYTHONPATH`/system-`xacro` metadata conflict. |
| `tests/test_gazebo_static_demo.py::GazeboStaticDemoTests::test_goal_and_region_colors_match` | PRE-EXISTING; ENVIRONMENTAL | Same `PYTHONPATH`/system-`xacro` metadata conflict. |
| `tests/test_gazebo_static_demo.py::GazeboStaticDemoTests::test_transform_is_uniform_and_world_is_static_without_motion_plugins` | PRE-EXISTING; ENVIRONMENTAL | Same `PYTHONPATH`/system-`xacro` metadata conflict. |
| `tests/test_gazebo_static_demo.py::GazeboStaticDemoTests::test_world_contains_four_labels_goals_and_map_coordinates` | PRE-EXISTING; ENVIRONMENTAL | Same `PYTHONPATH`/system-`xacro` metadata conflict. |

The xacro diagnosis is directly reproducible: with the venv path injected into `PYTHONPATH`, xacro raises `importlib.metadata.PackageNotFoundError: No package metadata was found for xacro`; without that injection it materializes the installed TurtleBot4 xacro successfully.  This is an environment composition problem, not a passing legacy test.

## Baseline policy

- Do not report `rtk pytest -q` as passing on this host while the above failures remain.
- Do not remove, skip, or weaken archived tests to change the count.
- Treat legacy Gazebo/Nav2/Final Demo coverage as regression/history evidence only until a separately specified environment-repair task addresses it.
- Phase 2 may proceed only after the Phase 1 gate above passes and every new-mainline test added for Phase 2 passes; it does not require claiming that the archived suite passes.
