# RaPToR original Overleaf audit

Source: read-only extraction of `references/source_materials/RaPToR_original_Overleaf.zip` to `/tmp/house_sitter_v2_source_audit_LpAaoM/raptor/`.

## File tree and classification

| Files | Classification | Audit finding |
|---|---|---|
| `acmart.cls`, `ACM-Reference-Format.bst`, `acm*.bbx/cbx/dbx`, `acm-jdslogo.png`, `acmguide.pdf`, `README.txt` | ACM template material | Default ACM distribution material, not RaPToR implementation evidence. |
| `main.bib` | Template bibliography | General BibTeX string definitions; no cited RaPToR study or implementation reference was found. |
| `main.tex` | RaPToR draft plus template scaffold | Contains title and a short motivation/features list, but retains sample authors, conference metadata, CCS terms, keywords, teaser, acknowledgements, and an empty abstract. |

## Genuine draft content

`main.tex` titles the draft **“RaPToR: Rapid Prototyping Toolkit for Robotics”**. Its motivation says ROS terminal interaction is a barrier for non-experts and makes an uncited broad claim that no end-to-end platform supports non-experts to set up, program, test, and deploy custom robot applications.

### Version 1 features claimed as implemented

The draft explicitly lists four features under “Implemented Features”:

1. real-time robot control via terminal and keyboard;
2. real-time numeric/textual sensor display;
3. generation of template code for actions, sensors, and a WebSocket communication function;
4. recording a user-demonstrated action sequence.

It links a YouTube demo for ideas about the toolkit. These are **draft claims only**: the ZIP contains no source code, repository URL, executable, API specification, test, robot model, ROS version, experiment, or data supporting them.

### Features explicitly planned, not implemented

The “Potential Features” list includes a natural-language task-executing agent, automatic ROS-to-robot setup/testing, visual sensor values, multi-robot control, add-on components, and visual programming. They must not be described as existing RaPToR functionality.

## Evidence and limitations

- The archive is **not a runnable codebase**. It contains no Python, ROS, launch, package, Docker, CI, data, experimental result, or implementation artifact.
- The only external reference is the YouTube URL in `main.tex`; it was not fetched or treated as verification.
- No robot type, hardware API, ROS distribution, software architecture, or evaluation method is specified.
- The LaTeX file remains materially based on the ACM sample (`sigconf,anonymous`, sample authors/rights/CCS/teaser/acks), so presentation details are not attributable to RaPToR.

## Restart implication

RaPToR-Lite may retain the draft’s accessibility motivation, but capability-grounded structured tasks, mandatory verification, simulation-first evaluation, and the House-Sitter application are new project design unless separately marked as repository evidence.
