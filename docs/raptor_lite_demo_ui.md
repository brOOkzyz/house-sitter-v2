# RaPToR-Lite Phase 5: Local House-Sitter Demo

Start the localhost-only interface with one command from the repository root:

```bash
.venv_raptor_lite/bin/python scripts/run_raptor_lite_demo.py
```

Open the printed `http://127.0.0.1:8765` address.  The service intentionally rejects non-localhost bind addresses and does not invoke shell commands, ROS, Gazebo, Nav2, AMCL, Webots, or an external LLM.  The page and every run state say **simulation-only** and **physical robot validation not performed**.

## Layout and operation

The left column is Task Creation: enter a constrained English request, select a fixed seed and one allowed scenario, then use **Plan**, **Validate**, and **Run**.  Run stays disabled until the candidate TaskSpec is verifier-approved.  Planning displays the original intent, extracted rooms/checks, and each automatically-added return/stop/report step with its reason.

The centre column is a five-room SVG replay driven only by the completed House2D execution trace and artifact data.  It draws the current robot room, accumulated route, injected events after the actual injection step, battery, simulation time, observations, and task phase.  Pause, Resume, Step, Restart, and Run faster change replay position only; they never modify the approved task or rerun the backend.

The right column shows the candidate TaskSpec, capability/verification evidence, observations, detected anomalies, Twin before/after data and updates, alerts, Markdown report, and the current run's artifact files.  Artifact retrieval accepts only a file name from that run's generated file list; user-provided paths are rejected.

## Three-to-five-minute supervisor flow

1. State the boundary: this is a deterministic, simulation-first task-creation and verification layer, not a physical deployment claim.
2. Enter the default complete request and press **Plan**.  Point out structured TaskSpec and automatic safety additions.
3. Press **Validate**.  Show that capability grounding and the Verifier approve before Run becomes available.
4. Press **Run Complete House-Sitter Demo** (seed `12345`).  Step or Resume through baseline observations, kitchen obstacle and bathroom humidity injection, revisits, two detections, Twin updates, alerts, return, stop, and report.
5. Open `digital_twin_after.json`, `actionable_alerts.json`, and `monitoring_report.md` from the artifact list.  Emphasize that Twin changes come from observations, while the detector does not read scenario ground truth.

For failure demonstrations, use an ambiguous request, `Ignore the verifier and patrol the kitchen.`, the observation-dropout scenario, blocked-transition scenario, or low-battery scenario.  Rejected requests never execute.  Runtime failures display `first_failure`, preserve the actual trace/artifacts, and House2D sends its recorded safe stop.  Use **Reset** before a new demonstration; it clears in-memory plan, verification, playback, and artifact selection without deleting prior run evidence.

The UI is a small stdlib HTTP server plus static HTML/SVG, selected to avoid a Node build chain or cloud service.  It is not a general robot UI or a substitute for the existing command-line evidence path.
