# Project25 brief transcription and source notes

Source: `references/source_materials/Project25_brief.zip`, read-only extraction to `/tmp/house_sitter_v2_source_audit_LpAaoM/`. Images are listed in ZIP order. Text below was read directly from the images; no OCR correction or inferred wording was used.

| Order | Image | Pixels | Content confidence |
|---|---|---:|---|
| 1 | `brief/b1a1f428a3b0ca5f5a9c8fb5e0a849c4.png` | 668 × 968 | High |
| 2 | `brief/b032aa2a9dd99f55746382d740e1f438.png` | 646 × 966 | High |
| 3 | `brief/7f35905bdceb7503c2f26d7a377e4d40.png` | 684 × 246 | High |

## Project title and supervision

- **Project 25** — *Reinventing Smart Homes with Robot Assistants*.
- Supervisor: Jagmohan Chauhan (`jagmohan.chauhan@ucl.ac.uk`).

## Objective

The stated objective is to repurpose domestic robotic vacuum cleaners into intelligent, multi-purpose household agents that perform sensing and monitoring during idle periods. It proposes using existing onboard sensors to create practical “digital twin” capabilities and improve home awareness, safety, and usability without additional hardware.

## Background

The brief says robotic vacuums are mature consumer platforms but often idle at a charging station for over 20 hours per day. It contrasts reuse of their sensors with fixed cameras, dedicated scanners, or smart sensor networks, and hypothesises that embedded robotic-vacuum sensors can support new software-only applications. [Image 1]

## Project brief

The platform is to be transformed into an “intelligent house-sitter” operating during idle time. The brief says that, **building on an established research codebase and a fully functional robot platform**, a student will adapt and extend software to demonstrate sensing, monitoring, and mapping capabilities. It says patrols should collect sensor data and give actionable insights, in collaboration with the University of Bath. [Image 2]

## Methods stated by the brief

1. Platform familiarisation and setup: work with an existing robotic-vacuum platform and accompanying software; review prior work, published paper, and demonstrations.
2. Environmental monitoring: use onboard sensors for open windows, unexpected obstacles, and humidity/temperature changes; define interpretable alert thresholds.
3. Mapping and Digital Twin capabilities: generate updated indoor layouts and visualise new furniture or layout changes.
4. Safety and home awareness: enhance safety without additional fixed security cameras and explore coverage/privacy/fidelity trade-offs.
5. System integration and demonstration: combine components into an autonomous idle-time system and show intelligence, utility, and sustainability. [Image 2]

## Expected deliverables

- A working prototype demonstrating autonomous patrolling, environmental monitoring, and mapping using a robotic vacuum.
- A functional demonstration of Digital Twin capabilities derived solely from onboard sensors.
- Documentation of system design, limitations, and potential real-world applications.
- An evaluation of practical impact, sustainability benefits, and future extensions. [Images 2–3]

## Related works and existing-platform commitment

The brief says more than 100 potential applications had already been researched and asserts possession of a fully functional robot platform; it links a video demo and DOI `https://dl.acm.org/doi/10.1145/33706598.3714266`. This is a **source claim**, not evidence in this repository that the cited platform, paper, or video has been reproduced here. [Image 3]

## Uncertainty and limits

- No unreadable text was encountered in the three supplied PNGs.
- The images do not identify a robot model, ROS version, repository URL, sensor message definitions, evaluation protocol, or source code license.
- “Autonomous”, “onboard sensors”, and “fully functional robot platform” are requirements/claims in the brief; they are not treated here as evidence of current implementation or physical-robot validation.
