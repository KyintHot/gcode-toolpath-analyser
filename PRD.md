# Product Requirements Document — G-code Toolpath Analyser

| | |
| --- | --- |
| **Product** | G-code Toolpath Analyser |
| **Version** | 1.0 |
| **Status** | Shipped (v1.0.0) |
| **Owner** | RAI |
| **Last updated** | 2026-11-15 |

---

## 1. Summary

A local-first web app and CLI that read a standard `.gcode` file, reconstruct the
exact toolpath (linear moves plus true circular/helical arcs), plan it with an
acceleration-limited motion model, and report engineering metrics — real print
time, filament volume/mass, and bounding-box dimensions — next to an interactive
3D rendering of the path coloured by planned speed.

## 2. Problem

Slicer estimates and naive `distance / feedrate` calculations systematically
understate machining time because every direction change forces the machine to
decelerate and re-accelerate. The error is worst on small, detail-heavy parts —
exactly the parts where time and material estimates matter most for quoting,
scheduling and material planning.

Engineers, makers and print-farm operators currently have three unsatisfying
options:

1. trust the slicer's ETA (opaque, machine-specific, no breakdown),
2. run the print or a dry run (slow, consumes material and machine time),
3. do hand maths on distance and feedrate (wrong, because it ignores kinematics).

There is also no quick way to *see* what a file actually does — where the fast
moves are, whether the part fits the machine, how many layers and retractions it
contains — without opening the slicer that produced it.

## 3. Goals

| # | Goal | Measure |
| --- | --- | --- |
| G1 | Reproduce the toolpath faithfully, including arcs | Rendered path matches commanded geometry within the arc chord tolerance |
| G2 | Produce a physically plausible time estimate | Estimate ≥ naive estimate, with the acceleration overhead exposed as a percentage |
| G3 | Report trustworthy material figures | Filament length/volume/mass derived from E values, filament diameter and density |
| G4 | Make the result inspectable | Interactive 3D view with speed colouring, travel toggle and per-layer isolation |
| G5 | Work with zero setup and zero cloud | One command to run; no network required after install |

## 4. Non-goals

- Slicing, repairing or editing G-code.
- Simulating material physics (shrinkage, warping, pressure advance).
- Replaying firmware exactly (queue depth, look-ahead corner cases, per-axis
  junction behaviour beyond the chosen model).
- Sending files anywhere: analysis is local and in-memory only.
- Multi-user accounts, cloud storage or collaboration (v1).

## 5. Target users

| Persona | Need |
| --- | --- |
| **Maker / hobbyist** | "How long will this actually take, and how much filament do I need?" |
| **Print-farm / job-shop operator** | Fast, repeatable quotes across many files without opening a slicer |
| **Machine owner / tuner** | "What happens to time if I raise acceleration or change the cornering model?" |
| **CNC / laser user** | Toolpath inspection and envelope checks for non-extruding programs |

## 6. User stories

- As a maker, I want to drop a `.gcode` file and immediately see time, filament
  and dimensions, so I can plan a print without opening the slicer.
- As an operator, I want to change machine parameters and see the time estimate
  update, so I can compare profiles before committing to a job.
- As a tuner, I want to see the path coloured by actual planned speed, so I can
  spot where acceleration limits are throttling the print.
- As a CNC user, I want bounding-box and path metrics for a file with no
  extrusion, so I can verify the program fits my envelope.
- As a developer, I want a CLI and JSON output, so I can automate analysis in a
  pipeline.

## 7. Functional requirements

### 7.1 Parsing (FR-1)

| ID | Requirement |
| --- | --- |
| FR-1.1 | Parse `G0/G1` linear moves and `G2/G3` arcs. |
| FR-1.2 | Maintain modal state: units (`G20/G21`), distance mode (`G90/G91`), planes (`G17/G18/G19`), feed mode (`G93/G94`), extruder mode (`M82/M83`), work offsets (`G92`). |
| FR-1.3 | Handle dwells (`G4`), barriers (`G28`, `M0/M1/M25/M600`, tool changes) as zero-speed breaks. |
| FR-1.4 | Convert volumetric extrusion (`M200 D…`) E values to filament length. |
| FR-1.5 | Treat in-file `M204` as a request, clamped by the configured machine acceleration. |
| FR-1.6 | Skip non-motion M-code parameters (`M201 X…`) so they never create motion. |
| FR-1.7 | Report unsupported/approximated constructs as diagnostics, never fail silently. |

### 7.2 Arc interpolation (FR-2)

| ID | Requirement |
| --- | --- |
| FR-2.1 | Support `I/J/K` centres and `R` radii, including negative `R` (major arc). |
| FR-2.2 | Support all three planes and full circles. |
| FR-2.3 | Support helical arcs (in-plane arc with Z change). |
| FR-2.4 | Subdivide arcs into chords with bounded chordal deviation (`arc_tolerance`, default 0.01 mm). |
| FR-2.5 | Use the same chord set for rendering and timing so the two never disagree. |
| FR-2.6 | Keep analytic arc length available for reporting. |

### 7.3 Motion planning (FR-3)

| ID | Requirement |
| --- | --- |
| FR-3.1 | Compute per-block speed ceilings from commanded feed and per-axis velocity limits. |
| FR-3.2 | Compute junction speeds from junction deviation by default, classic jerk optionally. |
| FR-3.3 | Run forward and backward passes so each block can only reach speeds it can accelerate/decelerate to. |
| FR-3.4 | Integrate trapezoidal (or triangular) block times between junction speeds. |
| FR-3.5 | Stop the machine (v = 0) at barriers and between discontinuous runs. |
| FR-3.6 | Report the naive `distance / feed` baseline alongside the planned time. |

