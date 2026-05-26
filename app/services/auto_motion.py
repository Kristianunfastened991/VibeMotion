from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import EditPlan, MotionSpec, TranscriptData
from app.services.motion import PRESET_DEFAULTS, fit_motion_to_canvas, place_motion_on_quiet_area, render_motion_asset
from app.services.ollama import OllamaError, chat_json
from app.services.vision import analyze_video_with_vision


SYSTEM_PROMPT = """You are an assistant video editor.
Return JSON only.
Create motion graphics ideas for a short social video.
Use overlays sparingly: 1 to 3 elements total.
Prefer concise text, strong hooks, simple infographics, and clear callouts.
If vision_context is available, use safe zones and avoid covering faces or existing on-screen text.
Never output random placeholder words. Text must be meaningful and derived from transcript or vision_context.
Prefer compact overlays in lower-left or left-side empty areas, not large central banners.
Avoid the right side on landscape screen recordings because it often contains webcam/ad/sidebar content.
Allowed presets: soft-neumorphism only.
Return shape:
{"motions":[{"prompt":"string","preset":"soft-neumorphism","start":number,"duration":number}]}
"""


def _fallback_motions(transcript: TranscriptData, edit_plan: EditPlan) -> list[dict]:
    duration = max(1.0, edit_plan.estimated_duration or transcript.duration or 1.0)
    text = transcript.text.strip()
    if not text:
        return []
    hook = text[:70].strip()
    motions = [
        {
            "prompt": f"Add a small soft-neumorphic callout in a safe empty corner with this text: {hook}",
            "preset": "soft-neumorphism",
            "start": min(0.6, duration * 0.08),
            "duration": min(4.0, max(2.0, duration * 0.35)),
        }
    ]
    if duration >= 8:
        motions.append(
            {
                "prompt": "Add a compact soft-neumorphic left-side card that summarizes the problem in 3 short words",
                "preset": "soft-neumorphism",
                "start": min(duration - 2.5, duration * 0.45),
                "duration": min(4.0, max(2.0, duration * 0.28)),
            }
        )
    return motions


def _motions_from_vision(vision_context: dict, duration: float) -> list[dict]:
    ideas = vision_context.get("global_graphic_ideas") or []
    summary = str(vision_context.get("summary") or "").strip()
    text = ""
    if ideas:
        text = str(ideas[0]).strip()
    if not text:
        text = summary
    if not text:
        return []
    return [
        {
            "prompt": f"Add a compact soft-neumorphic insight card with this text: {text[:70]}",
            "preset": "soft-neumorphism",
            "start": min(0.6, duration * 0.08),
            "duration": min(4.0, max(2.0, duration * 0.35)),
        }
    ]


def generate_auto_motions(
    transcript: TranscriptData,
    edit_plan: EditPlan,
    project_root: Path,
    source_video: Path,
) -> list[MotionSpec]:
    duration = max(1.0, edit_plan.estimated_duration or transcript.duration or 1.0)
    vision_context: dict | None = None
    try:
        vision_context = analyze_video_with_vision(source_video, project_root)
    except Exception:
        vision_context = None

    has_transcript_text = bool(transcript.text.strip())
    if not has_transcript_text and not vision_context:
        return []
    if not has_transcript_text and vision_context:
        raw_motions = _motions_from_vision(vision_context, duration)
    else:
        raw_motions = []

    if not raw_motions:
        payload = {
            "duration": duration,
            "transcript": transcript.text[:4000],
            "cut_strategy": edit_plan.strategy,
            "motion_notes": edit_plan.motion_notes,
            "vision_context": vision_context,
            "rules": [
                "Do not cover the main subject face if possible.",
                "Use the vision_context safe_zones when available.",
                "Avoid covering existing text detected in vision_context.",
                "Keep overlays small: max 30% of frame width in landscape, max 85% in portrait.",
                "Prefer lower-left or left-middle for landscape streamer/screen recordings.",
                "Avoid right-side overlays unless vision_context explicitly marks it as empty.",
                "Use short text, not paragraphs.",
                "Use only the soft-neumorphism preset.",
                "Use neumorphic cards, pills, rows, sliders, or compact controls for every overlay.",
                "Do not use glass, neon, dark editorial, glitch, or bold-caption styling.",
            ],
        }
        try:
            response = chat_json(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), timeout=75)
            raw_motions = response.get("motions") or []
        except Exception:
            raw_motions = _fallback_motions(transcript, edit_plan)

    if not raw_motions:
        raw_motions = _fallback_motions(transcript, edit_plan)

    from app.services.media import detect_video_size

    canvas_width, canvas_height = detect_video_size(source_video)
    specs: list[MotionSpec] = []
    for item in raw_motions[:3]:
        preset = "soft-neumorphism"
        start = max(0.0, min(float(item.get("start", 0.5)), max(0.0, duration - 0.5)))
        motion_duration = max(1.0, min(float(item.get("duration", 3.0)), max(1.0, duration - start)))
        prompt = str(item.get("prompt") or "Add a stylish soft-neumorphic card")
        text = prompt.split("text:", 1)[1].strip() if "text:" in prompt else prompt
        defaults = PRESET_DEFAULTS.get(preset, PRESET_DEFAULTS["glass"])
        motion = MotionSpec(
            id=f"motion-auto-{len(specs) + 1}",
            kind=defaults["kind"],
            design_preset=preset,
            text=text[:90],
            start=start,
            duration=motion_duration,
            x=defaults["x"],
            y=defaults["y"],
            width=defaults["width"],
            height=defaults["height"],
            accent=defaults["accent"],
            animation=defaults["animation"],
            prompt=prompt,
        )
        motion = fit_motion_to_canvas(motion, canvas_width, canvas_height)
        motion = place_motion_on_quiet_area(motion, source_video, project_root / "assets")
        render_motion_asset(motion, project_root / "assets")
        specs.append(motion)
    return specs
