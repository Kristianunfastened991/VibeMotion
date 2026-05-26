from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from app.models.schemas import EditPlan, MotionSpec, TranscriptData
from app.services.media import detect_duration, detect_video_size
from app.services.motion import PRESET_DEFAULTS, fit_motion_to_canvas, place_motion_on_quiet_area, prompt_to_motion
from app.services.ollama import OllamaError, chat_json
from app.services.vision import analyze_video_with_vision


DEFAULT_MOTION_BLOCK_DURATION = 5.0
MAX_CONTEXT_MOTIONS = 5

MOTION_TYPES = {"auto", "text", "callout", "badge", "lower-third", "quote"}
_DURATION_TOKEN = r"(\d+(?:[\.,]\d+)?)"

STYLE_PACKS = [
    {
        "id": "soft-neumorphism",
        "label": "Soft Neumorphism",
        "preset": "soft-neumorphism",
        "accent": "#2b8cff",
        "background": "rgba(242, 241, 237, 0.98)",
        "enter_from": "bottom",
        "exit_to": "center",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    {
        "id": "frosted-glass",
        "label": "Frosted Glass",
        "preset": "frosted-glass",
        "accent": "#a6fff0",
        "background": "rgba(246, 248, 246, 0.58)",
        "enter_from": "bottom",
        "exit_to": "center",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    {
        "id": "warm-teal-ui",
        "label": "Warm Teal UI",
        "preset": "warm-teal-ui",
        "accent": "#006d6b",
        "background": "rgba(239, 231, 215, 0.98)",
        "enter_from": "bottom",
        "exit_to": "center",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    {
        "id": "clean-premium",
        "label": "Clean Premium",
        "preset": "glass",
        "accent": "#38bdf8",
        "background": "rgba(255, 255, 255, 0.72)",
        "enter_from": "bottom",
        "exit_to": "top",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    {
        "id": "cinematic",
        "label": "Cinematic",
        "preset": "bold-caption",
        "accent": "#f8fafc",
        "background": "rgba(0, 0, 0, 0.72)",
        "enter_from": "bottom",
        "exit_to": "bottom",
        "enter_animation": "fade",
        "exit_animation": "fade",
        "easing": "sine",
    },
    {
        "id": "editorial",
        "label": "Editorial",
        "preset": "glass",
        "accent": "#f97316",
        "background": "rgba(255, 255, 255, 0.82)",
        "enter_from": "left",
        "exit_to": "right",
        "enter_animation": "slide",
        "exit_animation": "slide",
        "easing": "expo",
    },
    {
        "id": "gaming-energy",
        "label": "Gaming/Energy",
        "preset": "bold-caption",
        "accent": "#22c55e",
        "background": "rgba(5, 10, 22, 0.78)",
        "enter_from": "center",
        "exit_to": "right",
        "enter_animation": "pop",
        "exit_animation": "slide",
        "easing": "power",
    },
    {
        "id": "minimal-apple",
        "label": "Minimal Apple-like",
        "preset": "creator-vibe",
        "accent": "#111827",
        "background": "rgba(255, 255, 255, 0.28)",
        "enter_from": "bottom",
        "exit_to": "center",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
]


DIRECTOR_SYSTEM_PROMPT = """You are a senior motion director for an easy video editor.
Return JSON only.
Create impressive editable motion overlay blocks for the whole video.
The user wants After Effects-level taste with minimal effort.
If the user gave exact text, never rewrite it. If exact_text is empty, concise copy may be derived from transcript/video context.
Use only these block types: text, callout, badge, lower-third, quote.
Do not use arrows or pointer graphics yet.
Do not turn the transcript into subtitles. Avoid raw long sentences, filler words, profanity, and self-referential junk.
Write short motion-design copy: 2-6 words, punchy, useful, and visually scannable.
Use only the currently selected built-in visual preset: Soft Neumorphism, Frosted Glass, or Warm Teal UI.
Soft Neumorphism means off-white soft panels, inset fields, pills, sliders, toggles, rows, and small blue accents.
Frosted Glass means translucent blurred panels, bright highlights, and optional dark inset code panels for technical beats.
Warm Teal UI means warm cream neumorphic interface panels, compact controls, inset fields, dark teal buttons, sliders, checkboxes, rows, and process pills.
Do not use dark editorial, neon, glitch, or bold-caption styling.
Avoid clutter: create 1 to 5 motion blocks only where useful.
Every block must fit inside source duration.
Default block duration is 5 seconds unless context clearly needs shorter.
Return shape:
{"motions":[{"text":"string","type":"text|callout|badge|lower-third|quote","style":"soft-neumorphism|frosted-glass|warm-teal-ui","start":number,"duration":number,"position":"top-left|top-right|center|bottom-left|bottom-right|lower-third","reason":"string"}]}
"""


AUTO_FALLBACK_LABELS = [
    "Look here",
    "Main idea",
    "Key point",
    "New angle",
    "Remember this",
]

AUTO_COPY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("подпис", "канал", "колоколь", "subscribe"), "Subscribe"),
    (("продукт", "дроп", "релиз", "запуск", "выходит", "launch"), "New product"),
    (("автомонтаж", "монтаж", "видеоролик", "edit", "editing"), "Auto edit"),
    (("моушн", "motion", "анимац", "плашк", "субтитр", "график"), "Motion graphics"),
    (("фигм", "figma", "слой", "layer"), "Layer on screen"),
    (("рендер", "render", "таймлайн", "timeline"), "Final render"),
    (("ltx", "фото", "картин", "image"), "Live image motion"),
    (("эффект", "вау", "красив", "premium", "after effects"), "Premium effect"),
    (("тут", "здесь", "угол", "показы", "палец", "смотри"), "Look here"),
]

BAD_AUTO_COPY_MARKERS = (
    "кашля",
    "перд",
    "хлюп",
    "мыч",
    "ээ",
    "эмм",
    "ммм",
    "типа",
    "короче",
    "блин",
    "хер",
    "говн",
    "снимаю для тебя",
    "что там еще",
)

AUTO_CONTEXT_TYPES = ["text", "callout", "lower-third", "badge", "quote"]
AUTO_CONTEXT_STYLES = ["soft-neumorphism", "frosted-glass", "warm-teal-ui"]
AUTO_CONTEXT_POSITIONS = ["top-left", "bottom-left", "lower-third", "top-right", "center"]


def normalize_motion_type(value: str | None) -> str:
    motion_type = re.sub(r"\s+", "-", str(value or "auto").strip().casefold())
    return motion_type if motion_type in MOTION_TYPES else "auto"


def _style_by_id(style_id: str | None) -> dict[str, Any] | None:
    normalized = re.sub(r"\s+", "-", str(style_id or "").strip().casefold())
    if normalized and normalized not in {"soft-neumorphism", "frosted-glass", "warm-teal-ui"}:
        return None
    for style in STYLE_PACKS:
        if style.get("id") == normalized:
            return style
    return None


def _normalize_style_id(style_id: str | None, index: int = 0) -> str:
    normalized = re.sub(r"\s+", "-", str(style_id or "").strip().casefold())
    return normalized if normalized in {"soft-neumorphism", "frosted-glass", "warm-teal-ui"} else "soft-neumorphism"


def _clean_exact_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'")
    if not text:
        return ""
    splitters = [
        r"\s+(?:в|на)\s+(?:левом|правом|верхнем|нижнем)\s+углу\b",
        r"\s+(?:слева|справа|сверху|снизу|по центру)\b",
        r"\s+(?:in|at|on)\s+(?:the\s+)?(?:left|right|top|bottom|center)\b",
    ]
    for splitter in splitters:
        text = re.split(splitter, text, maxsplit=1, flags=re.IGNORECASE)[0].strip(" \t\r\n\"'")
    return text[:120]


def extract_user_motion_text(prompt: str) -> str | None:
    text = str(prompt or "")
    quoted = re.findall(r"[\"'«“](.*?)[\"'»”]", text)
    for item in quoted:
        cleaned = _clean_exact_text(item)
        if cleaned:
            return cleaned

    patterns = [
        r"(?:текст|надпись|напиши|пиши|с\s+текстом|с\s+надписью|with\s+(?:the\s+)?text|says|label)\s*[:\-]?\s+(.+)$",
        r"(?:добавь|создай|покажи|add|create|show)\s+.{0,40}?(?:текст|надпись|text|label)\s*[:\-]?\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        cleaned = _clean_exact_text(match.group(1))
        if cleaned:
            return cleaned
    return None


def is_general_motion_request(prompt: str) -> bool:
    if extract_user_motion_text(prompt):
        return False
    text = re.sub(r"\s+", " ", str(prompt or "")).strip().casefold()
    if not text:
        return False
    markers = [
        "сделай красиво",
        "сделай моушн",
        "сделай motion",
        "оживи",
        "оформи",
        "оформи видео",
        "улучши",
        "добавь моушн",
        "добавь motion",
        "добавь надписи",
        "добавь акценты",
        "по всему видео",
        "для всего видео",
        "весь ролик",
        "красивый motion",
        "красивый моушн",
        "make it beautiful",
        "make motion",
        "add motion",
        "whole video",
        "entire video",
        "full video",
        "make it pop",
        "make it better",
    ]
    return any(marker in text for marker in markers)


def _style_for(prompt: str, motion_type: str = "auto", variant_index: int = 0, enhance: bool = True) -> dict[str, Any]:
    return STYLE_PACKS[0]


def _type_for(prompt: str, requested_type: str = "auto") -> str:
    requested = normalize_motion_type(requested_type)
    if requested != "auto":
        return requested
    text = str(prompt or "").casefold()
    if any(item in text for item in ("lower third", "нижн", "имя", "подпись")):
        return "lower-third"
    if any(item in text for item in ("quote", "цитат")):
        return "quote"
    if any(item in text for item in ("плашк", "карточк", "табличк", "plate", "card", "callout", "выноск", "акцент")):
        return "callout"
    if any(item in text for item in ("badge", "бейдж", "label", "лейбл")):
        return "badge"
    return "text"


def _soft_component_for_prompt(prompt: str, block_type: str) -> str:
    text = str(prompt or "").casefold()
    if any(item in text for item in ("hero", "title", "intro", "opening", "hook", "заголов", "вступлен", "хук")):
        return "hero"
    if any(item in text for item in ("volume", "sound", "loud", "increase", "boost", "progress", "loading", "level", "meter", "громк", "звук", "прибав", "увелич", "прогресс", "загруз", "уров")):
        return "slider"
    if any(item in text for item in ("toggle", "switch", "on off", "turn on", "turn off", "enable", "disable", "переключ", "тумблер", "включ", "выключ")):
        return "toggle"
    if any(item in text for item in ("done", "complete", "ready", "success", "approved", "check", "готов", "заверш", "успеш", "галоч")):
        return "check"
    if any(item in text for item in ("table", "row", "list", "steps", "items", "spacing", "план", "таблиц", "спис", "строк", "пункт", "шаг")):
        return "rows"
    if any(item in text for item in ("callout", "badge", "lower third", "plate", "label", "corner", "point", "here", "this spot", "плашк", "лейбл", "угол", "сюда", "тут", "показыв")):
        return "callout"
    if any(item in text for item in ("hyperframes", "remotion", "code", "html", "api")):
        return "card"
    if any(item in text for item in ("input", "field", "dropdown", "select", "form", "figma", "frame", "layer", "инпут", "поле", "дропдаун", "форма", "фигм", "фрейм", "слой")):
        return "card"
    if block_type == "badge":
        return "callout"
    if block_type == "callout":
        return "callout"
    if block_type == "lower-third":
        return "text"
    return "text"


def _explicit_total_duration(prompt: str) -> float | None:
    text = str(prompt or "").casefold().replace(",", ".")
    patterns = [
        rf"(?:через|спустя|после|after)\s+{_DURATION_TOKEN}\s*(?:секунд[ыу]?|сек\.?|s|sec|seconds?)",
        rf"(?:на|for)\s+{_DURATION_TOKEN}\s*(?:секунд[ыу]?|сек\.?|s|sec|seconds?)",
        rf"{_DURATION_TOKEN}\s*(?:секунд[ыу]?|сек\.?|s|sec|seconds?)\s+(?:и\s+)?(?:исчез|пропад|убер|уйд|fade\s*out|disappear)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 0.25 <= value <= 60:
            return value
    return None


def _director_animation_overrides(prompt: str, enter_animation: str, exit_animation: str, enter_from: str, exit_to: str) -> tuple[str, str, str, str]:
    text = str(prompt or "").casefold()
    if any(item in text for item in ("фейд ин", "fade in", "плавно появ", "мягко появ", "прояв")):
        enter_animation = "fade"
        enter_from = "center"
    if any(item in text for item in ("исчез", "пропад", "убер", "fade out", "disappear", "vanish")):
        exit_animation = "fade"
        exit_to = "center"
    return enter_animation, exit_animation, enter_from, exit_to


def _position_for(position: str | None, canvas_width: int, canvas_height: int, width: int, height: int) -> tuple[int, int]:
    margin = max(16, int(min(canvas_width, canvas_height) * 0.045))
    safe_top = max(margin, int(canvas_height * 0.09))
    key = re.sub(r"\s+", "-", str(position or "").strip().casefold())
    if key in {"top-right", "right-top"}:
        return canvas_width - width - margin, safe_top
    if key in {"bottom-right", "right-bottom"}:
        return canvas_width - width - margin, canvas_height - height - margin
    if key in {"center", "middle"}:
        return (canvas_width - width) // 2, (canvas_height - height) // 2
    if key in {"bottom-left", "left-bottom"}:
        return margin, canvas_height - height - margin
    if key in {"lower-third", "bottom"}:
        return margin, max(margin, int(canvas_height * 0.72) - height // 2)
    return margin, safe_top


def _position_from_prompt(prompt: str) -> str | None:
    text = str(prompt or "").casefold()
    if any(item in text for item in ("lower third", "нижн", "снизу", "внизу", "bottom")):
        return "lower-third"
    if any(item in text for item in ("центр", "центре", "по центру", "center", "middle")):
        return "center"
    right = any(item in text for item in ("справа", "правом", "right"))
    left = any(item in text for item in ("слева", "левом", "left"))
    top = any(item in text for item in ("сверху", "верх", "top"))
    bottom = any(item in text for item in ("снизу", "ниж", "bottom"))
    if right and bottom:
        return "bottom-right"
    if right and top:
        return "top-right"
    if left and bottom:
        return "bottom-left"
    if left and top:
        return "top-left"
    if right:
        return "top-right"
    if left:
        return "top-left"
    return None


def _size_for(block_type: str, style: dict[str, Any], canvas_width: int, canvas_height: int, component: str | None = None) -> tuple[int, int]:
    portrait = canvas_height > canvas_width
    component = str(component or "text")
    if component == "hero":
        return (int(canvas_width * (0.86 if portrait else 0.48)), int(canvas_height * (0.23 if portrait else 0.28)))
    if component == "slider":
        return (int(canvas_width * (0.84 if portrait else 0.52)), int(canvas_height * (0.13 if portrait else 0.14)))
    if component == "toggle":
        return (int(canvas_width * (0.74 if portrait else 0.36)), int(canvas_height * (0.11 if portrait else 0.13)))
    if component == "rows":
        return (int(canvas_width * (0.84 if portrait else 0.38)), int(canvas_height * (0.24 if portrait else 0.30)))
    if component == "card":
        return (int(canvas_width * (0.82 if portrait else 0.38)), int(canvas_height * (0.20 if portrait else 0.22)))
    if component == "callout":
        return (int(canvas_width * (0.78 if portrait else 0.38)), int(canvas_height * (0.10 if portrait else 0.12)))
    if block_type == "lower-third":
        return (int(canvas_width * (0.88 if portrait else 0.52)), int(canvas_height * (0.15 if portrait else 0.14)))
    if block_type == "badge":
        return (int(canvas_width * (0.62 if portrait else 0.30)), int(canvas_height * (0.11 if portrait else 0.13)))
    if block_type == "quote":
        return (int(canvas_width * (0.86 if portrait else 0.46)), int(canvas_height * (0.20 if portrait else 0.18)))
    if block_type == "callout":
        return (int(canvas_width * (0.84 if portrait else 0.46)), int(canvas_height * (0.17 if portrait else 0.16)))
    if style.get("id") == "cinematic":
        return (int(canvas_width * (0.82 if portrait else 0.40)), int(canvas_height * (0.13 if portrait else 0.13)))
    return (int(canvas_width * (0.82 if portrait else 0.40)), int(canvas_height * (0.14 if portrait else 0.14)))


def _preset_for(block_type: str, style: dict[str, Any]) -> str:
    return str(style.get("preset") or "soft-neumorphism")
    if block_type == "badge":
        return "bold-caption"
    if block_type == "lower-third":
        return "glass"
    if block_type == "quote":
        return "glass"
    return str(style.get("preset") or "creator-vibe")


def _legacy_animation(enter_animation: str, enter_from: str) -> str:
    if enter_animation == "fade" or enter_from == "center":
        return "fade"
    if enter_from == "left":
        return "slide-right"
    if enter_from == "bottom":
        return "slide-up"
    return "slide-left"


def build_directed_motion(
    *,
    prompt: str,
    start: float,
    duration: float,
    canvas_width: int,
    canvas_height: int,
    preset: str = "soft-neumorphism",
    enhance: bool = True,
    motion_type: str = "auto",
    variant_index: int = 0,
    exact_text: str | None = None,
    position: str | None = None,
    style_id: str | None = None,
    existing_id: str | None = None,
    keep_text: str | None = None,
) -> MotionSpec:
    block_type = _type_for(prompt, motion_type)
    style = _style_for(prompt, block_type, variant_index=variant_index, enhance=enhance)
    if style_id:
        style = _style_by_id(style_id) or style
    exact = exact_text if exact_text is not None else extract_user_motion_text(prompt)
    base_prompt = prompt
    if exact:
        base_prompt = f'{prompt}\nUse exact on-screen text: "{exact}"'

    motion = prompt_to_motion(base_prompt, duration_hint=max(0.25, float(duration or DEFAULT_MOTION_BLOCK_DURATION)), preset=preset)
    if existing_id:
        motion.id = existing_id
    motion.start = max(0.0, float(start or 0.0))
    if exact:
        motion.text = exact
    elif keep_text:
        motion.text = keep_text
    explicit_duration = _explicit_total_duration(prompt)
    motion.duration = max(0.25, float(explicit_duration or duration or motion.duration or DEFAULT_MOTION_BLOCK_DURATION))

    soft_component = _soft_component_for_prompt(prompt, block_type)
    width, height = _size_for(block_type, style, canvas_width, canvas_height, component=soft_component)
    x, y = _position_for(position or _position_from_prompt(prompt), canvas_width, canvas_height, width, height)
    preset_name = _preset_for(block_type, style)
    defaults = PRESET_DEFAULTS.get(preset_name, PRESET_DEFAULTS["soft-neumorphism"])
    enter_animation = str(style.get("enter_animation") or motion.enter_animation or "slide")
    exit_animation = str(style.get("exit_animation") or motion.exit_animation or "fade")
    enter_from = str(style.get("enter_from") or motion.enter_from or "bottom")
    exit_to = str(style.get("exit_to") or motion.exit_to or "center")
    enter_animation, exit_animation, enter_from, exit_to = _director_animation_overrides(
        prompt,
        enter_animation,
        exit_animation,
        enter_from,
        exit_to,
    )
    motion_plan = dict(motion.motion_plan or {})
    motion_plan["soft_component"] = soft_component
    motion_plan["component_library"] = f"{preset_name}-v1"
    motion_plan["director"] = {
        "style": style.get("id"),
        "style_label": style.get("label"),
        "type": block_type,
        "soft_component": soft_component,
        "enhance": bool(enhance),
        "variant_index": int(variant_index),
        "exact_text": bool(exact),
    }
    return motion.model_copy(
        update={
            "kind": defaults.get("kind", motion.kind),
            "design_preset": preset_name,
            "x": int(x),
            "y": int(y),
            "width": max(120, min(1400, int(width))),
            "height": max(64, min(620, int(height))),
            "accent": str(style.get("accent") or defaults.get("accent") or motion.accent),
            "background": str(style.get("background") or defaults.get("background") or motion.background),
            "animation": _legacy_animation(enter_animation, enter_from),
            "enter_animation": enter_animation,
            "exit_animation": exit_animation,
            "enter_from": enter_from,
            "exit_to": exit_to,
            "enter_duration": 0.54 if enhance else motion.enter_duration,
            "exit_duration": 0.42 if enhance else motion.exit_duration,
            "easing": str(style.get("easing") or motion.easing or "expo"),
            "prompt": prompt,
            "motion_plan": motion_plan,
        }
    )


def next_variant_index(motion: MotionSpec) -> int:
    plan = motion.motion_plan if isinstance(motion.motion_plan, dict) else {}
    director = plan.get("director") if isinstance(plan.get("director"), dict) else {}
    try:
        return int(director.get("variant_index") or 0) + 1
    except (TypeError, ValueError):
        return 1


def variant_motion(
    motion: MotionSpec,
    *,
    prompt: str,
    canvas_width: int,
    canvas_height: int,
    enhance: bool = True,
    motion_type: str = "auto",
    variant_index: int | None = None,
) -> MotionSpec:
    exact = extract_user_motion_text(prompt)
    keep_text = None if exact else motion.text
    next_index = next_variant_index(motion) if variant_index is None else int(variant_index)
    new_motion = build_directed_motion(
        prompt=prompt or motion.prompt or "Create another tasteful motion variant",
        start=float(motion.start or 0.0),
        duration=float(motion.duration or DEFAULT_MOTION_BLOCK_DURATION),
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        preset=motion.design_preset,
        enhance=enhance,
        motion_type=motion_type,
        variant_index=next_index,
        exact_text=exact,
        existing_id=motion.id,
        keep_text=keep_text,
    )
    return fit_motion_to_canvas(new_motion, canvas_width, canvas_height)


def _segments_payload(transcript: TranscriptData | None) -> list[dict[str, Any]]:
    if not transcript:
        return []
    items = []
    for segment in transcript.segments[:80]:
        text = re.sub(r"\s+", " ", segment.text or "").strip()
        if not text:
            continue
        items.append({"start": segment.start, "end": segment.end, "text": text[:180]})
    return items


def _fallback_auto_label(index: int, source_text: str = "") -> str:
    text = str(source_text or "").casefold()
    for keywords, label in AUTO_COPY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return label
    return AUTO_FALLBACK_LABELS[index % len(AUTO_FALLBACK_LABELS)]


def _has_context_signal(source_text: str) -> bool:
    text = str(source_text or "").casefold()
    return any(keyword in text for keywords, _label in AUTO_COPY_KEYWORDS for keyword in keywords)


def _segment_is_disposable(source_text: str) -> bool:
    text = str(source_text or "").casefold()
    return any(marker in text for marker in BAD_AUTO_COPY_MARKERS) and not _has_context_signal(text)


def _looks_like_bad_auto_copy(text: str) -> bool:
    lowered = str(text or "").casefold()
    if any(marker in lowered for marker in BAD_AUTO_COPY_MARKERS):
        return True
    words = re.findall(r"[\wЁёА-Яа-яA-Za-z]+", lowered)
    if len(words) > 8:
        return True
    if len(text) > 56:
        return True
    return False


def _polish_auto_copy(raw_text: str, *, index: int, source_text: str = "") -> str:
    text = _clean_exact_text(raw_text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?-")
    if not text or _looks_like_bad_auto_copy(text):
        return _fallback_auto_label(index, source_text or raw_text)

    # Longer sentence fragments look like accidental subtitles, not motion design.
    sentence = re.split(r"[.!?…]+", text, maxsplit=1)[0].strip(" .,:;!?-")
    if sentence and not _looks_like_bad_auto_copy(sentence):
        text = sentence
    words = re.findall(r"[\wЁёА-Яа-яA-Za-z]+", text)
    if len(words) > 6:
        return _fallback_auto_label(index, source_text or raw_text)
    return text[:42].strip(" .,:;!?-") or _fallback_auto_label(index, source_text or raw_text)


def _context_item_type(raw_type: Any, index: int, requested_type: str = "auto") -> str:
    item_type = normalize_motion_type(str(raw_type or requested_type or "auto"))
    if item_type != "auto":
        return item_type
    requested = normalize_motion_type(requested_type)
    if requested != "auto":
        return requested
    return AUTO_CONTEXT_TYPES[index % len(AUTO_CONTEXT_TYPES)]


def _source_duration(transcript: TranscriptData | None, edit_plan: EditPlan | None, source_video: Path | None = None) -> float:
    for value in (
        transcript.duration if transcript else None,
        edit_plan.estimated_duration if edit_plan else None,
    ):
        try:
            duration = float(value or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            return duration
    if source_video is not None and source_video.exists():
        try:
            return max(1.0, float(detect_duration(source_video)))
        except Exception:
            pass
    return 1.0


def _fallback_context_items(transcript: TranscriptData | None, source_duration: float) -> list[dict[str, Any]]:
    segments = _segments_payload(transcript)
    if not segments:
        count = min(3, MAX_CONTEXT_MOTIONS)
        spacing = source_duration / max(1, count + 1)
        return [
            {
                "text": AUTO_FALLBACK_LABELS[index],
                "type": AUTO_CONTEXT_TYPES[index],
                "style": AUTO_CONTEXT_STYLES[index],
                "start": max(0.0, min(spacing * (index + 1), max(0.0, source_duration - DEFAULT_MOTION_BLOCK_DURATION))),
                "duration": min(DEFAULT_MOTION_BLOCK_DURATION, max(1.0, source_duration)),
                "position": AUTO_CONTEXT_POSITIONS[index],
                "reason": "Neutral fallback without transcript.",
            }
            for index in range(count)
        ]
    signal_segments = [segment for segment in segments if _has_context_signal(segment["text"])]
    candidates = signal_segments if len(signal_segments) >= 3 else [segment for segment in segments if not _segment_is_disposable(segment["text"])]
    if len(candidates) < min(3, len(segments)):
        candidates = segments
    picks = []
    if len(candidates) <= MAX_CONTEXT_MOTIONS:
        target_indexes = list(range(len(candidates)))
    else:
        target_indexes = [0, 1, len(candidates) // 3, len(candidates) // 2, max(0, len(candidates) - 1)]
    seen = set()
    for index in target_indexes:
        if index in seen or index >= len(candidates) or len(picks) >= MAX_CONTEXT_MOTIONS:
            continue
        seen.add(index)
        segment = candidates[index]
        pick_index = len(picks)
        picks.append(
            {
                "text": _polish_auto_copy(segment["text"], index=pick_index, source_text=segment["text"]),
                "type": AUTO_CONTEXT_TYPES[pick_index % len(AUTO_CONTEXT_TYPES)],
                "style": AUTO_CONTEXT_STYLES[pick_index % len(AUTO_CONTEXT_STYLES)],
                "start": max(0.0, min(float(segment["start"]), max(0.0, source_duration - DEFAULT_MOTION_BLOCK_DURATION))),
                "duration": min(DEFAULT_MOTION_BLOCK_DURATION, max(1.0, source_duration)),
                "position": AUTO_CONTEXT_POSITIONS[pick_index % len(AUTO_CONTEXT_POSITIONS)],
                "reason": "Fallback from transcript topic, rewritten as short motion copy.",
            }
        )
    return picks[:MAX_CONTEXT_MOTIONS]


def plan_context_motion_items(
    *,
    prompt: str,
    transcript: TranscriptData | None,
    edit_plan: EditPlan | None,
    source_video: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    source_duration = _source_duration(transcript, edit_plan, source_video)
    transcript_items = _segments_payload(transcript)
    if transcript_items:
        return _fallback_context_items(transcript, source_duration)
    if transcript is None:
        return _fallback_context_items(transcript, source_duration)

    vision_context: dict[str, Any] | None = None
    try:
        vision_context = analyze_video_with_vision(source_video, project_root)
    except Exception:
        vision_context = None
    payload = {
        "user_prompt": prompt,
        "exact_text": extract_user_motion_text(prompt) or "",
        "source_duration": source_duration,
        "transcript_segments": transcript_items,
        "edit_summary": edit_plan.summary if edit_plan else "",
        "motion_notes": edit_plan.motion_notes if edit_plan else [],
        "vision_context": vision_context,
        "rules": [
            "Create useful high-impact motion overlays, not subtitles.",
            "If exact_text is present, use it exactly and create one block near the playhead/start request.",
            "If exact_text is empty, write short editorial labels from transcript/context; never paste raw long transcript.",
            "Prefer 2 to 6 words per block.",
            "Use varied but compatible styles.",
            "Avoid arrows/pointers in this MVP.",
        ],
    }
    try:
        response = chat_json(DIRECTOR_SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), timeout=90)
        motions = response.get("motions") if isinstance(response, dict) else None
        if isinstance(motions, list) and motions:
            return [item for item in motions if isinstance(item, dict)][:MAX_CONTEXT_MOTIONS]
    except (OllamaError, Exception):
        pass
    return _fallback_context_items(transcript, source_duration)


def build_context_motions(
    *,
    prompt: str,
    transcript: TranscriptData | None,
    edit_plan: EditPlan | None,
    source_video: Path,
    project_root: Path,
    enhance: bool = True,
    motion_type: str = "auto",
) -> list[MotionSpec]:
    canvas_width, canvas_height = detect_video_size(source_video)
    source_duration = _source_duration(transcript, edit_plan, source_video)
    items = plan_context_motion_items(
        prompt=prompt,
        transcript=transcript,
        edit_plan=edit_plan,
        source_video=source_video,
        project_root=project_root,
    )
    exact = extract_user_motion_text(prompt)
    specs: list[MotionSpec] = []
    for index, item in enumerate(items[:MAX_CONTEXT_MOTIONS]):
        item_type = _context_item_type(item.get("type"), index, motion_type)
        source_text = f"{item.get('text') or ''} {item.get('reason') or ''}"
        text = _clean_exact_text(str(item.get("text") or "")) or (exact or AUTO_FALLBACK_LABELS[index % len(AUTO_FALLBACK_LABELS)])
        if exact:
            text = exact
        else:
            text = _polish_auto_copy(text, index=index, source_text=source_text)
        try:
            start = float(item.get("start", 0.0) or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(item.get("duration", DEFAULT_MOTION_BLOCK_DURATION) or DEFAULT_MOTION_BLOCK_DURATION)
        except (TypeError, ValueError):
            duration = DEFAULT_MOTION_BLOCK_DURATION
        duration = max(1.0, min(DEFAULT_MOTION_BLOCK_DURATION if exact else 7.0, duration))
        start = max(0.0, min(start, max(0.0, source_duration - min(duration, source_duration))))
        style_id = _normalize_style_id(str(item.get("style") or ""), index)
        item_prompt = f"{prompt}\nGenerated motion text: {text}"
        motion = build_directed_motion(
            prompt=item_prompt,
            start=start,
            duration=min(duration, max(1.0, source_duration - start)),
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            enhance=enhance,
            motion_type=item_type,
            variant_index=index,
            exact_text=text,
            position=str(item.get("position") or ""),
            style_id=style_id,
        )
        plan = dict(motion.motion_plan or {})
        director = dict(plan.get("director") or {})
        director["reason"] = str(item.get("reason") or "")
        director["source"] = "context"
        plan["director"] = director
        motion = motion.model_copy(update={"id": f"motion-auto-{uuid.uuid4().hex[:8]}", "motion_plan": plan})
        motion = fit_motion_to_canvas(motion, canvas_width, canvas_height)
        motion = place_motion_on_quiet_area(motion, source_video, project_root / "assets")
        specs.append(motion)
        if exact:
            break
    return specs