### 7.4 Metrics (FR-4)

| ID | Metric |
| --- | --- |
| FR-4.1 | Total time, split into printing, travel, dwell and retraction. |
| FR-4.2 | Acceleration overhead versus naive time, in percent. |
| FR-4.3 | Filament length, volume, mass; retracted and primed filament. |
| FR-4.4 | Part bounding box (deposited moves) and full motion envelope, with build-volume fit. |
| FR-4.5 | Layer count and median layer height. |
| FR-4.6 | Arc count and total arc length. |
| FR-4.7 | Peak/mean speed and peak/mean volumetric flow. |
| FR-4.8 | Retraction and tool-change counts; slicer, nozzle/bed temperature, fan, volumetric-E flags. |
| FR-4.9 | Per-layer time/filament table and per-feedrate time buckets. |

### 7.5 Interfaces (FR-5)

| ID | Requirement |
| --- | --- |
| FR-5.1 | Web app: drag-and-drop or file picker, sample files, live re-plan on parameter change. |
| FR-5.2 | 3D view: speed-coloured extrusion, travel toggle, envelope box, per-layer isolation and playback. |
| FR-5.3 | Analysis tab: per-layer time chart, time-by-feedrate chart, timing and diagnostics tables. |
| FR-5.4 | CLI: human-readable report, `--json` export, self-contained `--html` Plotly report. |
| FR-5.5 | HTTP API: health, samples, analyze (upload), re-plan by analysis id. |
| FR-5.6 | All machine/material parameters overridable without re-uploading the file. |

## 8. Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-1 | Analysis of a 2,000-line file completes in well under a second; 6,500 blocks in ~0.1 s. |
| NFR-2 | Uploads capped at 256 MB; the last 6 analyses cached in memory for re-planning. |
| NFR-3 | Rendering payload decimated to a point budget (default 80,000) without losing corners or layer changes. |
| NFR-4 | No network dependency at runtime; Plotly is vendored. |
| NFR-5 | Invalid configuration returns a clear 400 with a human-readable message; analysis failures never surface as bare 500s. |
| NFR-6 | Runs on Python 3.11+ with only FastAPI and Uvicorn as runtime dependencies. |
| NFR-7 | No file contents persisted to disk or sent off the machine. |

## 9. Metric definitions

- **Planned time**: Σ block traversal times from the trapezoidal profile between
  junction speeds, plus dwells and retraction time.
- **Naive time**: Σ block length ÷ commanded feed (clipped by axis limits), plus
  dwells and retractions.
- **Acceleration overhead**: `planned / naive − 1`, in percent.
- **Junction deviation**: `v² = a·δ·sin(θ/2) / (1 − sin(θ/2))`, θ the interior
  angle between consecutive path directions (Marlin 2 / PrusaSlicer model).
- **Classic jerk**: `v ≤ jerk / max(|u_prev,axis|, |u_next,axis|)` per axis.
- **Filament volume**: extruded filament length × π(d/2)².
- **Mass**: volume × density.
- **Bounding box**: min/max of deposited points (falls back to the motion
  envelope for files that never extrude).

## 10. UX requirements

- Dark, engineering-oriented interface with a clear KPI row: time, material,
  bounding box, motion.
- Parameters grouped as Machine / Material, with presets (Ender 3, Prusa MK3S+,
  Voron 2.4, Bambu X1C, CNC router) and a reset.
- Non-blocking loading state, dismissible error banner, and a diagnostics list
  for anything the analyser could not model exactly.
- Keyboard-accessible dropzone; layout degrades to a single column on narrow
  screens.

## 11. Success metrics

| Metric | Target |
| --- | --- |
| Estimate sanity | Planned time always ≥ naive time for files with direction changes |
| Arc fidelity | Chord deviation ≤ configured tolerance; endpoints land exactly |
| Transfer size | ≤ ~2 MB JSON for a 6,500-block file after decimation |
| Time to first result | < 1 s for typical slicer output on a laptop |
| Correctness | Test suite covers parser states, arc geometry, planner physics and metrics |

## 12. Release plan

| Milestone | Scope | Status |
| --- | --- | --- |
| M1 — Core engine | Parser, arcs, planner, metrics, tests | Done |
| M2 — Interfaces | FastAPI + web app, CLI, HTML export | Done |
| M3 — Polish | Presets, diagnostics, vendored Plotly, docs | Done (v1.0.0) |
| M4 — Next | mm:ss timeline scrubber, multi-file comparison, G-code export of the re-planned path, nozzle/temperature time model | Proposed |

## 13. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Estimate differs from firmware | Medium | Transparent model, exposed parameters, naive baseline shown for comparison |
| Files with firmware retraction (`G10/G11`) | Time understated | Detected and reported as a note |
| Huge files exhaust browser memory | High | Server-side decimation to a point budget; upload cap |
| Slicer-specific extensions | Medium | Unknown commands ignored with diagnostics rather than aborting |
| In-file `M204` vs machine config confusion | Medium | File value reported, config acts as the ceiling, note explains it |

## 14. Open questions

- Should the planner account for nozzle heating/cooling time from `M109/M190`?
- Is a per-layer time heatmap more useful than the current per-layer bar chart?
- Should re-planning persist user profiles server-side for multi-user setups?
