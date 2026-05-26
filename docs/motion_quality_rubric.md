# VibeMotion Motion Quality Rubric

VibeMotion motion QA is a Build -> Integrate -> Tune loop, not a compile check.
Every non-trivial motion change should produce a storyboard or motion plan, render
evidence, a visual QA report, and a regression artifact.

## Score Categories

Each category is scored from 0 to 5. A scenario passes only when every critical
category is at least 3 and there are no hard visual failures.

| Category | What Good Means | Hard Failure |
| --- | --- | --- |
| Prompt fidelity | The rendered phases match the user's literal prompt and requested scope. | Requested phase is missing or wrong scope animates. |
| Timing accuracy | Phase start/end times are within tolerance of the plan. | First/last/hold timing is visibly wrong. |
| Smoothness | Movement eases cleanly and settles without jumps. | Snaps, jitter, or visible frame jumps. |
| Visual beauty | Motion feels deliberate, readable, and premium. | Cluttered, random, or amateur-looking motion. |
| Frame integrity | Hold state matches the original Figma frame. | Text, icons, masks, or layout shift in hold state. |
| No duplicates | Animated layer replaces/covers baked pixels cleanly. | Ghost layer, double text, or duplicated image crop. |
| Preview/render parity | Live preview and final render follow the same motion contract. | Preview and final MP4 disagree semantically. |
| UI flow | Modal buttons, status, cancel, apply, and timeline feedback behave. | UI hangs, mutates on cancel, or hides errors. |
| LTX quality | Source preview, generated video, apply, and final render are contained and decodable. | Black preview, distorted output, crash, or wrong layer replaced. |
| Performance/VRAM | Heavy paths gate by available VRAM and degrade gracefully. | Native crash or unbounded GPU memory use. |

## Required Evidence

- JSON report under `qa_artifacts/motion_autotest/<run_id>/`.
- Rendered MP4s or existing MP4 references.
- Extracted sample frames for intent-critical timestamps.
- Visual metrics such as mean pixel diff, edge score, frame decode status, and
  frame-size/aspect checks.
- Notes on root cause and fix when a scenario fails.

## Gate Rules

- `py_compile` alone is never enough.
- A whole-frame choreography pass needs storyboard/motion plan, render, extracted
  frames, and hold-frame integrity checks.
- A selected-layer pass needs proof that only the selected region changes while
  outside pixels stay stable.
- LTX pass needs pre-generation source preview check, output decode/aspect check,
  and VRAM fallback behavior.
