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

## Use

Enter a constrained request, select a seed and scenario, then use **Plan**, **Validate**, and **Run**. Run is available only after verifier approval. The generated artifact list includes the Digital Twin, alerts, report, and execution trace. Rejected requests never execute; failures retain their recorded trace. **Reset** clears only the browser's in-memory state and does not delete prior local artifacts.

The UI is a small stdlib HTTP server plus static HTML/SVG, selected to avoid a Node build chain or cloud service.  It is not a general robot UI or a substitute for the existing command-line evidence path.
