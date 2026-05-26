# VibeMotion Notice

VibeMotion is a local AI-assisted video editing prototype. It is intended to run
on the user's own Windows machine and keep local projects, rendered media,
model weights, tokens, and environment files outside the Git repository.

This repository contains VibeMotion source code, launch scripts, the Figma plugin
source, QA scripts, and documentation. It does not include generated videos,
private projects, downloaded model weights, Figma exports, local logs, local
cache folders, or API tokens.

VibeMotion source code is licensed under Apache-2.0. See `LICENSE`.

Third-party projects, services, tools, and models keep their own licenses and
terms. VibeMotion is not affiliated with, sponsored by, or endorsed by Figma,
Lightricks, Google, Ollama, HeyGen, Browser Use, GreenSock, FFmpeg, or any other
upstream provider mentioned in this repository.

Model weights are intentionally not redistributed here. LTX, Gemma, Ollama,
Whisper/faster-whisper, and any other user-selected model material must be
downloaded and used under the relevant upstream terms.

Before publishing, distributing, or using this project commercially, review:

- `THIRD_PARTY_NOTICES.md`
- `docs/license_review.md`
- upstream model license pages and terms
- FFmpeg build licensing for the selected binary distribution
- GreenSock/GSAP terms if hosted previews or templates use GSAP

If source code, binaries, model weights, or assets from another project are
copied into this repository later, add the attribution and license details before
committing.
