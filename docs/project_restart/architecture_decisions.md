# Architecture and backend decisions

## Minimal target architecture

```text
optional NL adapter -> TaskSpec -> capability registry -> verifier -> backend adapter
                                      |                    |             |
                                      +-> evidence/artifacts <- executor <-+
                                                           |
                                                House-Sitter application layer
```

- **Task creation:** CLI first; a future natural-language adapter returns a `TaskSpec` candidate only. It has no backend handle. **PROPOSED**.
- **Structured representation, registry, verifier, mock executor, artifacts:** retain Phase 1 as the single authoritative core. **REPOSITORY-EVIDENCED**.
- **Backend abstraction:** add one narrow deterministic backend protocol in Phase 2; do not refactor the current executor until that protocol has tests. **PROPOSED**.
- **House-Sitter layer:** adapt existing observation/anomaly/twin/alert/report functions behind that backend. **REPOSITORY-EVIDENCED** inputs, **PROPOSED** integration boundary.
- **UI:** optional CLI/text output only after evidence is stable; no UI is a research dependency. **PROPOSED**.

## Backend comparison (audit date: 2026-08-06)

| Candidate | Local availability / Jazzy | Complexity & CI/headless | Visual/sensor value | Risk of lifecycle/TF/localisation regression | Decision |
|---|---|---|---|---|---|
| Pure Python custom 2D household simulator | Python available; new small code only | Lowest; deterministic, seeded, fast batch CI | Minimal visual value; sufficient events/noise/ground truth | None | **Primary experimental backend** |
| Webots | `webots` command/import absent | Would require installation/integration | Potentially good 3D/sensors | Lower ROS graph burden than current stack but unproven locally | Do not install now; candidate visual backend after a separate feasibility gate |
| Existing Gazebo without Nav2 | `gz`, ROS Jazzy packages and TurtleBot4 Gazebo package present | Moderate/high startup overhead | Existing house/TurtleBot visual asset | Control/spawn instability remains documented | **Primary visual demo backend only**, optional and non-evaluative |
| Gazebo + Nav2 | Installed packages present | Highest maintenance; difficult repeatable batch runs | Navigation visualisation | High: prior readiness, TF, localisation/lifecycle variability | Archive/optional regression only |
| Nav2 loopback simulation | Package available | Still introduces Nav2 state and technical validity issues | Low incremental value | High | Reject as main backend |
| PyBullet/lightweight alternative | `pybullet` import absent | New dependency/integration required | Moderate 3D but no existing case asset | Low ROS risk, unknown adoption cost | Fallback candidate only if pure 2D cannot meet a required experiment/visual need |

## Decisions

1. **Primary experiments:** a custom seeded 2D household simulator, not a robot-navigation stack. It makes ground truth, noise, missing observations, battery state and paired scenarios controllable; the contribution is verification/reliability rather than a simulator. **PROPOSED**.
2. **Primary visual demo:** existing Gazebo *without Nav2* as a best-effort visual presentation of the House-Sitter setting, after Phase 5 smoke criteria. It cannot gate experimental conclusions. **REPOSITORY-EVIDENCED** availability; **PROPOSED** role.
3. **Fallback visual backend:** render deterministic 2D traces/artifacts (and optionally a Webots feasibility prototype later). **PROPOSED**.
4. **Gazebo/Nav2:** retain untouched as archived/technical regression evidence. Do not claim it validates RaPToR-Lite or physical autonomy. **REPOSITORY-EVIDENCED** instability history and **PROPOSED** policy.

## Backend acceptance gates

- A backend must consume only an approved `TaskSpec`, be seedable, emit trace/ground truth, support a bounded run, and be executable headlessly before entering experiments. **PROPOSED**.
- A visual backend must never upgrade simulation observations to physical sensor evidence. **PROPOSED**.
