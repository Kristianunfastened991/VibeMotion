from __future__ import annotations

import json
import math
import re
import hashlib
import random
import subprocess
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageStat

from app.models.schemas import CutRange, MotionSpec, ProjectState, TranscriptData, WordToken
from app.services.media import detect_duration, detect_video_size
from app.services.vision import analyze_video_with_vision
from app.services.style_presets import SOFT_NEUMORPHISM_PRESET_ID, apply_style_to_motion, load_style_preset, soft_neumorphism_profile


AGENT_MOTION_PREFIX = "agent-motion-"
PHRASE_GAP_SECONDS = 0.5
AGENT_DIRECTOR_VERSION = "hyperframes-director-v3"
AGENT_STYLE_PRESET_IDS = {"soft-neumorphism", "frosted-glass", "warm-teal-ui"}


DIRECTOR_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "so",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
    "your",
    "\u0430",
    "\u0432",
    "\u0438",
    "\u0438\u043b\u0438",
    "\u043a",
    "\u043a\u0430\u043a",
    "\u043d\u0430",
    "\u043d\u043e",
    "\u043f\u043e",
    "\u0441",
    "\u044d\u0442\u043e",
    "\u044f",
}


STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "Clean Premium": {
        "background": "rgba(255, 255, 255, 0.24)",
        "accent": "#111111",
        "font_family": "Inter",
        "font_weight": 600,
        "motion_energy": "calm",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    "Cinematic": {
        "background": "rgba(0, 0, 0, 0.32)",
        "accent": "#f5f5f0",
        "font_family": "Inter",
        "font_weight": 500,
        "motion_energy": "calm",
        "enter_animation": "fade",
        "exit_animation": "fade",
        "easing": "sine",
    },
    "Editorial": {
        "background": "rgba(255, 255, 255, 0.28)",
        "accent": "#1a1a1a",
        "font_family": "Manrope",
        "font_weight": 600,
        "motion_energy": "measured",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    "Gaming/Energy": {
        "background": "rgba(0, 0, 0, 0.28)",
        "accent": "#38bdf8",
        "font_family": "Inter",
        "font_weight": 700,
        "motion_energy": "fast",
        "enter_animation": "pop",
        "exit_animation": "fade",
        "easing": "power",
    },
    "Minimal Apple-like": {
        "background": "rgba(255, 255, 255, 0.20)",
        "accent": "#0f172a",
        "font_family": "Inter",
        "font_weight": 500,
        "motion_energy": "minimal",
        "enter_animation": "fade",
        "exit_animation": "fade",
        "easing": "sine",
    },
}


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "so",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
    "your",
    "а",
    "в",
    "и",
    "как",
    "на",
    "но",
    "по",
    "с",
    "то",
    "что",
    "это",
    "я",
}


def agent_edit_dir(project_root: Path) -> Path:
    path = project_root / "edit"
    path.mkdir(parents=True, exist_ok=True)
    (path / "verify").mkdir(parents=True, exist_ok=True)
    return path


def _stamp(seconds: float) -> str:
    return f"{max(0.0, seconds):06.2f}"


