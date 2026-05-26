# Changelog

## v0.1.0-pre-alpha.1 - 2026-05-26

First GitHub pre-alpha release of VibeMotion.

### Included

- Local Windows launcher with first-run setup via `Launch-VibeMotion.bat`.
- Automatic `.env` creation, `.venv` creation, Python dependency install, CUDA PyTorch install, FFmpeg check, Ollama check/model pulls, faster-whisper cache, Figma plugin registration, and LTX 2.3 model-pack check/download.
- Browser-based local video editor with upload, timeline, trim/split workflows, motion blocks, Figma frame/layer import, selected-layer motion, LTX layer animation, preview, and final MP4 render paths.
- Prompt-to-motion planning, motion presets, visual QA scripts, motion rubric, regression matrix, and one-hour soak runner.
- GitHub publication hygiene: `.gitignore`, `.env.example`, license/notice files, third-party notices, and publication audit workflow.

### Known Limitations

- This is pre-alpha software. UI/API behavior and project schema can change.
- First launch can be slow because Python packages and local models are large.
- LTX generation requires an NVIDIA GPU with enough VRAM; basic editing works without LTX.
- Some motion QA scenarios are still improving, especially complex whole-frame choreography and selected-layer parity edge cases.
- Model weights, user projects, rendered videos, logs, tokens, and QA artifacts are intentionally not included in the repository.

### Install

Download the repository ZIP, extract it, and run `Launch-VibeMotion.bat`.
