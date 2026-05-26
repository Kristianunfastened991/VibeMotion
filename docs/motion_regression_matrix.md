# Motion Regression Matrix

This matrix is implemented by `scripts/motion_autotest.py`. The runner may skip
GPU-heavy LTX generation when VRAM is not sufficient, but it must record the skip
reason and still test the fallback decision path.

| ID | Scenario | Evidence |
| --- | --- | --- |
| whole_frame_choreography | Main prompt: background-only intro, random fly-in build, exact hold, glass shatter fade-out. | Storyboard JSON, MP4, frames at 0.5/2.5/4.5/12.5/14.5, visual metrics. |
| selected_image_layer | Animate one image crop/mask layer. | MP4, inside/outside-region diff, duplicate-pixel check. |
| selected_text_layer | Animate one text layer. | MP4, outside-region stability, text hold fidelity. |
| selected_card_or_group | Animate one UI card/group-like shape. | MP4, outside-region stability, shape/card geometry check. |
| add_new_cancel_semantics | Replace resets stack, Add appends action, Cancel is no mutation. | Recipe/action JSON diff. |
| timeline_drag_resize_contract | Resize keeps absolute phase timing unless prompt asks to stretch. | Motion plan timing assertions. |
| render_parity | Motion asset and final preview match semantically at sample times. | Extracted final frames and parity metrics. |
| ltx_preview_apply_render | Source preview non-black, generated MP4 decodes, layer apply is scoped, render decodes. | LTX JSON, ffprobe output, VRAM decision, optional MP4. |
| ltx_quality_480_4_8 | 480px at 4s and 8s are accepted when VRAM allows. | Schema/fallback checks, optional render. |
| ltx_quality_720_1080_gate | 720/1080 only run when VRAM threshold allows. | VRAM threshold report. |

## Main Whole-Frame Prompt

```text
The first 2 seconds show only the clean background. Then over 3 seconds all visible elements fly into the frame in random order and settle smoothly. Hold the exact original frame. During the last 3 seconds the full frame shatters like glass and fades out.
```

Critical samples: `0.5s`, `2.5s`, `4.5s`, `12.5s`, `14.5s`.