def _parse_css_rgb(value: str | None) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("#"):
        raw = text.lstrip("#")
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) >= 6:
            try:
                return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
            except ValueError:
                return None
    match = re.search(r"rgba?\(([^)]+)\)", text)
    if not match:
        return None
    parts = [part.strip() for part in match.group(1).split(",")[:3]]
    if len(parts) < 3:
        return None
    try:
        return tuple(max(0, min(255, int(float(part)))) for part in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def _rgb_to_hex(rgb: tuple[int, int, int] | None, fallback: str = "#111111") -> str:
    if rgb is None:
        return fallback
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _luma(rgb: tuple[int, int, int] | None) -> float:
    if rgb is None:
        return 255.0
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722


def _dominant(counter: Counter[str], fallback: str) -> str:
    if not counter:
        return fallback
    return counter.most_common(1)[0][0]


def build_style_profile(state: ProjectState, project_root: Path) -> dict[str, Any]:
    """Create a lightweight project style memory from Figma and accepted motion blocks."""
    return load_style_preset(getattr(state, "style_preset_id", None)) or soft_neumorphism_profile()
    fills: Counter[str] = Counter()
    text_colors: Counter[str] = Counter()
    accents: Counter[str] = Counter()
    fonts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []

    for motion in state.motions:
        is_agent_generated = motion.id.startswith(AGENT_MOTION_PREFIX)
        if motion.background and not is_agent_generated:
            fills[motion.background] += 1
        if motion.accent and not is_agent_generated:
            accents[motion.accent] += 1
        if motion.text:
            examples.append(
                {
                    "id": motion.id,
                    "source_type": motion.source_type,
                    "text": motion.text[:80],
                    "preset": motion.design_preset,
                }
            )
        for layer in list(motion.figma_layers or []):
            if not isinstance(layer, dict):
                continue
            fill = str(layer.get("fill") or "").strip()
            color = str(layer.get("color") or "").strip()
            font = str(layer.get("font_family") or "").strip()
            if fill:
                fills[fill] += 1
            if color:
                text_colors[color] += 1
            if font:
                fonts[font] += 1

    background_rgb = _parse_css_rgb(_dominant(fills, "rgba(255, 255, 255, 1)"))
    text_rgb = _parse_css_rgb(_dominant(text_colors, "#111111"))
    accent = _dominant(accents, _rgb_to_hex(text_rgb, "#111111"))
    font_family = _dominant(fonts, "Inter")
    light_canvas = _luma(background_rgb) >= 170
    dark_canvas = _luma(background_rgb) <= 80
    has_editorial_font = any(name.casefold() in {"manrope", "georgia", "times new roman", "playfair display"} for name in fonts)

    if dark_canvas:
        style_name = "Cinematic"
    elif has_editorial_font:
        style_name = "Editorial"
    elif light_canvas and _luma(text_rgb) < 80:
        style_name = "Clean Premium"
    else:
        style_name = "Minimal Apple-like"

    preset = dict(STYLE_PRESETS[style_name])
    preset["font_family"] = font_family or preset["font_family"]
    preset["accent"] = accent or preset["accent"]
    if dark_canvas:
        preset["background"] = "rgba(0, 0, 0, 0.28)"
    elif light_canvas:
        preset["background"] = "rgba(255, 255, 255, 0.24)"
    accent_rgb = _parse_css_rgb(str(preset.get("accent") or ""))
    if light_canvas and _luma(accent_rgb) > 210:
        preset["accent"] = _rgb_to_hex(text_rgb, "#111111")
    if dark_canvas and _luma(accent_rgb) < 60:
        preset["accent"] = "#ffffff"

    profile = {
        "version": 1,
        "source": "figma-and-motion-memory",
        "style_name": style_name,
        "tokens": {
            "background_color": _rgb_to_hex(background_rgb, "#ffffff"),
            "foreground_color": _rgb_to_hex(text_rgb, "#111111"),
            "accent": preset["accent"],
            "overlay_background": preset["background"],
            "font_family": preset["font_family"],
            "font_weight": preset["font_weight"],
            "motion_energy": preset["motion_energy"],
            "enter_animation": preset["enter_animation"],
            "exit_animation": preset["exit_animation"],
            "easing": preset["easing"],
        },
        "rules": [
            "Use project style tokens before inventing colors.",
            "Keep generated text accents readable and away from faces when possible.",
            "Do not use neon/glitch/orb defaults unless the selected style explicitly asks for them.",
            "Apply subtitles after overlays.",
            "Preserve Figma and LTX layers as independent sources of truth.",
        ],
        "examples": examples[:12],
    }
    return profile


def write_style_artifacts(state: ProjectState, project_root: Path, edit_dir: Path) -> dict[str, str]:
    profile = build_style_profile(state, project_root)
    profile_path = edit_dir / "style_profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    tokens = profile.get("tokens", {})
    design_lines = [
        "# DESIGN",
        "",
        "## Style Memory",
        "",
        f"Name: {profile.get('style_name', 'Clean Premium')}",
        f"Source: {profile.get('source', 'project-memory')}",
        "",
        "## Colors",
        "",
        f"- Background: {tokens.get('background_color', '#ffffff')}",
        f"- Foreground: {tokens.get('foreground_color', '#111111')}",
        f"- Accent: {tokens.get('accent', '#111111')}",
        f"- Overlay background: {tokens.get('overlay_background', 'rgba(255, 255, 255, 0.24)')}",
        "",
        "## Typography",
        "",
        f"- Family: {tokens.get('font_family', 'Inter')}",
        f"- Weight: {tokens.get('font_weight', 600)}",
        "",
        "## Motion",
        "",
        f"- Energy: {tokens.get('motion_energy', 'calm')}",
        f"- Enter: {tokens.get('enter_animation', 'rise')}",
        f"- Exit: {tokens.get('exit_animation', 'fade')}",
        f"- Easing: {tokens.get('easing', 'sine')}",
        "",
        "## Rules",
        "",
    ]
    design_lines.extend(f"- {rule}" for rule in profile.get("rules", []))
    design_path = edit_dir / "DESIGN.md"
    design_path.write_text("\n".join(design_lines) + "\n", encoding="utf-8")
    return {
        "style_profile": str(profile_path.relative_to(project_root)),
        "design_md": str(design_path.relative_to(project_root)),
    }


def _word_dict(word: WordToken, *, output_start: float | None = None, output_end: float | None = None) -> dict[str, Any]:
    payload = word.model_dump(mode="json")
    if output_start is not None:
        payload["output_start"] = round(output_start, 3)
    if output_end is not None:
        payload["output_end"] = round(output_end, 3)
    return payload


def _phrases_from_word_dicts(words: list[dict[str, Any]], *, start_key: str = "start", end_key: str = "end") -> list[dict[str, Any]]:
    phrases: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = " ".join(str(item.get("text") or "").strip() for item in current).strip()
        if text:
            phrases.append(
                {
                    "start": round(float(current[0].get(start_key) or 0.0), 3),
                    "end": round(float(current[-1].get(end_key) or current[0].get(start_key) or 0.0), 3),
                    "text": re.sub(r"\s+", " ", text),
                    "word_count": len(current),
                }
            )
        current = []

    previous_end: float | None = None
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        start = float(word.get(start_key) or 0.0)
        if previous_end is not None and start - previous_end >= PHRASE_GAP_SECONDS:
            flush()
        current.append(word)
        previous_end = float(word.get(end_key) or start)
        if text.endswith((".", "!", "?", ":", ";")) or len(current) >= 16:
            flush()
    flush()
    return phrases


def output_timeline_words(transcript: TranscriptData | None, keep_ranges: list[CutRange]) -> list[dict[str, Any]]:
    if transcript is None or not transcript.words:
        return []
    mapped: list[dict[str, Any]] = []
    timeline_offset = 0.0
    for keep_range in keep_ranges:
        start = float(keep_range.start)
        end = float(keep_range.end)
        for word in transcript.words:
            if word.end <= start or word.start >= end:
                continue
            output_start = timeline_offset + max(0.0, word.start - start)
            output_end = timeline_offset + min(end - start, max(0.0, word.end - start))
            if output_end <= output_start:
                output_end = output_start + max(0.05, word.end - word.start)
            mapped.append(_word_dict(word, output_start=output_start, output_end=output_end))
        timeline_offset += max(0.0, end - start)
    return mapped


def build_packed_transcript(transcript: TranscriptData | None, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if transcript is None or not transcript.words:
        output.write_text("# Packed transcript\n\nNo word-level transcript is available yet.\n", encoding="utf-8")
        return output
    words = [_word_dict(word) for word in transcript.words]
    phrases = _phrases_from_word_dicts(words)
    lines = [
        "# Packed transcript",
        "",
        f"## source  (duration: {float(transcript.duration or 0.0):.1f}s, {len(phrases)} phrases)",
    ]
    for phrase in phrases:
        lines.append(f"  [{_stamp(phrase['start'])}-{_stamp(phrase['end'])}] S0 {phrase['text']}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def build_edl(state: ProjectState, project_root: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    clips: list[dict[str, Any]] = []
    cursor = 0.0
    keep_ranges = state.edit_plan.keep_ranges if state.edit_plan else []
    for index, item in enumerate(keep_ranges, start=1):
        duration = max(0.0, float(item.end) - float(item.start))
        clips.append(
            {
                "id": f"b{index:02d}",
                "source": state.source_video,
                "source_start": round(float(item.start), 3),
                "source_end": round(float(item.end), 3),
                "output_start": round(cursor, 3),
                "output_end": round(cursor + duration, 3),
                "reason": item.reason,
            }
        )
        cursor += duration
    payload = {
        "version": 1,
        "project_id": state.project_id,
        "source_video": state.source_video,
        "expected_duration": round(cursor or float(state.transcript.duration or 0.0) if state.transcript else cursor, 3),
        "clips": clips,
        "motions": [
            {
                "id": motion.id,
                "source_type": motion.source_type,
                "text": motion.text,
                "start": round(float(motion.start), 3),
                "duration": round(float(motion.duration), 3),
                "design_preset": motion.design_preset,
            }
            for motion in state.motions
        ],
        "subtitle_style": state.edit_plan.subtitle_style if state.edit_plan else "none",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _clean_label_words(text: str) -> list[str]:
    tokens = re.findall(r"[\wА-Яа-яЁё-]+", text, flags=re.UNICODE)
    clean = [token.strip("-_") for token in tokens if token.strip("-_")]
    content = [token for token in clean if token.casefold() not in STOPWORDS and len(token) > 1]
    return content or clean


def _motion_label(text: str) -> str:
    words = _clean_label_words(text)
    if not words:
        return "Key moment"
    label = " ".join(words[:5])
    return label[:64].strip() or "Key moment"


def _agent_director_rng(state: ProjectState, prompt: str, variant_seed: str | None) -> random.Random:
    seed = str(variant_seed or "default").strip() or "default"
    raw = f"{AGENT_DIRECTOR_VERSION}|{state.project_id}|{prompt.strip()}|{seed}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _prompt_tokens(text: str, limit: int = 18) -> list[str]:
    tokens = re.findall(r"[\w\u0400-\u04ff-]+", str(text or ""), flags=re.UNICODE)
    clean: list[str] = []
    for token in tokens:
        value = token.strip("-_").casefold()
        if len(value) <= 2 or value in DIRECTOR_STOPWORDS:
            continue
        if value not in clean:
            clean.append(value)
        if len(clean) >= limit:
            break
    return clean


def _director_label(text: str, *, fallback: str = "Key moment", max_words: int = 4) -> str:
    tokens = re.findall(r"[\w\u0400-\u04ff-]+", str(text or ""), flags=re.UNICODE)
    words: list[str] = []
    for token in tokens:
        value = token.strip("-_")
        if not value or value.casefold() in DIRECTOR_STOPWORDS:
            continue
        words.append(value)
        if len(words) >= max_words:
            break
    label = " ".join(words).strip() or fallback
    return label[:42].upper()


def _quoted_prompt_labels(prompt: str) -> list[str]:
    labels = []
    for match in re.findall(r"[\"'\u00ab\u201c](.{2,48}?)[\"'\u00bb\u201d]", str(prompt or "")):
        label = re.sub(r"\s+", " ", match).strip()
        if label:
            labels.append(label[:48])
    return labels[:8]


def _director_wants_gesture(prompt: str) -> bool:
    return _contains_any(
        prompt,
        [
            "point",
            "finger",
            "hand",
            "gesture",
            "where i point",
            "\u043f\u0430\u043b\u0435\u0446",
            "\u0440\u0443\u043a",
            "\u0436\u0435\u0441\u0442",
            "\u043f\u043e\u043a\u0430\u0437",
            "\u043a\u0443\u0434\u0430",
            "\u0441\u044e\u0434\u0430",
        ],
    )


def _director_wants_callouts(prompt: str) -> bool:
    return _contains_any(
        prompt,
        [
            "callout",
            "card",
            "plate",
            "label",
            "badge",
            "corner",
            "\u043f\u043b\u0430\u0448",
            "\u043d\u0430\u0434\u043f\u0438\u0441",
            "\u043b\u0435\u0439\u0431\u043b",
            "\u043a\u0430\u0440\u0442\u043e\u0447",
            "\u0430\u043a\u0446\u0435\u043d\u0442",
        ],
    )


def _director_is_generic_creative_motion_prompt(prompt: str) -> bool:
    text = str(prompt or "").casefold()
    if not text.strip() or _quoted_prompt_labels(prompt):
        return False
    has_motion = _contains_any(
        text,
        [
            "motion",
            "motion graphics",
            "animation",
            "animate",
            "\u043c\u043e\u0443\u0448\u043d",
            "\u0430\u043d\u0438\u043c\u0430\u0446",
        ],
    )
    has_creative_quality = _contains_any(
        text,
        [
            "beautiful",
            "nice",
            "premium",
            "stylish",
            "cinematic",
            "dynamic",
            "clean",
            "make it pop",
            "\u043a\u0440\u0430\u0441\u0438\u0432",
            "\u0441\u0442\u0438\u043b",
            "\u0434\u0438\u043d\u0430\u043c",
            "\u043a\u0440\u0443\u0442",
            "\u043f\u0440\u0435\u043c",
            "\u044d\u0444\u0444\u0435\u043a\u0442",
            "\u0441\u0434\u0435\u043b\u0430\u0439",
        ],
    )
    has_specific_deliverable = _contains_any(
        text,
        [
            "subscribe",
            "caption",
            "subtitle",
            "lower third",
            "callout",
            "badge",
            "label",
            "figma",
            "ltx",
            "render only",
            "\u043f\u043e\u0434\u043f\u0438\u0441",
            "\u0441\u0443\u0431\u0442\u0438\u0442\u0440",
            "\u043d\u0438\u0436\u043d\u044f\u044f \u0442\u0440\u0435\u0442\u044c",
            "\u043f\u043b\u0430\u0448",
            "\u043b\u0435\u0439\u0431\u043b",
            "\u0444\u0438\u0433\u043c",
            "\u0444\u043e\u0442\u043e",
        ],
    )
    token_count = len(_prompt_tokens(text))
    return has_motion and has_creative_quality and not has_specific_deliverable and token_count <= 12


def _director_wants_opening(prompt: str) -> bool:
    if _director_is_generic_creative_motion_prompt(prompt):
        return True
    return _contains_any(
        prompt,
        [
            "intro",
            "opening",
            "hook",
            "title",
            "first seconds",
            "\u0438\u043d\u0442\u0440\u043e",
            "\u0432 \u043d\u0430\u0447\u0430\u043b",
            "\u043f\u0435\u0440\u0432\u044b\u0435 \u0441\u0435\u043a",
            "\u0437\u0430\u0433\u043e\u043b\u043e\u0432",
            "\u0445\u0443\u043a",
        ],
    )


def _stringify_context(value: Any, *, limit: int = 3000) -> str:
    if value is None:
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:limit]


def _nearest_vision_frame_text(vision_context: dict[str, Any] | None, timestamp: float) -> str:
    if not isinstance(vision_context, dict):
        return ""
    frames = [frame for frame in vision_context.get("frames", []) if isinstance(frame, dict)]
    if not frames:
        return _stringify_context(vision_context, limit=1200)
    nearest = min(frames, key=lambda frame: abs(float(frame.get("time") or 0.0) - timestamp))
    return _stringify_context(nearest, limit=1600)


def _contains_direction(text: str, values: list[str]) -> bool:
    return _contains_any(str(text or ""), values)


def _direction_from_text(text: str, index: int = 0) -> str | None:
    lowered = str(text or "").casefold()
    if _contains_direction(lowered, ["left to right", "\u0441\u043b\u0435\u0432\u0430 \u043d\u0430\u043f\u0440\u0430\u0432", "\u0441\u043b\u0435\u0432\u043e \u043d\u0430\u043f\u0440\u0430\u0432"]):
        horizontal = "right"
    elif _contains_direction(lowered, ["right to left", "\u0441\u043f\u0440\u0430\u0432\u0430 \u043d\u0430\u043b\u0435\u0432", "\u0441\u043f\u0440\u0430\u0432\u043e \u043d\u0430\u043b\u0435\u0432"]):
        horizontal = "left"
    elif _contains_direction(lowered, ["right", "corner right", "\u0441\u043f\u0440\u0430\u0432", "\u043f\u0440\u0430\u0432"]):
        horizontal = "right"
    elif _contains_direction(lowered, ["left", "corner left", "\u0441\u043b\u0435\u0432", "\u043b\u0435\u0432"]):
        horizontal = "left"
    else:
        horizontal = "right" if index % 2 else "left"

    if _contains_direction(lowered, ["top to bottom", "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437", "\u0441\u0432\u0435\u0440\u0445 \u0432\u043d\u0438\u0437"]):
        vertical = "bottom"
    elif _contains_direction(lowered, ["bottom to top", "\u0441\u043d\u0438\u0437\u0443 \u0432\u0432\u0435\u0440\u0445", "\u0441\u043d\u0438\u0437 \u0432\u0432\u0435\u0440\u0445"]):
        vertical = "top"
    elif _contains_direction(lowered, ["top", "above", "upper", "\u0441\u0432\u0435\u0440\u0445", "\u0432\u0435\u0440\u0445", "\u0443\u0433\u043e\u043b"]):
        vertical = "top"
    elif _contains_direction(lowered, ["bottom", "below", "lower", "\u0441\u043d\u0438\u0437", "\u0432\u043d\u0438\u0437", "\u043d\u0438\u0437"]):
        vertical = "bottom"
    else:
        vertical = None

    has_explicit = any(
        _contains_direction(lowered, values)
        for values in (
            ["right", "left", "corner", "\u0441\u043f\u0440\u0430\u0432", "\u0441\u043b\u0435\u0432", "\u0443\u0433\u043e\u043b", "\u043f\u0440\u0430\u0432", "\u043b\u0435\u0432"],
            ["top", "bottom", "above", "below", "\u0441\u0432\u0435\u0440\u0445", "\u0441\u043d\u0438\u0437", "\u0432\u0435\u0440\u0445", "\u0432\u043d\u0438\u0437"],
        )
    )
    if not has_explicit:
        return None
    if vertical:
        return f"{vertical}-{horizontal}"
    return horizontal


def _direction_words(direction: str | None) -> str:
    return {
        "top-left": "top left",
        "top-right": "top right",
        "bottom-left": "bottom left",
        "bottom-right": "bottom right",
        "left": "left",
        "right": "right",
        "top": "top",
        "bottom": "bottom",
    }.get(str(direction or ""), "")


def _clamp_position(position: dict[str, int], canvas_width: int, canvas_height: int) -> dict[str, int]:
    width = max(120, min(int(position.get("width") or 360), canvas_width))
    height = max(64, min(int(position.get("height") or 120), canvas_height))
    margin = max(16, int(min(canvas_width, canvas_height) * 0.035))
    return {
        "x": max(margin, min(canvas_width - width - margin, int(position.get("x") or margin))),
        "y": max(margin, min(canvas_height - height - margin, int(position.get("y") or margin))),
        "width": width,
        "height": height,
    }


def _seeded_position(
    prompt: str,
    phrase_text: str,
    intent: str,
    component: str,
    direction: str | None,
    index: int,
    canvas_width: int,
    canvas_height: int,
    rng: random.Random,
) -> dict[str, int]:
    if _director_is_generic_creative_motion_prompt(prompt):
        layouts = [
            (0.055, 0.10, 0.46, 0.24),
            (0.57, 0.14, 0.35, 0.15),
            (0.065, 0.62, 0.38, 0.17),
            (0.57, 0.46, 0.35, 0.30),
            (0.075, 0.34, 0.32, 0.14),
            (0.50, 0.68, 0.42, 0.16),
            (0.27, 0.78, 0.46, 0.14),
        ]
        x, y, width, height = layouts[index % len(layouts)]
        position = {
            "x": int(canvas_width * x),
            "y": int(canvas_height * y),
            "width": int(canvas_width * width),
            "height": int(canvas_height * height),
        }
        if component in {"rows", "card"}:
            position["height"] = max(position["height"], int(canvas_height * 0.26))
        elif component == "hero":
            position["height"] = max(position["height"], int(canvas_height * 0.23))
        elif component in {"callout", "check"}:
            position["height"] = min(position["height"], int(canvas_height * 0.16))
        return _clamp_position(position, canvas_width, canvas_height)

    position_text = " ".join(
        part
        for part in ([phrase_text, _direction_words(direction)] if component == "callout" else [prompt, phrase_text, _direction_words(direction)])
        if part
    )
    position = _beat_position(position_text, intent, index, canvas_width, canvas_height)
    if component == "callout":
        position["width"] = int(min(520, canvas_width * 0.38))
        position["height"] = int(min(138, max(86, canvas_height * 0.13)))
    elif component == "text":
        position["width"] = int(min(560, canvas_width * 0.42))
        position["height"] = int(min(180, max(112, canvas_height * 0.18)))
    if component != "slider":
        jitter_x = int(canvas_width * 0.028 * (rng.random() - 0.5))
        jitter_y = int(canvas_height * 0.026 * (rng.random() - 0.5))
        position["x"] = int(position["x"] + jitter_x)
        position["y"] = int(position["y"] + jitter_y)
    return _clamp_position(position, canvas_width, canvas_height)


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text))


def _contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.casefold()
    return any(needle.casefold() in lowered for needle in needles)


def _beat_intent(text: str, index: int) -> str:
    if _contains_any(text, ["mistake", "cut", "trim", "pause", "filler", "ошиб", "выреж", "обреж", "пауза", "паразит"]):
        return "cut"
    if _contains_any(text, ["card", "label", "callout", "badge", "lower third", "плаш", "лейбл", "надпис", "текст", "угол", "сюда", "тут"]):
        return "callout"
    if _contains_any(text, ["figma", "фигм", "frame", "фрейм", "layer", "слой"]):
        return "figma"
    if _contains_any(text, ["ltx", "image", "photo", "картин", "фото"]):
        return "ltx"
    if _contains_any(text, ["motion", "graphic", "animation", "animate", "моушн", "график", "анимац"]):
        return "motion"
    if _contains_any(text, ["edit", "editing", "pipeline", "raw", "монтаж", "редакт", "исходник", "план"]):
        return "workflow"
    if _contains_any(text, ["result", "final", "preview", "render", "download", "итог", "результ", "рендер", "скач"]):
        return "result"
    return "hook" if index == 0 else "emphasis"


def _beat_copy(intent: str, phrase_text: str) -> tuple[str, str]:
    ru = _has_cyrillic(phrase_text)
    if ru:
        return {
            "hook": ("THE EDIT LIVE", "agent planned"),
            "cut": ("MISTAKES WILL BE CUT", "trim pass"),
            "callout": ("SMART CALLOUTS", "smart placement"),
            "figma": ("FIGMA LAYERS", "design frame"),
            "ltx": ("LTX IMAGE MOTION", "photo/video layer"),
            "motion": ("MOTION GRAPHICS", "animated beats"),
            "workflow": ("AI EDIT PLAN", "timeline logic"),
            "result": ("FINAL RENDER", "final pass"),
            "emphasis": ("KEY MOMENT", "key moment"),
        }.get(intent, ("KEY MOMENT", "key moment"))
    return {
        "hook": ("THE EDIT LIVE", "agent planned"),
        "cut": ("MISTAKES WILL BE CUT", "trim pass"),
        "callout": ("SMART CALLOUTS", "context aware"),
        "figma": ("FIGMA LAYERS", "design frame"),
        "ltx": ("LTX IMAGE MOTION", "photo/video layer"),
        "motion": ("MOTION GRAPHICS", "animated beats"),
        "workflow": ("AI EDIT PLAN", "timeline logic"),
        "result": ("FINAL RENDER", "ready to export"),
        "emphasis": (_motion_label(phrase_text).upper(), "key moment"),
    }.get(intent, (_motion_label(phrase_text).upper(), "key moment"))


def _soft_component_for_intent(intent: str, text: str, index: int = 0) -> str:
    if _contains_any(text, ["volume", "sound", "loud", "increase", "boost", "progress", "loading", "level", "meter", "громк", "звук", "прибав", "увелич", "прогресс", "загруз", "уров"]):
        return "slider"
    if _contains_any(text, ["toggle", "switch", "on off", "turn on", "turn off", "enable", "disable", "переключ", "тумблер", "включ", "выключ"]):
        return "toggle"
    if _contains_any(text, ["done", "complete", "ready", "success", "approved", "check", "готов", "заверш", "успеш", "галоч"]):
        return "check"
    if _contains_any(text, ["table", "row", "list", "steps", "items", "spacing", "план", "таблиц", "спис", "строк", "пункт", "шаг"]):
        return "rows"
    if _contains_any(text, ["card", "label", "callout", "badge", "lower third", "plate", "corner", "point", "here", "this spot", "плашк", "лейбл", "угол", "сюда", "тут", "показыв"]):
        return "callout"
    if _contains_any(text, ["hyperframes", "remotion", "code", "html", "api"]):
        return "card"
    if intent == "hook" or index == 0:
        return "hero"
    return {
        "cut": "slider",
        "callout": "callout",
        "workflow": "rows",
        "figma": "card",
        "ltx": "card",
        "result": "check",
    }.get(intent, "text")


def _beat_position(text: str, intent: str, index: int, canvas_width: int, canvas_height: int) -> dict[str, int]:
    lowered = text.casefold()
    is_right = _contains_any(lowered, ["right", "справа", "правый", "право"])
    is_left = _contains_any(lowered, ["left", "слева", "левый", "лево"])
    is_top = _contains_any(lowered, ["top", "above", "верх", "сверху", "вверху"])
    is_bottom = _contains_any(lowered, ["bottom", "below", "низ", "снизу", "внизу"])
    component = _soft_component_for_intent(intent, text, index)

    if component == "slider":
        width = int(canvas_width * 0.76)
        height = int(max(88, min(124, canvas_height * 0.15)))
        return {
            "x": int((canvas_width - width) / 2),
            "y": int(canvas_height - height - canvas_height * 0.11),
            "width": width,
            "height": height,
        }
    if component == "hero":
        width = int(min(680, canvas_width * 0.48))
        height = int(min(280, max(190, canvas_height * 0.29)))
        return {
            "x": int(canvas_width * 0.055),
            "y": int(canvas_height * 0.12),
            "width": width,
            "height": height,
        }
    if component in {"rows", "card"}:
        width = int(min(540, canvas_width * 0.38))
        height = int(min(310, max(210, canvas_height * 0.34)))
        x = int(canvas_width - width - canvas_width * 0.055) if is_right or (not is_left and index % 2 == 1) else int(canvas_width * 0.055)
        y = int(canvas_height * 0.14 if not is_bottom else canvas_height - height - canvas_height * 0.13)
        return {"x": x, "y": y, "width": width, "height": height}
    if component == "callout":
        width = int(min(520, canvas_width * 0.38))
        height = int(min(138, max(86, canvas_height * 0.13)))
        x = int(canvas_width - width - canvas_width * 0.08) if is_right or (not is_left and index % 2 == 0) else int(canvas_width * 0.08)
        if is_top:
            y = int(canvas_height * 0.12)
        elif is_bottom:
            y = int(canvas_height - height - canvas_height * 0.15)
        else:
            y = int(canvas_height * (0.16 if index % 2 == 0 else 0.62))
        return {"x": x, "y": y, "width": width, "height": height}

    width = int(min(560, canvas_width * 0.42))
    height = int(min(180, max(112, canvas_height * 0.18)))
    x = int(canvas_width - width - canvas_width * 0.06) if is_right else int(canvas_width * 0.06)
    if is_left:
        x = int(canvas_width * 0.06)
    if is_top:
        y = int(canvas_height * 0.10)
    elif is_bottom:
        y = int(canvas_height - height - canvas_height * 0.13)
    else:
        y = int(canvas_height * (0.16 if index % 2 == 0 else 0.60))
    return {"x": x, "y": y, "width": width, "height": height}


def _beat_visual(intent: str, index: int, text: str = "") -> dict[str, Any]:
    soft_component = _soft_component_for_intent(intent, text, index)
    if intent == "cut":
        return {
            "design_preset": SOFT_NEUMORPHISM_PRESET_ID,
            "soft_component": soft_component,
            "enter_animation": "rise",
            "exit_animation": "fade",
            "enter_from": "bottom",
            "exit_to": "center",
            "enter_duration": 0.38,
            "exit_duration": 0.38,
            "text_scale": 0.95,
        }
    if intent in {"workflow", "figma", "ltx"}:
        return {
            "design_preset": SOFT_NEUMORPHISM_PRESET_ID,
            "soft_component": soft_component,
            "enter_animation": "slide",
            "exit_animation": "fade",
            "enter_from": "right" if index % 2 else "left",
            "exit_to": "center",
            "enter_duration": 0.5,
            "exit_duration": 0.42,
            "text_scale": 0.9,
        }
    if intent == "hook":
        return {
            "design_preset": SOFT_NEUMORPHISM_PRESET_ID,
            "soft_component": soft_component,
            "enter_animation": "pop",
            "exit_animation": "fade",
            "enter_from": "left",
            "exit_to": "center",
            "enter_duration": 0.55,
            "exit_duration": 0.45,
            "text_scale": 1.0,
        }
    if intent == "motion":
        return {
            "design_preset": SOFT_NEUMORPHISM_PRESET_ID,
            "soft_component": soft_component,
            "enter_animation": "rise",
            "exit_animation": "fade",
            "enter_from": "bottom",
            "exit_to": "center",
            "enter_duration": 0.44,
            "exit_duration": 0.36,
            "text_scale": 0.92,
        }
    return {
        "design_preset": SOFT_NEUMORPHISM_PRESET_ID,
        "soft_component": soft_component,
        "enter_animation": "rise",
        "exit_animation": "fade",
        "enter_from": "bottom",
        "exit_to": "center",
        "enter_duration": 0.42,
        "exit_duration": 0.34,
        "text_scale": 0.9,
    }


def _project_canvas_size(state: ProjectState, project_root: Path) -> tuple[int, int]:
    source = project_root / state.source_video if state.source_video else None
    if source and source.exists():
        try:
            return detect_video_size(source)
        except Exception:
            pass
    return 1280, 720


def enrich_agent_motion_slots(state: ProjectState, project_root: Path, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canvas_width, canvas_height = _project_canvas_size(state, project_root)
    enriched: list[dict[str, Any]] = []
    for index, slot in enumerate(slots):
        clean = dict(slot)
        if not clean.get("soft_component"):
            clean["soft_component"] = _soft_component_for_intent(
                str(clean.get("intent") or ""),
                str(clean.get("quote") or clean.get("anchor_words") or clean.get("label") or ""),
                index,
            )
        if not isinstance(clean.get("position"), dict):
            clean["position"] = _beat_position(
                str(clean.get("quote") or clean.get("anchor_words") or ""),
                str(clean.get("intent") or ""),
                index,
                canvas_width,
                canvas_height,
            )
        enriched.append(clean)
    return enriched


def _slot_frame_safe_zones(vision_context: dict[str, Any] | None, start: float) -> list[str]:
    if not isinstance(vision_context, dict):
        return []
    frames = [frame for frame in vision_context.get("frames", []) if isinstance(frame, dict)]
    if not frames:
        return []
    nearest = min(frames, key=lambda frame: abs(float(frame.get("time") or 0.0) - start))
    zones = nearest.get("safe_zones")
    if not isinstance(zones, list):
        return []
    return [str(zone).strip().casefold() for zone in zones if str(zone or "").strip()]


def _position_in_safe_zone(position: dict[str, Any], zone: str, canvas_width: int, canvas_height: int) -> dict[str, int]:
    width = max(120, int(position.get("width") or 360))
    height = max(64, int(position.get("height") or 120))
    margin = max(18, int(min(canvas_width, canvas_height) * 0.055))
    safe_top = max(margin, int(canvas_height * 0.10))
    zone = zone.replace("_", "-")
    if zone in {"top-right", "right-top", "center-right", "right"}:
        x = canvas_width - width - margin
    else:
        x = margin
    if zone in {"bottom-left", "bottom-right", "bottom", "lower-third"}:
        y = canvas_height - height - max(margin, int(canvas_height * 0.12))
    elif zone in {"center-left", "center-right"}:
        y = int((canvas_height - height) / 2)
    else:
        y = safe_top
    return {
        "x": max(margin, min(canvas_width - width - margin, int(x))),
        "y": max(margin, min(canvas_height - height - margin, int(y))),
        "width": width,
        "height": height,
    }


def apply_vision_safe_zones_to_slots(
    slots: list[dict[str, Any]],
    vision_context: dict[str, Any] | None,
    canvas_width: int,
    canvas_height: int,
) -> list[dict[str, Any]]:
    if not isinstance(vision_context, dict):
        return slots
    adjusted: list[dict[str, Any]] = []
    for slot in slots:
        clean = dict(slot)
        zones = _slot_frame_safe_zones(vision_context, float(clean.get("start") or 0.0))
        if zones and isinstance(clean.get("position"), dict) and not clean.get("direction"):
            clean["position"] = _position_in_safe_zone(dict(clean["position"]), zones[0], canvas_width, canvas_height)
            clean["vision_safe_zone"] = zones[0]
        adjusted.append(clean)
    return adjusted


def _motion_phrase_score(phrase: dict[str, Any], index: int) -> float:
    text = str(phrase.get("text") or "")
    label_words = _clean_label_words(text)
    phrase_duration = max(0.0, float(phrase.get("end") or 0) - float(phrase.get("start") or 0))
    intent = _beat_intent(text, index)
    intent_bonus = {
        "cut": 7.0,
        "callout": 7.0,
        "motion": 6.0,
        "workflow": 5.2,
        "figma": 4.6,
        "ltx": 4.6,
        "result": 4.4,
        "hook": 3.8,
    }.get(intent, 2.0)
    return intent_bonus + min(8, len(label_words)) * 1.15 + min(3.0, phrase_duration)


def propose_agent_motion_slots(state: ProjectState, max_slots: int = 5) -> list[dict[str, Any]]:
    if state.transcript is None or state.edit_plan is None:
        return []
    words = output_timeline_words(state.transcript, state.edit_plan.keep_ranges)
    phrases = _phrases_from_word_dicts(words, start_key="output_start", end_key="output_end")
    if not phrases:
        return []
    duration = max(0.0, float(state.edit_plan.estimated_duration or 0.0))

    selected: list[dict[str, Any]] = []
    max_slots = max(1, min(8, int(max_slots or 5)))
    if duration > 0:
        target_count = max(3, min(max_slots, int(round(duration / 12.0)) or 3))
    else:
        target_count = max_slots

    def add_phrase(phrase: dict[str, Any], reason: str) -> None:
        start = max(0.0, float(phrase.get("start") or 0.0))
        end = max(start + 0.4, float(phrase.get("end") or start + 0.4))
        if any(abs(start - float(item["start"])) < 2.0 for item in selected):
            return
        index = len(selected)
        text = str(phrase.get("text") or "")
        intent = _beat_intent(text, index)
        title, eyebrow = _beat_copy(intent, text)
        beat_duration = max(3.2, min(6.4, end - start + 2.2))
        if duration > 0:
            beat_duration = min(beat_duration, max(1.0, duration - start))
        visual = _beat_visual(intent, index, text)
        selected.append(
            {
                "id": f"B{len(selected) + 1:02d}",
                "start": round(start, 3),
                "duration": round(beat_duration, 3),
                "label": title,
                "eyebrow": eyebrow,
                "quote": text,
                "intent": intent,
                "soft_component": visual["soft_component"],
                "visual_type": visual["design_preset"],
                "visual": visual,
                "reason": reason,
                "anchor_words": text[:180],
                "engine": "vibemotion-deterministic-overlay",
            }
        )

    first = next((phrase for phrase in phrases if float(phrase.get("start") or 0.0) >= 0.15), phrases[0])
    add_phrase(first, "Opening hook phrase.")

    priority_intents = ["cut", "callout", "motion", "workflow", "figma", "ltx", "result"]
    for intent in priority_intents:
        if len(selected) >= target_count:
            break
        candidates = [
            phrase
            for index, phrase in enumerate(phrases)
            if _beat_intent(str(phrase.get("text") or ""), index) == intent
        ]
        if candidates:
            add_phrase(max(candidates, key=lambda phrase: _motion_phrase_score(phrase, phrases.index(phrase))), f"Detected {intent} cue in transcript.")

    candidates = sorted(
        phrases,
        key=lambda phrase: _motion_phrase_score(phrase, phrases.index(phrase)),
        reverse=True,
    )
    for phrase in candidates:
        if len(selected) >= target_count:
            break
        start = float(phrase.get("start") or 0.0)
        if duration > 0 and (start < duration * 0.08 or start > duration * 0.94):
            continue
        add_phrase(phrase, "High-signal transcript phrase.")

    selected.sort(key=lambda item: float(item.get("start") or 0.0))
    for index, item in enumerate(selected, start=1):
        item["id"] = f"B{index:02d}"
    return selected[:target_count]


def _director_intent(prompt: str, phrase_text: str, index: int) -> str:
    if _director_is_generic_creative_motion_prompt(prompt):
        phrase_intent = _beat_intent(phrase_text, index)
        if phrase_intent not in {"hook", "emphasis"}:
            return phrase_intent
        storyboard = ["hook", "callout", "emphasis", "workflow", "result", "motion", "emphasis"]
        return storyboard[index % len(storyboard)]
    combined = f"{prompt} {phrase_text}"
    base = _beat_intent(combined, index)
    if _contains_any(combined, ["volume", "sound", "level", "progress", "\u0433\u0440\u043e\u043c\u043a", "\u0437\u0432\u0443\u043a", "\u0443\u0440\u043e\u0432", "\u043f\u0440\u043e\u0433\u0440\u0435\u0441"]):
        return "motion"
    if _contains_any(combined, ["point", "finger", "hand", "gesture", "corner", "callout", "label", "card", "\u043f\u0430\u043b\u0435\u0446", "\u0440\u0443\u043a", "\u0436\u0435\u0441\u0442", "\u0443\u0433\u043e\u043b", "\u043f\u043b\u0430\u0448", "\u0442\u0443\u0442", "\u0441\u044e\u0434\u0430"]):
        return "callout"
    return base


def _director_score(phrase: dict[str, Any], index: int, prompt: str, prompt_tokens: list[str], rng: random.Random) -> float:
    text = str(phrase.get("text") or "")
    if _director_bad_phrase(text):
        return -100.0 + rng.random()
    score = _motion_phrase_score(phrase, index)
    lowered = text.casefold()
    if _contains_any(prompt, ["no captions", "no subtitles", "without captions", "without subtitles", "do not add captions", "do not add subtitles", "\u0431\u0435\u0437 \u0441\u0443\u0431\u0442\u0438\u0442\u0440", "\u043d\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0439 \u0441\u0443\u0431\u0442\u0438\u0442\u0440", "\u0441\u0443\u0431\u0442\u0438\u0442\u0440\u044b \u043d\u0435"]) and _contains_any(text, ["caption", "subtitle", "\u0441\u0443\u0431\u0442\u0438\u0442\u0440", "\u043a\u0430\u043f\u0448\u043d"]):
        score -= 12.0
    for token in prompt_tokens:
        if token and token in lowered:
            score += 1.15
    if _contains_any(prompt, ["point", "finger", "hand", "gesture", "\u043f\u0430\u043b\u0435\u0446", "\u0440\u0443\u043a", "\u0436\u0435\u0441\u0442", "\u043f\u043e\u043a\u0430\u0437"]):
        if _contains_any(text, ["here", "this", "look", "corner", "\u0442\u0443\u0442", "\u0441\u044e\u0434\u0430", "\u0443\u0433\u043e\u043b", "\u0432\u043e\u0442"]):
            score += 3.0
        score += 0.35
    words = int(phrase.get("word_count") or 0)
    if 4 <= words <= 12:
        score += 1.0
    if words > 18:
        score -= 1.2
    return score + rng.random() * 8.5


def _director_bad_phrase(text: str) -> bool:
    lowered = str(text or "").casefold()
    bad_markers = [
        "\u043a\u0430\u0448\u043b",
        "\u043f\u0435\u0440\u0434",
        "\u043c\u044b\u0447",
        "\u0431\u043b\u0438\u043d",
        "\u0445\u0435\u0440",
        "\u0433\u043e\u0432\u043d",
        "\u044d\u044d",
        "\u044d\u043c\u043c",
        "uh",
        "umm",
    ]
    if not any(marker in lowered for marker in bad_markers):
        return False
    return not _contains_any(
        lowered,
        [
            "\u043f\u043b\u0430\u0448",
            "\u043c\u043e\u0443\u0448\u043d",
            "motion",
            "\u043c\u043e\u043d\u0442\u0430\u0436",
            "\u0444\u0438\u0433\u043c",
            "figma",
            "ltx",
            "\u0440\u0435\u043d\u0434\u0435\u0440",
        ],
    )


def _director_copy(prompt: str, phrase_text: str, intent: str, index: int, rng: random.Random) -> tuple[str, str]:
    quoted = _quoted_prompt_labels(prompt)
    if index < len(quoted):
        title = quoted[index]
    else:
        semantic = _semantic_director_label(prompt, phrase_text, intent, index, rng)
        if semantic:
            title = semantic
        else:
            fallback_by_intent = {
                "cut": "CLEAN CUT",
                "callout": rng.choice(["LOOK HERE", "KEY POINT", "ON SCREEN", "RIGHT HERE"]),
                "figma": "DESIGN FRAME",
                "ltx": "IMAGE MOTION",
                "motion": rng.choice(["MOTION BEAT", "VISUAL CUE", "CONTROL"]),
                "workflow": "EDIT PLAN",
                "result": "READY",
                "hook": "THE IDEA",
                "emphasis": "KEY MOMENT",
            }
            title = _director_label(phrase_text, fallback=fallback_by_intent.get(intent, "KEY MOMENT"))
            if len(title) <= 3 or title in {"THE", "THIS", "THAT"}:
                title = fallback_by_intent.get(intent, "KEY MOMENT")
    eyebrow = {
        "cut": "trim pass",
        "callout": "context cue",
        "figma": "frame",
        "ltx": "image",
        "motion": "motion",
        "workflow": "plan",
        "result": "export",
        "hook": "intro",
    }.get(intent, "beat")
    return title, eyebrow


def _semantic_director_label(prompt: str, phrase_text: str, intent: str, index: int, rng: random.Random) -> str | None:
    generic_creative = _director_is_generic_creative_motion_prompt(prompt)
    if generic_creative:
        return None
    text = (phrase_text if generic_creative else f"{prompt} {phrase_text}").casefold()
    rules: list[tuple[list[str], str]] = [
        (["\u043f\u043e\u0434\u043f\u0438\u0441", "\u043a\u0430\u043d\u0430\u043b", "subscribe"], "SUBSCRIBE"),
        (["\u0430\u0432\u0442\u043e\u043c\u043e\u043d\u0442\u0430\u0436", "auto edit", "auto-edit"], "AUTO EDIT"),
        (["\u043c\u043e\u0443\u0448\u043d", "motion graphic", "motion graphics"], "MOTION GRAPHICS"),
        (["\u043f\u043b\u0430\u0448", "plate", "callout"], "SMART CALLOUTS"),
        (["\u0441\u0443\u0431\u0442\u0438\u0442\u0440", "subtitle", "caption"], "SMART CALLOUTS"),
        (["figma", "\u0444\u0438\u0433\u043c"], "FIGMA LAYERS"),
        (["ltx", "\u0444\u043e\u0442\u043e", "\u043a\u0430\u0440\u0442\u0438\u043d"], "IMAGE MOTION"),
        (["\u043f\u043b\u0430\u043d", "\u043c\u043e\u043d\u0442\u0430\u0436", "edit plan", "workflow"], "AI EDIT PLAN"),
        (["render", "\u0440\u0435\u043d\u0434\u0435\u0440", "\u0433\u043e\u0442\u043e\u0432"], "READY"),
    ]
    for needles, label in rules:
        if any(needle in text for needle in needles):
            return label
    if intent == "callout" and (_director_wants_gesture(prompt) or _director_wants_callouts(prompt)):
        return rng.choice(["LOOK HERE", "KEY POINT", "RIGHT HERE"])
    if intent == "motion" and not generic_creative:
        return "MOTION GRAPHICS"
    if intent == "workflow" and not generic_creative:
        return "AI EDIT PLAN"
    return None


def _director_enter_from(direction: str | None, index: int) -> str:
    direction = str(direction or "")
    if "right" in direction:
        return "right"
    if "left" in direction:
        return "left"
    if "top" in direction:
        return "top"
    if "bottom" in direction:
        return "bottom"
    return "right" if index % 2 else "left"


def propose_hyperframes_agent_motion_slots(
    state: ProjectState,
    project_root: Path,
    *,
    prompt: str = "",
    variant_seed: str | None = None,
    style_profile: dict[str, Any] | None = None,
    vision_context: dict[str, Any] | None = None,
    max_slots: int = 5,
) -> list[dict[str, Any]]:
    if state.transcript is None or state.edit_plan is None:
        return []
    words = output_timeline_words(state.transcript, state.edit_plan.keep_ranges)
    phrases = _phrases_from_word_dicts(words, start_key="output_start", end_key="output_end")
    if not phrases:
        return []

    rng = _agent_director_rng(state, prompt, variant_seed)
    canvas_width, canvas_height = _project_canvas_size(state, project_root)
    duration = max(0.0, float(state.edit_plan.estimated_duration or 0.0))
    generic_creative = _director_is_generic_creative_motion_prompt(prompt)
    max_slots = max(1, min(8, int(max_slots or 5)))
    if generic_creative and duration > 0:
        target_count = max(4, min(max_slots, int(round(duration / 8.0)) or 4))
    else:
        target_count = max(3, min(max_slots, int(round(duration / 11.0)) or 3)) if duration > 0 else max_slots
    prompt_tokens = _prompt_tokens(prompt)
    selected: list[dict[str, Any]] = []

    def add_phrase(phrase: dict[str, Any], reason: str) -> None:
        if len(selected) >= target_count:
            return
        start = max(0.0, float(phrase.get("start") or 0.0))
        if any(abs(start - float(item["start"])) < 1.75 for item in selected):
            return
        if duration > 0 and start > duration - 0.65:
            return
        end = max(start + 0.4, float(phrase.get("end") or start + 0.4))
        index = len(selected)
        phrase_text = str(phrase.get("text") or "")
        if _director_bad_phrase(phrase_text):
            return
        if _contains_any(prompt, ["no captions", "no subtitles", "without captions", "without subtitles", "do not add captions", "do not add subtitles", "\u0431\u0435\u0437 \u0441\u0443\u0431\u0442\u0438\u0442\u0440", "\u043d\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0439 \u0441\u0443\u0431\u0442\u0438\u0442\u0440", "\u0441\u0443\u0431\u0442\u0438\u0442\u0440\u044b \u043d\u0435"]) and _contains_any(phrase_text, ["caption", "subtitle", "\u0441\u0443\u0431\u0442\u0438\u0442\u0440", "\u043a\u0430\u043f\u0448\u043d"]):
            return
        vision_text = _nearest_vision_frame_text(vision_context, start)
        intent = _director_intent(prompt, phrase_text, index)
        if intent == "hook" and prompt.strip() and not _director_wants_opening(prompt):
            intent = "callout" if (_director_wants_gesture(prompt) or _director_wants_callouts(prompt)) else "emphasis"
        component = _soft_component_for_intent(intent, f"{prompt} {phrase_text}", index)
        if generic_creative:
            component = ["hero", "callout", "text", "rows", "check", "callout", "text"][index % 7]
        if intent == "callout" and (_director_wants_gesture(prompt) or _director_wants_callouts(prompt)):
            component = "callout"
        if intent == "callout" and component in {"hero", "rows", "card", "text"}:
            component = "callout"
        direction = _direction_from_text(f"{prompt} {vision_text}", index)
        title, eyebrow = _director_copy(prompt, phrase_text, intent, index, rng)
        title_key = re.sub(r"\W+", "", title, flags=re.UNICODE).casefold()
        if title_key and any(title_key == re.sub(r"\W+", "", str(item.get("label") or ""), flags=re.UNICODE).casefold() for item in selected):
            for alternate in ["\u0421\u041c\u041e\u0422\u0420\u0418 \u0421\u042e\u0414\u0410", "\u0412\u0410\u0416\u041d\u042b\u0419 \u0410\u041a\u0426\u0415\u041d\u0422", "\u0412 \u041a\u0410\u0414\u0420\u0415", "\u0417\u0414\u0415\u0421\u042c"]:
                alternate_key = re.sub(r"\W+", "", alternate, flags=re.UNICODE).casefold()
                if all(alternate_key != re.sub(r"\W+", "", str(item.get("label") or ""), flags=re.UNICODE).casefold() for item in selected):
                    title = alternate
                    title_key = alternate_key
                    break
            else:
                if sum(title_key == re.sub(r"\W+", "", str(item.get("label") or ""), flags=re.UNICODE).casefold() for item in selected) >= 2:
                    return
        visual = _beat_visual(intent, index, phrase_text if generic_creative else f"{prompt} {phrase_text}")
        visual["design_preset"] = str((style_profile or {}).get("preset_id") or visual.get("design_preset") or SOFT_NEUMORPHISM_PRESET_ID)
        visual["soft_component"] = component
        visual["enter_from"] = _director_enter_from(direction, index)
        if generic_creative:
            creative_enter = ["rise", "slide", "pop", "slide", "fade", "rise", "slide"][index % 7]
            creative_from = ["bottom", "left", "center", "right", "center", "bottom", "left"][index % 7]
            visual["enter_animation"] = creative_enter
            visual["enter_from"] = creative_from
            visual["exit_animation"] = "fade" if index % 3 else "slide"
            visual["exit_to"] = "center" if visual["exit_animation"] == "fade" else ("left" if index % 2 else "right")
            visual["text_scale"] = 1.0 if component == "hero" else 0.86 if component in {"callout", "check"} else 0.92
        visual["enter_duration"] = round(float(visual.get("enter_duration") or 0.42) + rng.random() * 0.14, 3)
        visual["exit_duration"] = round(float(visual.get("exit_duration") or 0.34) + rng.random() * 0.08, 3)
        position = _seeded_position(prompt, phrase_text, intent, component, direction, index, canvas_width, canvas_height, rng)
        beat_duration = max(3.0, min(6.6, end - start + 2.0 + rng.random() * 0.65))
        if duration > 0:
            beat_duration = min(beat_duration, max(1.0, duration - start))
        selected.append(
            {
                "id": f"B{len(selected) + 1:02d}",
                "start": round(start, 3),
                "duration": round(beat_duration, 3),
                "label": title,
                "eyebrow": eyebrow,
                "quote": phrase_text,
                "intent": intent,
                "soft_component": component,
                "visual_type": visual["design_preset"],
                "visual": visual,
                "position": position,
                "direction": direction,
                "variant_seed": variant_seed,
                "reason": reason,
                "anchor_words": phrase_text[:180],
                "engine": "hyperframes-style-director",
            }
        )

    if _director_wants_opening(prompt):
        first = next((phrase for phrase in phrases if float(phrase.get("start") or 0.0) >= 0.15), phrases[0])
        add_phrase(first, "Opening beat requested by brief.")

    ranked = sorted(
        enumerate(phrases),
        key=lambda item: _director_score(item[1], item[0], prompt, prompt_tokens, rng),
        reverse=True,
    )
    for phrase_index, phrase in ranked:
        start = float(phrase.get("start") or 0.0)
        if duration > 0 and (start < duration * 0.06 or start > duration * 0.95):
            continue
        intent = _director_intent(prompt, str(phrase.get("text") or ""), phrase_index)
        add_phrase(phrase, f"Director picked {intent} beat from prompt, transcript, and visual context.")
        if len(selected) >= target_count:
            break

    if len(selected) < target_count:
        fallback = propose_agent_motion_slots(state, max_slots=target_count)
        for slot in fallback:
            phrase = {"start": slot.get("start"), "end": float(slot.get("start") or 0) + 0.6, "text": slot.get("quote") or slot.get("label") or ""}
            add_phrase(phrase, "Fallback beat kept because transcript had too few director candidates.")

    selected.sort(key=lambda item: float(item.get("start") or 0.0))
    for index, item in enumerate(selected, start=1):
        item["id"] = f"B{index:02d}"
    return selected[:target_count]


def apply_agent_motion_slots(
    state: ProjectState,
    project_root: Path,
    slots: list[dict[str, Any]],
    *,
    replace_existing: bool = True,
    style_profile: dict[str, Any] | None = None,
) -> ProjectState:
    canvas_width, canvas_height = _project_canvas_size(state, project_root)
    def is_agent_motion(motion: MotionSpec) -> bool:
        motion_plan = motion.motion_plan if isinstance(motion.motion_plan, dict) else {}
        return (
            str(motion.id or "").startswith(AGENT_MOTION_PREFIX)
            or motion_plan.get("engine") == "video-use-hyperframes-style-plan"
            or isinstance(motion_plan.get("agent_slot"), dict)
            or isinstance(motion_plan.get("agent_beat"), dict)
        )

    existing = [motion for motion in state.motions if not (replace_existing and is_agent_motion(motion))]
    generated: list[MotionSpec] = []
    tokens = dict((style_profile or {}).get("tokens") or {})
    style_family = str((style_profile or {}).get("style_family") or tokens.get("shape_language") or SOFT_NEUMORPHISM_PRESET_ID)
    accent = str(tokens.get("accent") or "#38bdf8")
    background = str(tokens.get("overlay_background") or "rgba(242, 241, 237, 0.98)")
    enter_animation = str(tokens.get("enter_animation") or "rise")
    exit_animation = str(tokens.get("exit_animation") or "fade")
    easing = str(tokens.get("easing") or "sine")
    if enter_animation not in {"none", "slide", "fade", "pop", "rise", "drop"}:
        enter_animation = "rise"
    if exit_animation not in {"none", "slide", "fade", "pop", "rise", "drop"}:
        exit_animation = "fade"
    if easing not in {"expo", "power", "sine", "linear"}:
        easing = "sine"
    slots = enrich_agent_motion_slots(state, project_root, slots)
    for index, slot in enumerate(slots):
        visual = dict(slot.get("visual") or {})
        position = dict(slot.get("position") or _beat_position(str(slot.get("quote") or ""), str(slot.get("intent") or ""), index, canvas_width, canvas_height))
        slot = dict(slot)
        slot["position"] = position
        slot["beat_id"] = str(slot.get("id") or f"B{index + 1:02d}")
        slot["soft_component"] = str(slot.get("soft_component") or visual.get("soft_component") or _soft_component_for_intent(str(slot.get("intent") or ""), str(slot.get("quote") or ""), index))
        requested_preset = str((style_profile or {}).get("preset_id") or visual.get("design_preset") or SOFT_NEUMORPHISM_PRESET_ID)
        design_preset = requested_preset if requested_preset in AGENT_STYLE_PRESET_IDS else SOFT_NEUMORPHISM_PRESET_ID
        slot["visual_type"] = design_preset
        visual["soft_component"] = slot["soft_component"]
        slot_enter = str(visual.get("enter_animation") or enter_animation)
        slot_exit = str(visual.get("exit_animation") or exit_animation)
        if slot_enter not in {"none", "slide", "fade", "pop", "rise", "drop"}:
            slot_enter = enter_animation
        if slot_exit not in {"none", "slide", "fade", "pop", "rise", "drop"}:
            slot_exit = exit_animation
        motion = MotionSpec(
            id=f"{AGENT_MOTION_PREFIX}{uuid.uuid4().hex[:8]}",
            kind="glass-card",
            design_preset=design_preset,  # type: ignore[arg-type]
            text=str(slot.get("label") or "Key moment"),
            start=max(0.0, float(slot.get("start") or 0.0)),
            duration=max(1.0, float(slot.get("duration") or 3.2)),
            x=position["x"],
            y=position["y"],
            width=position["width"],
            height=position["height"],
            text_scale=float(visual.get("text_scale") or 0.9),
            accent=accent,
            background=background,
            enter_animation=slot_enter,  # type: ignore[arg-type]
            exit_animation=slot_exit,  # type: ignore[arg-type]
            enter_from=str(visual.get("enter_from") or "bottom"),  # type: ignore[arg-type]
            exit_to=str(visual.get("exit_to") or "center"),  # type: ignore[arg-type]
            enter_duration=float(visual.get("enter_duration") or 0.42),
            exit_duration=float(visual.get("exit_duration") or 0.34),
            easing=easing,  # type: ignore[arg-type]
            prompt=f"Agent edit beat {slot['beat_id']}: {slot.get('reason', '')}",
            motion_plan={"agent_slot": slot, "agent_beat": slot, "soft_component": slot["soft_component"], "component_library": f"{style_family}-v1", "engine": "video-use-hyperframes-style-plan", "style": style_profile},
        )
        motion = apply_style_to_motion(motion, style_profile)
        generated.append(motion)
    return state.model_copy(update={"motions": [*existing, *generated]})


def _run_quiet(command: list[str]) -> bool:
    return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _extract_frame(video: Path, timestamp: float, output: Path, width: int = 220) -> Path | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ok = _run_quiet(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, timestamp):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "4",
            str(output),
        ]
    )
    return output if ok and output.exists() else None


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_timeline_view(
    video: Path,
    output: Path,
    *,
    start: float,
    end: float,
    words: list[dict[str, Any]] | None = None,
    title: str = "timeline view",
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end - start)
    frame_count = 8
    frame_width = 220
    frame_paths: list[Path] = []
    frames_dir = output.parent / f"{output.stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for index in range(frame_count):
        timestamp = start + duration * ((index + 0.5) / frame_count)
        frame_path = frames_dir / f"frame_{index:02d}.jpg"
        if _extract_frame(video, timestamp, frame_path, frame_width):
            frame_paths.append(frame_path)

    width = frame_width * max(1, len(frame_paths))
    filmstrip_height = 130
    waveform_height = 72
    text_height = 96
    header_height = 40
    canvas = Image.new("RGB", (max(640, width), header_height + filmstrip_height + waveform_height + text_height), (18, 18, 18))
    draw = ImageDraw.Draw(canvas)
    draw.text((14, 10), f"{title}  [{start:.2f}-{end:.2f}s]", font=_font(16), fill=(240, 240, 240))

    x = 0
    for frame_path in frame_paths:
        with Image.open(frame_path).convert("RGB") as frame:
            frame.thumbnail((frame_width, filmstrip_height), Image.Resampling.LANCZOS)
            y = header_height + (filmstrip_height - frame.height) // 2
            canvas.paste(frame, (x + (frame_width - frame.width) // 2, y))
        draw.line((x, header_height, x, header_height + filmstrip_height), fill=(70, 70, 70))
        x += frame_width

    wave_top = header_height + filmstrip_height
    draw.rectangle((0, wave_top, canvas.width, wave_top + waveform_height), fill=(26, 28, 26))
    relevant_words = [
        item
        for item in (words or [])
        if float(item.get("end") or item.get("output_end") or 0.0) >= start
        and float(item.get("start") or item.get("output_start") or 0.0) <= end
    ]
    for item in relevant_words:
        word_start = float(item.get("output_start", item.get("start", 0.0)) or 0.0)
        word_end = float(item.get("output_end", item.get("end", word_start)) or word_start)
        left = int(((word_start - start) / duration) * canvas.width)
        right = int(((word_end - start) / duration) * canvas.width)
        height = min(waveform_height - 10, 14 + len(str(item.get("text") or "")) * 2)
        draw.rectangle((left, wave_top + waveform_height - height, max(left + 2, right), wave_top + waveform_height - 6), fill=(76, 194, 255))

    text_top = wave_top + waveform_height
    phrases = _phrases_from_word_dicts(relevant_words, start_key="output_start" if relevant_words and "output_start" in relevant_words[0] else "start", end_key="output_end" if relevant_words and "output_end" in relevant_words[0] else "end")
    y = text_top + 10
    for phrase in phrases[:4]:
        line = f"[{phrase['start']:.2f}-{phrase['end']:.2f}] {phrase['text']}"
        draw.text((14, y), line[:150], font=_font(14), fill=(235, 235, 225))
        y += 20

    canvas.save(output)
    return output


def append_project_memory(state: ProjectState, edit_dir: Path, artifacts: dict[str, Any]) -> Path:
    memory = edit_dir / "project.md"
    lines = [
        "",
        "## Agent edit run",
        f"- project: {state.project_id}",
        f"- status: {state.status}",
        f"- clips: {len(state.edit_plan.keep_ranges) if state.edit_plan else 0}",
        f"- motions: {len(state.motions)}",
        f"- expected duration: {state.edit_plan.estimated_duration if state.edit_plan else 'unknown'}",
        f"- packed transcript: {artifacts.get('takes_packed', '')}",
        f"- edl: {artifacts.get('edl', '')}",
    ]
    with memory.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return memory


def write_motion_plan_markdown(slots: list[dict[str, Any]], output: Path) -> Path:
    lines = [
        "# Motion Plan",
        "",
        "Review the beats, then run Build planned edit to create timeline blocks and render the preview.",
        "",
    ]
    if not slots:
        lines.append("No motion beats were proposed.")
    for slot in slots:
        position = slot.get("position") if isinstance(slot.get("position"), dict) else {}
        lines.extend(
            [
                f"## {slot.get('id', 'B??')} - {slot.get('label', 'Key moment')}",
                "",
                f"- Time: {float(slot.get('start') or 0.0):.2f}s for {float(slot.get('duration') or 0.0):.2f}s",
                f"- Type: {slot.get('intent', 'emphasis')} / {slot.get('visual_type', 'glass')}",
                f"- Component: {slot.get('soft_component', 'text')}",
                f"- Placement: x={position.get('x', '?')}, y={position.get('y', '?')}, w={position.get('width', '?')}, h={position.get('height', '?')}",
                f"- Anchor: {slot.get('anchor_words') or slot.get('quote') or ''}",
                f"- Reason: {slot.get('reason', '')}",
                "",
            ]
        )
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output


def build_agent_edit_artifacts(
    state: ProjectState,
    project_root: Path,
    *,
    create_motions: bool = True,
    prompt: str = "",
    variant_seed: str | None = None,
) -> tuple[ProjectState, dict[str, Any]]:
    edit_dir = agent_edit_dir(project_root)
    takes_path = build_packed_transcript(state.transcript, edit_dir / "takes_packed.md")
    edl_path = build_edl(state, project_root, edit_dir / "edl.json")
    style_artifacts = write_style_artifacts(state, project_root, edit_dir)
    style_profile_path = project_root / style_artifacts["style_profile"]
    try:
        style_profile = json.loads(style_profile_path.read_text(encoding="utf-8"))
    except Exception:
        style_profile = build_style_profile(state, project_root)
    source = project_root / state.source_video if state.source_video else None
    vision_context: dict[str, Any] | None = None
    if source and source.exists():
        try:
            vision_context = analyze_video_with_vision(source, project_root)
        except Exception:
            vision_context = None
    slots = propose_hyperframes_agent_motion_slots(
        state,
        project_root,
        prompt=prompt,
        variant_seed=variant_seed,
        style_profile=style_profile,
        vision_context=vision_context,
        max_slots=7 if _director_is_generic_creative_motion_prompt(prompt) else 5,
    )
    slots = enrich_agent_motion_slots(state, project_root, slots)
    if slots and vision_context:
        canvas_width, canvas_height = _project_canvas_size(state, project_root)
        slots = apply_vision_safe_zones_to_slots(slots, vision_context, canvas_width, canvas_height)
    if create_motions and slots:
        state = apply_agent_motion_slots(state, project_root, slots, style_profile=style_profile)
    motion_plan_md = write_motion_plan_markdown(slots, edit_dir / "motion_plan.md")
    overview_path: Path | None = None
    if source and source.exists():
        raw_words = [_word_dict(word) for word in (state.transcript.words if state.transcript else [])]
        try:
            source_duration = detect_duration(source)
        except Exception:
            source_duration = float(state.transcript.duration or 0.0) if state.transcript else 0.0
        overview_path = build_timeline_view(
            source,
            edit_dir / "verify" / "source_overview.png",
            start=0.0,
            end=min(max(0.1, source_duration), 12.0),
            words=raw_words,
            title="source overview",
        )
    vision_path: Path | None = None
    if vision_context:
        vision_path = edit_dir / "vision_context.json"
        vision_path.write_text(json.dumps(vision_context, ensure_ascii=False, indent=2), encoding="utf-8")
    agent_plan = {
        "version": 2,
        "approach": "video-use / HyperFrames-style director: brief + transcript + vision context + style preset + variant seed -> motion beats -> self-eval",
        "project_id": state.project_id,
        "brief": prompt,
        "variant_seed": variant_seed,
        "summary": state.edit_plan.summary if state.edit_plan else "",
        "strategy": state.edit_plan.strategy if state.edit_plan else "",
        "style": style_profile,
        "motion_beats": slots,
        "motion_slots": slots,
        "vision_context": vision_context,
        "create_motions": bool(create_motions),
        "subtitles_enabled": bool(getattr(state, "subtitles_enabled", False)),
        "workflow": [
            "Transcribe and pack word timestamps.",
            "Build an EDL with pause/filler cleanup.",
            "Read the Agent brief, style preset, and visual context.",
            "Choose varied motion beats and placements with the director seed.",
            "After approval, build HyperFrames-style motion blocks and render.",
        ],
        "artifacts": {
            "takes_packed": str(takes_path.relative_to(project_root)),
            "edl": str(edl_path.relative_to(project_root)),
            "style_profile": style_artifacts["style_profile"],
            "design_md": style_artifacts["design_md"],
            "motion_plan_md": str(motion_plan_md.relative_to(project_root)),
            "source_overview": str(overview_path.relative_to(project_root)) if overview_path else None,
            "vision_context": str(vision_path.relative_to(project_root)) if vision_path else None,
        },
    }
    plan_path = edit_dir / "agent_plan.json"
    plan_path.write_text(json.dumps(agent_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts = {
        "agent_plan": str(plan_path.relative_to(project_root)),
        "takes_packed": str(takes_path.relative_to(project_root)),
        "edl": str(edl_path.relative_to(project_root)),
        "style_profile": style_artifacts["style_profile"],
        "design_md": style_artifacts["design_md"],
        "motion_plan_md": str(motion_plan_md.relative_to(project_root)),
        "source_overview": str(overview_path.relative_to(project_root)) if overview_path else None,
        "vision_context": str(vision_path.relative_to(project_root)) if vision_path else None,
    }
    memory = append_project_memory(state, edit_dir, artifacts)
    artifacts["project_memory"] = str(memory.relative_to(project_root))
    outputs = dict(state.outputs)
    outputs.update(
        {
            "agent_plan": artifacts["agent_plan"],
            "agent_takes_packed": artifacts["takes_packed"],
            "agent_edl": artifacts["edl"],
            "agent_motion_plan": artifacts["motion_plan_md"],
            "agent_style_profile": artifacts["style_profile"],
            "agent_design_md": artifacts["design_md"],
        }
    )
    if artifacts.get("source_overview"):
        outputs["agent_timeline_view"] = artifacts["source_overview"]
    if artifacts.get("vision_context"):
        outputs["agent_vision_context"] = artifacts["vision_context"]
    state = state.model_copy(update={"outputs": outputs})
    return state, artifacts


def self_eval_preview(state: ProjectState, project_root: Path) -> tuple[ProjectState, dict[str, Any]]:
    edit_dir = agent_edit_dir(project_root)
    verify_dir = edit_dir / "verify"
    preview_rel = state.outputs.get("preview")
    checks: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {}
    status = "pass"
    if not preview_rel:
        status = "warn"
        checks.append({"id": "preview-exists", "status": "warn", "message": "No rendered preview output to inspect."})
    else:
        preview_path = project_root / preview_rel
        if not preview_path.exists():
            status = "warn"
            checks.append({"id": "preview-exists", "status": "warn", "message": "Preview path is missing on disk."})
        else:
            actual_duration = detect_duration(preview_path)
            expected_duration = state.edit_plan.estimated_duration if state.edit_plan else actual_duration
            delta = abs(actual_duration - float(expected_duration or actual_duration))
            checks.append(
                {
                    "id": "duration",
                    "status": "pass" if delta <= 0.35 else "warn",
                    "actual": round(actual_duration, 3),
                    "expected": round(float(expected_duration or 0.0), 3),
                    "delta": round(delta, 3),
                }
            )
            if delta > 0.35:
                status = "warn"
            output_words = output_timeline_words(state.transcript, state.edit_plan.keep_ranges) if state.transcript and state.edit_plan else []
            overview = build_timeline_view(
                preview_path,
                verify_dir / "preview_overview.png",
                start=0.0,
                end=min(max(0.1, actual_duration), 12.0),
                words=output_words,
                title="rendered preview overview",
            )
            artifacts["preview_overview"] = str(overview.relative_to(project_root))
            boundaries: list[float] = []
            cursor = 0.0
            for keep_range in (state.edit_plan.keep_ranges if state.edit_plan else [])[:-1]:
                cursor += max(0.0, float(keep_range.end) - float(keep_range.start))
                boundaries.append(cursor)
            boundary_views = []
            for index, boundary in enumerate(boundaries[:8], start=1):
                start = max(0.0, boundary - 1.5)
                end = min(actual_duration, boundary + 1.5)
                path = build_timeline_view(
                    preview_path,
                    verify_dir / f"cut_{index:02d}_{int(boundary * 1000)}.png",
                    start=start,
                    end=max(start + 0.1, end),
                    words=output_words,
                    title=f"cut boundary {index}",
                )
                boundary_views.append(str(path.relative_to(project_root)))
            artifacts["cut_boundary_views"] = boundary_views
            checks.append({"id": "cut-boundary-views", "status": "pass", "count": len(boundary_views)})
            for motion in state.motions:
                motion_end = float(motion.start) + float(motion.duration)
                if motion_end > actual_duration + 0.25:
                    status = "warn"
                    checks.append(
                        {
                            "id": f"motion-window-{motion.id}",
                            "status": "warn",
                            "message": "Motion extends beyond rendered timeline.",
                            "end": round(motion_end, 3),
                            "duration": round(actual_duration, 3),
                        }
                    )
    report = {
        "version": 1,
        "status": status,
        "checks": checks,
        "artifacts": artifacts,
        "rule_notes": [
            "Cut edges are generated from word-level transcript ranges.",
            "Subtitles are applied after overlays in the render pipeline.",
            "Figma and LTX layers are preserved as independent motion blocks.",
        ],
    }
    report_path = edit_dir / "self_eval.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs = dict(state.outputs)
    outputs["agent_self_eval"] = str(report_path.relative_to(project_root))
    if artifacts.get("preview_overview"):
        outputs["agent_preview_overview"] = artifacts["preview_overview"]
    state = state.model_copy(update={"outputs": outputs})
    return state, report
