# HyperFrames V2 Workflow Notes Applied To VibeMotion

The HyperFrames V2 lesson maps directly to VibeMotion's motion quality problem:
high-quality AI motion is not a one-shot prompt. It is a repeatable loop.

## Build -> Integrate -> Tune

1. Build: create a storyboard or motion plan before render.
2. Preview/render: produce an MP4 or browser-visible preview.
3. Visual QA: inspect frames, timing, smoothness, and artifacts.
4. Fix: patch the smallest systemic issue.
5. Integrate: save the working behavior as a preset, regression scenario, or skill note.
6. Tune: rerun the same visual scenario after every related change.

## Product Ideas Integrated

- Storyboard before render: QA runner writes a `storyboard.json` for non-trivial
  whole-frame motion.
- One card per beat: card/group scenarios should prefer one controlled layer
  action per beat instead of simultaneous clutter.
- Controlled motion graphics as layers: generated graphics remain explicit
  layer actions with ids, timing, and delete/edit controls.
- Visual reference/component-based presets: tests treat preset names and phase
  plans as inspectable contracts, not hidden prompt text.
- Save good behavior: every passing scenario creates regression artifacts in
  `qa_artifacts/motion_autotest`.

## What This Means For Engineering

- A prompt must become `operation -> action stack -> phase plan -> deterministic DSL`.
- Preview and final render share the same motion contract.
- Visual evidence is a first-class output.
- Failed or ugly motion should produce a root-cause note before any patch.
