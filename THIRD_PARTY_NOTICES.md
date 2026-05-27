# Third-Party Notices

This document summarizes the main third-party projects and model sources that
VibeMotion is designed to interoperate with. It is an engineering compliance
note, not legal advice.

## Credits

Application shell, interface, workflow layer, and practical editing wrapper:

- AI Pulse: https://x.com/youraipulse
- Amir Mushich: https://x.com/AmirMushich

## Source Inspiration And Local Vendor Checkouts

The `vendor/` directory is ignored and is not intended to be published as part
of this repository.

| Component | Source | License observed | Notes |
| --- | --- | --- | --- |
| HyperFrames | https://github.com/heygen-com/hyperframes | Apache-2.0 | Local `vendor/hyperframes/LICENSE` and upstream GitHub identify Apache-2.0. |
| video-use | https://github.com/browser-use/video-use | MIT | Local `vendor/video-use/LICENSE` and upstream GitHub identify MIT. |

## Main Upstream Repository And Model Links

| Area | Upstream |
| --- | --- |
| Motion/video workflow reference | https://github.com/heygen-com/hyperframes |
| Video editing workflow reference | https://github.com/browser-use/video-use |
| LTX model/package family | https://github.com/Lightricks/LTX-2 |
| LTX 2.3 model weights | https://huggingface.co/Lightricks/LTX-2.3 |
| Gemma text encoder terms/model family | https://ai.google.dev/gemma/terms, https://huggingface.co/google/gemma-3-12b-it-qat-q4_0-unquantized |
| Public Gemma/LTX model pack fallback | https://huggingface.co/DeepBeepMeep/LTX-2 |
| Local LLM runtime | https://github.com/ollama/ollama |
| Backend framework | https://github.com/fastapi/fastapi, https://github.com/encode/uvicorn |
| Local transcription | https://github.com/SYSTRAN/faster-whisper |
| ML/runtime libraries | https://github.com/pytorch/pytorch, https://github.com/pytorch/vision, https://github.com/pytorch/audio |
| Hugging Face libraries | https://github.com/huggingface/diffusers, https://github.com/huggingface/transformers, https://github.com/huggingface/accelerate, https://github.com/huggingface/safetensors |
| Tokenizer/runtime helpers | https://github.com/google/sentencepiece, https://github.com/python-pillow/Pillow |
| Media tools | https://github.com/FFmpeg/FFmpeg |
| Animation runtime used by generated previews | https://github.com/greensock/GSAP |

## Direct Python Dependencies

The direct dependencies in `pyproject.toml` are not vendored into this
repository. Their installed license files were checked in the local virtual
environment.

| Package | Observed license family |
| --- | --- |
| FastAPI | MIT |
| Uvicorn | BSD-style |
| python-multipart | Apache-2.0 |
| Pillow | HPND/PIL-style permissive license |
| faster-whisper | MIT |
| diffusers | Apache-2.0 |
| transformers | Apache-2.0 |
| accelerate | Apache-2.0 |
| ltx-pipelines | LTX-2 Community License / package metadata does not expose a separate PyPI license |
| PyTorch / torchvision / torchaudio | BSD-style |
| sentencepiece | Apache-2.0 |
| safetensors | Apache-2.0 |

## External Runtime Tools

| Tool | Distribution status | Notes |
| --- | --- | --- |
| FFmpeg / ffprobe | Not bundled | Users install their own FFmpeg build. FFmpeg builds can be LGPL or GPL depending on enabled codecs. |
| Ollama models | Not bundled | User-selected local models are subject to their own model licenses. |
| GSAP | Loaded from CDN in generated previews | GSAP uses GreenSock's standard no-charge license, not MIT/Apache. Review before commercial distribution of a hosted product. |

## Model Weights

Model weights are intentionally excluded from Git by `.gitignore`.

| Model source | License / terms observed | Repository policy |
| --- | --- | --- |
| Lightricks LTX-2.3 | LTX-2 Community License Agreement | Do not commit weights. Users must review and accept the model terms themselves before download/use. |
| Google Gemma 3 text encoder | Gemma Terms of Use | Do not commit weights or tokens. Users must accept Google/Hugging Face access terms themselves. |
| DeepBeepMeep/LTX-2 repack | No explicit license shown on its Hugging Face page; base model points to Lightricks/LTX-2 | Treat as external model material. Do not redistribute in this repository. |

## Repository Policy

- VibeMotion source code is licensed under Apache-2.0.
- Local projects, generated videos, QA artifacts, style presets, Figma exports,
  model weights, tokens, and environment files must stay out of Git.
- `NOTICE.md` contains the short public-facing notice used on GitHub.
- If future work copies source code from a third-party project, add the license
  and attribution here before committing.
- If future work bundles binaries or model files, re-run the license review.
