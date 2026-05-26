# License Review

Date: 2026-05-25

This is an engineering review for public GitHub publication. It is not legal
advice.

## Decision

Use Apache-2.0 for VibeMotion source code.

Rationale:

- HyperFrames, the most important upstream motion/rendering reference, is
  Apache-2.0. Apache-2.0 is compatible with MIT dependencies and preserves
  patent/license notice expectations.
- `video-use` is MIT, which is permissive and compatible with Apache-2.0.
- Direct Python dependencies are permissive in the installed metadata checked
  locally.
- LTX/Gemma model weights are not source-code dependencies and must not be
  bundled. Their own model terms remain separate from the source code license.

## Reviewed Sources

| Item | Evidence | Result |
| --- | --- | --- |
| HyperFrames | `vendor/hyperframes/LICENSE`, upstream `https://github.com/heygen-com/hyperframes` | Apache-2.0 |
| video-use | `vendor/video-use/LICENSE`, upstream `https://github.com/browser-use/video-use` | MIT |
| LTX-2 Python packages | `https://github.com/Lightricks/LTX-2`, local installed `ltx-pipelines` metadata | LTX-2 Community License observed for the LTX-2 package/model family |
| LTX-2.3 model weights | `https://huggingface.co/Lightricks/LTX-2.3/blob/main/LICENSE` | LTX-2 Community License Agreement |
| Gemma 3 text encoder | `https://ai.google.dev/gemma/terms` and Hugging Face model pages | Gemma Terms of Use |
| GSAP runtime CDN | npm package metadata and GreenSock standard license link | Standard no-charge license, not open-source MIT/Apache |
| FFmpeg | External system dependency, not bundled | Depends on user-installed FFmpeg build |

## Practical Consequences

- Keep `models/` ignored. Do not push LTX, Gemma, Whisper, Ollama, or other
  model weights.
- Keep `vendor/` ignored unless we intentionally vendor a dependency and carry
  its license/notice files.
- Keep generated HyperFrames/video assets out of Git unless their source media
  rights are clear.
- Treat GSAP as an external runtime dependency. If VibeMotion becomes a hosted
  commercial motion editor, review the GSAP terms again or replace the CDN
  dependency with native Web Animations/CSS.
- If distributing a packaged desktop app, include third-party notices for the
  exact bundled Python wheels, FFmpeg build, Node packages, and models.

## Current Repository State

- Root `LICENSE`: Apache-2.0.
- `THIRD_PARTY_NOTICES.md`: added for direct upstreams and runtime/model notes.
- `.gitignore`: excludes local projects, models, QA output, vendor checkouts,
  style presets, Figma caches, logs, and media renders.
- Publication audit: `python scripts/audit_publication.py`.
