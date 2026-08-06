# Supervisor Research Demonstration

## Preparation before the meeting

The main command is:

    python3 scripts/run_supervisor_research_demo.py

The program locates the repository through Path(__file__).resolve(). Before the meeting, prepare the optional paper-results cache with:

    python3 scripts/build_paper_results.py --regenerate --output-dir /tmp/house_sitter_paper_results_final

The live demonstration never regenerates all 570 experiment runs by default.

## Controls and command-line options

After every step, use:

    [Enter] Continue
    [s] Skip
    [r] Retry
    [q] Quit

Ctrl+C also exits safely. Use --start-at 8 to resume from the research-results section. Other options are --skip-3d, --skip-2d, --paper-results-dir, --output-dir, --non-interactive, --list-steps, and the meeting-preparation-only option --prepare-paper-results.

## The 13 demonstration steps

0. Pre-flight Check — “This demo uses the current simulation framework and previously generated experiment results.”
1. Project Objective and System Pipeline — “The robot patrols the home during idle time, checks each room and updates the Digital Twin when it detects a change.”
2. Static 3D Residential Preview — “This is the residential environment used in the project. The 3D view is only a static preview.”
3. 2D Patrol Demonstration — “The robot follows safe paths between labelled rooms and avoids walls and furniture.”
4. Live Monitoring Scenario — “In this scenario, the robot patrols the home and detects a new obstacle in the kitchen.”
5. Detected Anomaly and Alert — “The system explains what changed, where it happened and what action the user should take.”
6. Digital Twin Update — “Only the affected room receives anomaly updates in the Digital Twin. The other rooms retain normal status.”
7. Monitoring Report and Safe Return — “The report summarises the patrol, the detected anomaly and the robot’s return to the charging area.”
8. Monitoring Robustness — “The robustness test detected all injected anomalies, but one temporary layout disturbance caused a false alarm.”
9. Temporal Filtering Trade-off — “Using two observations removes the temporary false alarm, but it also increases delay and misses some short-lived changes.”
10. Patrol Strategy Trade-off — “Battery-aware patrol covers more rooms, while risk-priority patrol uses less distance and energy.”
11. Research Contributions and Limitations — “The project provides a reproducible simulation and evaluation framework, but it has not yet been validated on a physical robot.”
12. Final Summary — “The monitoring pipeline and the three main experiments are complete. The next stage is dissertation writing and final integration.”

## Fallback behaviour and safety boundary

The 3D step only opens the static preview_house_v1_3d.sh residential preview. The 2D step is a deterministic patrol demonstration from run_house_v1_visual_demo.py. If a GUI cannot be opened, the program continues with a file path or text summary.

When cached paper results are absent, the research steps use text fallback summaries and do not regenerate experiments. Monitoring artefacts are stored in monitoring_artifacts/ under the selected output directory, and command logs are stored in logs/. The program only terminates GUI processes that it created.

This is a simulation-only project. The static 3D preview is not dynamic Gazebo/Nav2 navigation for house_v1. The project makes no claim of physical-robot or real-sensor validation.
