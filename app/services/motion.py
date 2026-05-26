from __future__ import annotations

import json
import hashlib
import math
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageStat

from app.models.schemas import MotionSpec
from app.services.ollama import OllamaError, chat_json


FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]

CREATOR_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\ARIALN.TTF",
    r"C:\Windows\Fonts\segoeui.ttf",
]

MAX_FIGMA_MOTION_RENDER_SECONDS = 120.0
MOTION_ASSET_SIGNATURE_VERSION = "vibemotion-motion-asset-v8"
MOTION_ASSET_VOLATILE_KEYS = {"asset_version", "video_asset_path", "asset_signature", "motion_units"}
MOTION_VISUAL_SELF_CHECK_VERSION = 1


def motion_asset_signature(spec: MotionSpec) -> str:
    """Hash the deterministic inputs that define a rendered motion asset."""
    payload = spec.model_dump(mode="json")
    for key in MOTION_ASSET_VOLATILE_KEYS:
        payload.pop(key, None)
    encoded = json.dumps(
        {
            "version": MOTION_ASSET_SIGNATURE_VERSION,
            "motion": payload,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _prune_versioned_motion_videos(assets_dir: Path, motion_id: str, keep_name: str, keep_count: int = 3) -> None:
    pattern = f"{motion_id}-*.mp4"
    candidates = sorted(
        (path for path in assets_dir.glob(pattern) if path.name != keep_name),
        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0,
        reverse=True,
    )
    for stale in candidates[keep_count - 1 :]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            continue


def _best_effort_rmtree(path: Path, attempts: int = 5) -> bool:
    if not path.exists():
        return True
    for attempt in range(max(1, attempts)):
        try:
            shutil.rmtree(path)
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(0.15 * (attempt + 1))
    return False


def _prune_motion_frame_dirs(assets_dir: Path, motion_id: str, keep: Path | None = None) -> None:
    keep_resolved = keep.resolve() if keep else None
    for candidate in assets_dir.glob(f"{motion_id}_frames*"):
        if not candidate.is_dir():
            continue
        if keep_resolved and candidate.resolve() == keep_resolved:
            continue
        _best_effort_rmtree(candidate)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _creator_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in CREATOR_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return _font(size)


def _parse_rgba(value: str, fallback: tuple[int, int, int, int] = (0, 0, 0, 255)) -> tuple[int, int, int, int]:
    match = re.search(r"rgba?\(([^)]+)\)", str(value or ""))
    if not match:
        return fallback
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) < 3:
        return fallback
    try:
        r = int(float(parts[0]))
        g = int(float(parts[1]))
        b = int(float(parts[2]))
        a = float(parts[3]) if len(parts) >= 4 else 1.0
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), max(0, min(255, int(round(a * 255)))))
    except ValueError:
        return fallback


PRESET_DEFAULTS = {
    "soft-neumorphism": {
        "kind": "glass-card",
        "x": 72,
        "y": 76,
        "width": 460,
        "height": 170,
        "accent": "#2b8cff",
        "animation": "slide-up",
        "background": "rgba(242, 241, 237, 0.98)",
    },
    "frosted-glass": {
        "kind": "glass-card",
        "x": 92,
        "y": 86,
        "width": 520,
        "height": 150,
        "accent": "#a6fff0",
        "animation": "slide-up",
        "background": "rgba(246, 248, 246, 0.58)",
    },
    "warm-teal-ui": {
        "kind": "glass-card",
        "x": 88,
        "y": 90,
        "width": 500,
        "height": 150,
        "accent": "#006d6b",
        "animation": "slide-up",
        "background": "rgba(239, 231, 215, 0.98)",
    },
    "creator-vibe": {
        "kind": "glass-card",
        "x": 72,
        "y": 76,
        "width": 520,
        "height": 220,
        "accent": "#ffffff",
        "animation": "slide-left",
        "background": "rgba(255, 255, 255, 0.24)",
    },
    "glass": {
        "kind": "glass-card",
        "x": 72,
        "y": 76,
        "width": 520,
        "height": 132,
        "accent": "#38bdf8",
        "animation": "slide-up",
        "background": "rgba(255, 255, 255, 0.72)",
    },
    "liquid-glass": {
        "kind": "glass-card",
        "x": 110,
        "y": 120,
        "width": 880,
        "height": 260,
        "accent": "#7dd3fc",
        "animation": "slide-up",
        "background": "rgba(255, 255, 255, 0.72)",
    },
    "data-panel": {
        "kind": "glass-card",
        "x": 72,
        "y": 220,
        "width": 520,
        "height": 300,
        "accent": "#60a5fa",
        "animation": "slide-right",
        "background": "rgba(255, 255, 255, 0.72)",
    },
    "bold-caption": {
        "kind": "caption-box",
        "x": 72,
        "y": 76,
        "width": 260,
        "height": 112,
        "accent": "#fb7185",
        "animation": "fade",
        "background": "rgba(0, 0, 0, 0.74)",
    },
}


GENERIC_MOTION_TEXT = {
    "click to watch",
    "end screen",
    "text here",
    "welcome to the future",
    "dynamic element",
    "liquid glass preset",
    "main idea",
    "callout",
    "key point",
}


def _derive_motion_text(prompt: str, model_text: str | None) -> str:
    raw = re.sub(r"\s+", " ", (model_text or "")).strip()
    normalized = raw.casefold().strip(" .!?:;\"'")
    if raw and normalized not in GENERIC_MOTION_TEXT:
        return raw[:90]

    quoted = re.findall(r"[\"'«“](.*?)[\"'»”]", prompt)
    for item in quoted:
        item = re.sub(r"\s+", " ", item).strip()
        if item and item.casefold() not in GENERIC_MOTION_TEXT:
            return item[:90]

    patterns = [
        r"(?:text|текст|надпис[ьюи]|напиши|с надписью)\s*[:\-]?\s*(.+)$",
        r"(?:with|с)\s+(.{4,90})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            item = re.sub(r"\s+", " ", match.group(1)).strip(" .!?:;\"'")
            if item and item.casefold() not in GENERIC_MOTION_TEXT:
                return item[:90]

    return re.sub(r"\s+", " ", prompt).strip()[:90] or "Idea"


def _has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _has_exit_drop_intent(text: str) -> bool:
    exit_words = r"(?:исчез|исчезан|выход|уход|уходит|улет|пропад|убер|exit|outro|disappear|leave|leaves)"
    drop_words = r"(?:упал|упасть|пад|рух|вниз|камн|гравитац|ускор|drop|fall|down|bottom|gravity|stone|accelerat)"
    return bool(
        re.search(exit_words + r"[\s\S]{0,140}" + drop_words, text)
        or re.search(drop_words + r"[\s\S]{0,140}" + exit_words, text)
        or re.search(r"(?:drop|fall)\s+(?:out|down)|(?:упал|падает|падение)\s+вниз", text)
    )


def _has_enter_fade_intent(text: str) -> bool:
    if re.search(r"fade\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432|\u043f\u0440\u043e\u044f\u0432", text):
        return True
    return bool(
        re.search(r"fade\s*in|фейд\s*ин|появлен[\w\s]{0,40}(?:фейд|fade|прозрач)|появ\w*|прояв\w*", text)
        or _has_any(text, ["РїРѕСЏРІР»СЏРµС‚СЃСЏ", "РјСЏРіРєРѕ РїРѕСЏРІР»СЏРµС‚СЃСЏ"])
    )


def _has_exit_fade_intent(text: str) -> bool:
    if re.search(r"fade\s*out|fadeout|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u0435\u0439\u0434\u0430\u0443\u0442|\u0438\u0441\u0447\u0435\u0437|\u043f\u0440\u043e\u043f\u0430\u0434", text):
        return True
    return bool(
        re.search(r"fade\s*out|фейд\s*аут|исчез[\w\s]{0,50}(?:фейд|fade|прозрач)|пропад[\w\s]{0,50}(?:фейд|fade)", text)
        or _has_any(text, ["С„РµР№Рґ Р°СѓС‚", "fade out"])
    )


def _has_no_exit_intent(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:no\s+exit|no\s+outro|hold|holds|stay|stays|keep\s+(?:it\s+)?(?:there|in\s+place)|without\s+exit)\b|"
            r"\u0431\u0435\u0437\s+\u0432\u044b\u0445\u043e\u0434|\u0431\u0435\u0437\s+\u0443\u0445\u043e\u0434|\u043d\u0435\s+\u0443\u0445\u043e\u0434|\u043e\u0441\u0442\u0430(?:\u0435|\u0432|\u0442)|\u0434\u0435\u0440\u0436\w*\s+(?:\u043d\u0430\s+\u043c\u0435\u0441\u0442\u0435|\u0434\u043e\s+\u043a\u043e\u043d\u0446\u0430)",
            text,
        )
    )


def _has_explicit_exit_intent(text: str) -> bool:
    if _has_no_exit_intent(text):
        return False
    return (
        _has_exit_fade_intent(text)
        or _has_exit_drop_intent(text)
        or bool(
            re.search(
                r"\b(?:exit|outro|leave|leaves|at\s+the\s+end|final|last)\b|\u0432\s*\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|\u0432\u044b\u0445\u043e\u0434|\u0443\u0445\u043e\u0434",
                text,
            )
        )
    )


def _has_enter_drop_intent(text: str) -> bool:
    if _has_exit_drop_intent(text):
        return False
    return bool(re.search(r"drop|fall|падает|падение|сверху|гравитац|bounce", text) or _has_any(text, ["РїР°РґР°РµС‚", "drop"]))


def _derive_motion_text(prompt: str, model_text: str | None) -> str:
    raw = re.sub(r"\s+", " ", (model_text or "")).strip()
    normalized = raw.casefold().strip(" .!?:;\"'")
    if raw and normalized not in GENERIC_MOTION_TEXT:
        return raw[:90]

    quoted = re.findall(r"[\"'«“](.*?)[\"'»”]", prompt)
    for item in quoted:
        item = re.sub(r"\s+", " ", item).strip()
        if item and item.casefold() not in GENERIC_MOTION_TEXT:
            return item[:90]

    patterns = [
        r"(?:text|текст|надпис[ьюи]|напиши|с надписью)\s*[:\-]?\s*(.+)$",
        r"(?:with|с)\s+(.{4,90})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            item = re.sub(r"\s+", " ", match.group(1)).strip(" .!?:;\"'")
            if item and item.casefold() not in GENERIC_MOTION_TEXT:
                return item[:90]

    return re.sub(r"\s+", " ", prompt).strip()[:90] or "Idea"


def _legacy_animation(enter_animation: str, enter_from: str) -> str:
    if enter_animation == "fade" or enter_from == "center":
        return "fade"
    if enter_from == "left":
        return "slide-right"
    if enter_from == "bottom":
        return "slide-up"
    return "slide-left"


def _motion_direction_from_prompt(prompt_lower: str) -> tuple[str, str]:
    enter_from = "right"
    exit_to = "left"
    if _has_any(prompt_lower, ["слева направо", "left to right", "left-to-right"]):
        enter_from, exit_to = "left", "right"
    elif _has_any(prompt_lower, ["справа налево", "right to left", "right-to-left"]):
        enter_from, exit_to = "right", "left"
    elif _has_any(prompt_lower, ["слева", "из левой", "из лева", "from left", "from the left"]):
        enter_from = "left"
    elif _has_any(prompt_lower, ["справа", "из правой", "из права", "from right", "from the right"]):
        enter_from = "right"
    elif _has_any(prompt_lower, ["снизу", "из ниж", "bottom"]):
        enter_from = "bottom"
    elif _has_any(prompt_lower, ["сверху", "из верх", "top"]):
        enter_from = "top"
    elif _has_any(prompt_lower, ["из центра", "центр", "center", "middle"]):
        enter_from = "center"

    if _has_any(prompt_lower, ["в правую границу", "уходит вправо", "улетает вправо", "to right", "right boundary"]):
        exit_to = "right"
    elif _has_any(prompt_lower, ["в левую границу", "уходит влево", "улетает влево", "to left", "left boundary"]):
        exit_to = "left"
    elif _has_any(prompt_lower, ["уходит вниз", "to bottom"]):
        exit_to = "bottom"
    elif _has_any(prompt_lower, ["уходит вверх", "to top"]):
        exit_to = "top"
    elif _has_any(prompt_lower, ["исчезает на месте", "fade out", "фейд аут"]):
        exit_to = "center"
    if _has_exit_drop_intent(prompt_lower):
        exit_to = "bottom"
    return enter_from, exit_to


def _motion_animation_from_prompt(prompt_lower: str, enter_from: str, exit_to: str) -> tuple[str, str, str]:
    enter_animation = "slide"
    exit_animation = "slide"
    easing = "expo"
    if _has_any(prompt_lower, ["фейд", "fade", "появляется", "мягко появляется"]):
        enter_animation = "fade"
    if _has_any(prompt_lower, ["поп", "pop", "пружин", "bounce"]):
        enter_animation = "pop"
        easing = "power"
    if _has_any(prompt_lower, ["поднимается", "rise", "всплывает"]) and enter_from == "center":
        enter_animation = "rise"
    if _has_any(prompt_lower, ["падает", "drop"]):
        enter_animation = "drop"
    if _has_any(prompt_lower, ["исчез", "убер", "фейд аут", "fade out"]):
        exit_animation = "fade"
    if _has_any(prompt_lower, ["уходит", "улетает", "выезжает", "в границу", "за границу"]):
        exit_animation = "slide"
    if exit_to == "center" and exit_animation == "slide":
        exit_animation = "fade"
    if _has_exit_drop_intent(prompt_lower):
        exit_to = "bottom"
        exit_animation = "drop"
        easing = "power"
    elif _has_exit_fade_intent(prompt_lower):
        exit_animation = "fade"
    if _has_enter_drop_intent(prompt_lower):
        enter_animation = "drop"
    elif _has_enter_fade_intent(prompt_lower):
        enter_animation = "fade"
    elif _has_exit_fade_intent(prompt_lower):
        enter_animation = "slide"
    if _has_any(prompt_lower, ["линейно", "linear"]):
        easing = "linear"
    elif _has_any(prompt_lower, ["мягко", "плавно", "smooth", "gentle"]):
        easing = "sine"
    return enter_animation, exit_animation, easing


def _legacy_motion_timing_from_prompt(prompt_lower: str, fallback_duration: float) -> tuple[float, float, float]:
    raw_duration = fallback_duration
    duration_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:s|sec|second|seconds|сек|секунд|секунды)", prompt_lower)
    if duration_match:
        raw_duration = float(duration_match.group(1).replace(",", "."))
    if raw_duration > 60:
        raw_duration = raw_duration / 1000
    duration = max(0.25, min(12.0, raw_duration))
    enter_duration = 0.48
    exit_duration = 0.36
    if _has_any(prompt_lower, ["быстро", "fast", "резко"]):
        enter_duration, exit_duration = 0.28, 0.24
    elif _has_any(prompt_lower, ["медленно", "плавно", "slow", "smooth"]):
        enter_duration, exit_duration = 0.72, 0.52
    max_each = max(0.08, duration * 0.42)
    return duration, min(enter_duration, max_each), min(exit_duration, max_each)


MOTION_TIME_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "\u043e\u0434\u0438\u043d": 1.0,
    "\u043e\u0434\u043d\u0430": 1.0,
    "\u043e\u0434\u043d\u0443": 1.0,
    "\u0434\u0432\u0430": 2.0,
    "\u0434\u0432\u0435": 2.0,
    "\u0434\u0432\u0443\u0445": 2.0,
    "\u0442\u0440\u0438": 3.0,
    "\u0442\u0440\u0435\u0445": 3.0,
    "\u0442\u0440\u0451\u0445": 3.0,
    "\u0447\u0435\u0442\u044b\u0440\u0435": 4.0,
    "\u0447\u0435\u0442\u044b\u0440\u0435\u0445": 4.0,
    "\u0447\u0435\u0442\u044b\u0440\u0451\u0445": 4.0,
    "\u043f\u044f\u0442\u044c": 5.0,
    "\u043f\u044f\u0442\u0438": 5.0,
    "\u0448\u0435\u0441\u0442\u044c": 6.0,
    "\u0448\u0435\u0441\u0442\u0438": 6.0,
    "\u0441\u0435\u043c\u044c": 7.0,
    "\u0441\u0435\u043c\u0438": 7.0,
    "\u0432\u043e\u0441\u0435\u043c\u044c": 8.0,
    "\u0432\u043e\u0441\u044c\u043c\u0438": 8.0,
    "\u0434\u0435\u0432\u044f\u0442\u044c": 9.0,
    "\u0434\u0435\u0432\u044f\u0442\u0438": 9.0,
    "\u0434\u0435\u0441\u044f\u0442\u044c": 10.0,
    "\u0434\u0435\u0441\u044f\u0442\u0438": 10.0,
}
MOTION_TIME_NUMBER_PATTERN = r"\d+(?:[\.,]\d+)?|" + "|".join(re.escape(item) for item in MOTION_TIME_WORDS)
MOTION_TIME_CAPTURE_PATTERN = f"({MOTION_TIME_NUMBER_PATTERN})" + r"\s*(?:[x\u0445]\s*)?"
MOTION_TIME_UNITS_PATTERN = r"(?:seconds?|secs?|sec|s|\u0441\u0435\u043a(?:\u0443\u043d\u0434(?:\u044b|\u0430)?|\.)?)"


def _parse_motion_time_number(raw: str) -> float | None:
    token = str(raw or "").replace(",", ".").casefold().strip()
    if token in MOTION_TIME_WORDS:
        return MOTION_TIME_WORDS[token]
    try:
        return float(token)
    except ValueError:
        return None


def _explicit_motion_duration(prompt_lower: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        compiled = pattern.replace("<num>", MOTION_TIME_CAPTURE_PATTERN).replace("<units>", MOTION_TIME_UNITS_PATTERN)
        match = re.search(compiled, prompt_lower, flags=re.IGNORECASE)
        if not match:
            continue
        value = _parse_motion_time_number(match.group(1))
        if value is not None:
            return max(0.05, float(value))
    return None


def _motion_timing_from_prompt(prompt_lower: str, fallback_duration: float) -> tuple[float, float, float]:
    raw_duration = fallback_duration
    duration_match = re.search(MOTION_TIME_CAPTURE_PATTERN + MOTION_TIME_UNITS_PATTERN, prompt_lower)
    if duration_match:
        value = _parse_motion_time_number(duration_match.group(1))
        if value is not None:
            raw_duration = value
    if raw_duration > 60:
        raw_duration = raw_duration / 1000
    duration = max(0.25, min(60.0, raw_duration))
    enter_duration = 0.48
    exit_duration = 0.36
    if _has_any(prompt_lower, ["Р±С‹СЃС‚СЂРѕ", "fast", "СЂРµР·РєРѕ"]):
        enter_duration, exit_duration = 0.28, 0.24
    elif _has_any(prompt_lower, ["РјРµРґР»РµРЅРЅРѕ", "РїР»Р°РІРЅРѕ", "slow", "smooth"]):
        enter_duration, exit_duration = 0.72, 0.52
    explicit_enter = _explicit_motion_duration(
        prompt_lower,
        [
            r"(?:fade\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432\w*|\u043f\u0440\u043e\u044f\u0432\w*)[^,.;?!\n]{0,100}(?:over|during|for|in|\u0437\u0430|\u043d\u0430\s+\u043f\u0440\u043e\u0442\u044f\u0436\u0435\u043d\u0438\u0438|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438]|\u043d\u0430)\s+<num>\s*<units>",
            r"(?:fade\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432\w*|\u043f\u0440\u043e\u044f\u0432\w*)[\s\S]{0,100}(?:\u043d\u0430\s+\u043f\u0440\u043e\u0442\u044f\u0436\u0435\u043d\u0438\u0438|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438])\s+<num>\s*<units>",
            r"(?:fade\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432\w*|\u043f\u0440\u043e\u044f\u0432\w*)[\s\S]{0,100}(?:over|during|for|in|\u0437\u0430|\u043d\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438])\s+<num>\s*<units>",
            r"(?:fade\s*in|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u044d\u0439\u0434\s*\u0438\u043d|appear|\u043f\u043e\u044f\u0432\w*|\u043f\u0440\u043e\u044f\u0432\w*)[^.?!\n]{0,80}(?:over|during|for|in|\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[еи])\s+<num>\s*<units>",
            r"(?:first|initial|opening|beginning|\u043f\u0435\u0440\u0432\w*|\u0432\u043d\u0430\u0447\u0430\u043b\w*|\u0441\u043d\u0430\u0447\u0430\u043b\w*)[\s:,-]*(?:for\s+|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[еи]\s+)?<num>\s*<units>[\s\S]{0,140}(?:fade\s*in|\u0444\u0435\u0439\u0434|\u0444\u044d\u0439\u0434|\u043f\u043e\u044f\u0432|\u043f\u0440\u043e\u044f\u0432|\u0444\u043e\u043d|\u044d\u043a\u0440\u0430\u043d)",
            r"<num>\s*<units>[\s\S]{0,100}(?:fade\s*in|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u044d\u0439\u0434\s*\u0438\u043d)",
        ],
    )
    explicit_exit = _explicit_motion_duration(
        prompt_lower,
        [
            r"(?:fade\s*out|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\s*\u0430\u0443\u0442|exit|outro|\u0443\u0445\u043e\u0434|\u0438\u0441\u0447\u0435\u0437)[^.?!\n]{0,80}(?:at\s+the\s+end|in\s+the\s+end|end|\u0432\s*\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435)?[\s:,-]*<num>\s*<units>",
            r"(?:\u0432\s*\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435)[\s\S]{0,60}<num>\s*<units>[\s\S]{0,100}(?:fade\s*out|\u0444\u0435\u0439\u0434|\u0444\u044d\u0439\u0434|\u0443\u0445\u043e\u0434|\u0438\u0441\u0447\u0435\u0437|\u043f\u0430\u0434|\u0432\u043d\u0438\u0437|drop|fall)",
            r"(?:fade\s*out|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\s*\u0430\u0443\u0442|exit|outro|\u0443\u0445\u043e\u0434|\u0438\u0441\u0447\u0435\u0437)[^.?!\n]{0,100}(?:\u043d\u0430\s+\u043f\u0440\u043e\u0442\u044f\u0436\u0435\u043d\u0438\u0438|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438])\s+<num>\s*<units>",
            r"(?:fade\s*out|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\s*\u0430\u0443\u0442|exit|outro|\u0443\u0445\u043e\u0434|\u0438\u0441\u0447\u0435\u0437)[^.?!\n]{0,80}(?:over|during|for|in|\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[еи])\s+(?:the\s+)?(?:(?:last|final)\s+|\u043f\u043e\u0441\u043b\u0435\u0434\u043d\w*\s+)?<num>\s*<units>",
            r"(?:last|final|\u043f\u043e\u0441\u043b\u0435\u0434\u043d\w*)\s+<num>\s*<units>[\s\S]{0,140}(?:fade\s*out|\u0444\u0435\u0439\u0434|\u0444\u044d\u0439\u0434|exit|outro|\u0443\u0445\u043e\u0434|\u0438\u0441\u0447\u0435\u0437)",
        ],
    )
    if explicit_enter is not None:
        enter_duration = explicit_enter
    if explicit_exit is not None:
        exit_duration = explicit_exit
    if explicit_enter is not None or explicit_exit is not None:
        duration = max(float(fallback_duration or 0.0), duration, min(60.0, enter_duration + exit_duration + 0.25))
    else:
        duration = max(duration, min(60.0, enter_duration + exit_duration + 0.25))
    return duration, min(enter_duration, duration), min(exit_duration, duration)


def fit_motion_to_canvas(spec: MotionSpec, canvas_width: int, canvas_height: int) -> MotionSpec:
    if canvas_width <= 0 or canvas_height <= 0:
        return spec

    margin = max(12, int(min(canvas_width, canvas_height) * 0.035))
    max_width = max(80, canvas_width - margin * 2)
    max_height = max(80, canvas_height - margin * 2)

    if getattr(spec, "source_type", "generated") == "figma":
        source_width = max(1, int(spec.width))
        source_height = max(1, int(spec.height))
        source_ratio = source_width / max(1, source_height)
        canvas_ratio = canvas_width / max(1, canvas_height)
        source_area = source_width * source_height
        canvas_area = max(1, canvas_width * canvas_height)
        looks_like_full_frame = abs(source_ratio - canvas_ratio) <= 0.12 and source_area >= canvas_area * 0.45
        if looks_like_full_frame:
            scale = min(canvas_width / source_width, canvas_height / source_height)
            width = max(1, int(round(source_width * scale)))
            height = max(1, int(round(source_height * scale)))
            x = int(round((canvas_width - width) / 2))
            y = int(round((canvas_height - height) / 2))
            return spec.model_copy(update={"x": x, "y": y, "width": width, "height": height})
        target_width = canvas_width * (0.82 if canvas_height > canvas_width else 0.44)
        target_height = canvas_height * 0.72
        scale = min(target_width / source_width, target_height / source_height, max_width / source_width, max_height / source_height, 1.0)
        width = max(80, int(source_width * scale))
        height = max(60, int(source_height * scale))
        x = min(max(int(spec.x), margin), max(margin, canvas_width - width - margin))
        y = min(max(int(spec.y), margin), max(margin, canvas_height - height - margin))
        return spec.model_copy(update={"x": int(x), "y": int(y), "width": int(width), "height": int(height)})

    if canvas_height > canvas_width:
        if spec.design_preset == "soft-neumorphism":
            target_width = int(canvas_width * 0.84)
            target_height = int(canvas_height * 0.18)
            preferred_y = int(canvas_height * 0.08)
            width = max(170, min(max_width, target_width, spec.width))
            height = max(92, min(max_height, target_height, spec.height))
            x = min(max(spec.x, margin), max(margin, canvas_width - width - margin))
            y = spec.y
            if y + height > canvas_height - margin or y < margin:
                y = preferred_y
        elif spec.design_preset == "creator-vibe":
            target_width = int(canvas_width * 0.82)
            target_height = int(canvas_height * 0.22)
            preferred_y = int(canvas_height * 0.08)
            width = max(120, min(max_width, target_width, spec.width))
            height = max(64, min(max_height, target_height, spec.height))
            x = min(max(spec.x, margin), max(margin, canvas_width - width - margin))
            y = spec.y
            if y + height > canvas_height - margin or y < margin:
                y = preferred_y
        elif spec.design_preset == "glass":
            target_width = int(canvas_width * 0.86)
            target_height = int(canvas_height * 0.15)
            preferred_y = int(canvas_height * 0.05)
            width = max(160, min(max_width, target_width))
            height = max(118, min(max_height, target_height))
            x = min(max(spec.x, margin), max(margin, canvas_width - width - margin))
            y = spec.y
            if y + height > canvas_height - margin or y < margin:
                y = preferred_y
        elif spec.design_preset == "bold-caption":
            target_width = int(canvas_width * 0.86)
            target_height = int(canvas_height * 0.16)
            preferred_y = int(canvas_height * 0.72)
        elif spec.design_preset == "data-panel":
            target_width = int(canvas_width * 0.84)
            target_height = int(canvas_height * 0.30)
            preferred_y = int(canvas_height * 0.12)
        else:
            target_width = int(canvas_width * 0.84)
            target_height = int(canvas_height * 0.18)
            preferred_y = int(canvas_height * 0.08)

        if spec.design_preset not in {"glass", "creator-vibe", "soft-neumorphism"}:
            scale = min(target_width / max(1, spec.width), target_height / max(1, spec.height), 1.0)
            width = max(80, int(spec.width * scale))
            height = max(80, int(spec.height * scale))
            x = min(max(spec.x, margin), max(margin, canvas_width - width - margin))
            y = spec.y
            if y + height > canvas_height - margin or y < margin:
                y = preferred_y
    else:
        if spec.design_preset == "soft-neumorphism":
            director = spec.motion_plan.get("director") if isinstance(spec.motion_plan, dict) else {}
            director_type = str(director.get("type") or "") if isinstance(director, dict) else ""
            width_fraction = 0.46 if director_type in {"callout", "quote", "lower-third"} else 0.36
            height_fraction = 0.19 if director_type in {"callout", "quote"} else 0.15
            width = max(280, min(int(canvas_width * width_fraction), spec.width, max_width))
            height = max(92, min(int(canvas_height * height_fraction), spec.height, max_height))
            x = spec.x
            y = spec.y
        elif spec.design_preset == "creator-vibe":
            width = max(120, min(int(canvas_width * 0.40), spec.width, max_width))
            height = max(64, min(int(canvas_height * 0.24), spec.height, max_height))
            x = spec.x
            y = spec.y
        elif spec.design_preset == "glass":
            director = spec.motion_plan.get("director") if isinstance(spec.motion_plan, dict) else {}
            director_type = str(director.get("type") or "") if isinstance(director, dict) else ""
            width_fraction = 0.50 if director_type == "lower-third" else 0.46 if director_type in {"callout", "quote"} else 0.34
            height_fraction = 0.18 if director_type in {"callout", "lower-third", "quote"} else 0.14
            width = max(300, min(int(canvas_width * width_fraction), spec.width, max_width))
            height = max(84, min(int(canvas_height * height_fraction), spec.height, max_height))
            x = spec.x
            y = spec.y
        elif spec.design_preset == "bold-caption":
            director = spec.motion_plan.get("director") if isinstance(spec.motion_plan, dict) else {}
            director_type = str(director.get("type") or "") if isinstance(director, dict) else ""
            width_fraction = 0.30 if director_type == "badge" else 0.36
            height_fraction = 0.13 if director_type == "badge" else 0.15
            width = max(220, min(int(canvas_width * width_fraction), spec.width, max_width))
            height = max(78, min(int(canvas_height * height_fraction), spec.height, max_height))
            x = spec.x
            y = spec.y
        elif spec.design_preset == "data-panel":
            if _style_family(spec) == "editorial-grid":
                width = max(340, min(int(canvas_width * 0.36), spec.width, max_width))
                height = max(126, min(int(canvas_height * 0.24), spec.height, max_height))
            else:
                width = max(240, min(int(canvas_width * 0.24), spec.width, max_width))
                height = max(150, min(int(canvas_height * 0.26), spec.height, max_height))
            x = spec.x
            y = spec.y
        else:
            scale = min(max_width / max(1, spec.width), max_height / max(1, spec.height), 1.0)
            width = max(80, int(spec.width * scale))
            height = max(80, int(spec.height * scale))
            x = spec.x
            y = spec.y

    prompt_lower = (spec.prompt or "").casefold()
    if spec.design_preset == "creator-vibe" and prompt_lower:
        if _has_any(prompt_lower, ["центр", "center", "middle"]):
            x = (canvas_width - width) // 2
            y = (canvas_height - height) // 2
        if _has_any(prompt_lower, ["верх", "сверху", "top"]):
            y = margin
        if _has_any(prompt_lower, ["низ", "снизу", "bottom"]):
            y = canvas_height - height - margin
        if _has_any(prompt_lower, ["справа", "право", "right"]):
            x = canvas_width - width - margin
        if _has_any(prompt_lower, ["слева", "лево", "left"]):
            x = margin

    x = min(max(x, margin), max(margin, canvas_width - width - margin))
    y = min(max(y, margin), max(margin, canvas_height - height - margin))
    return spec.model_copy(update={"x": int(x), "y": int(y), "width": int(width), "height": int(height)})


def place_motion_on_quiet_area(spec: MotionSpec, video_path: Path, scratch_dir: Path) -> MotionSpec:
    """Place a new auto layer on a visually quieter area without requiring a vision model."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    frame_path = scratch_dir / f"safe-zone-{spec.id}.jpg"
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(0.0, float(spec.start or 0.0))),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        str(frame_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        frame = Image.open(frame_path).convert("L")
    except Exception:
        return spec

    canvas_width, canvas_height = frame.size
    margin = max(12, int(min(canvas_width, canvas_height) * 0.035))
    width = min(spec.width, canvas_width - margin * 2)
    height = min(spec.height, canvas_height - margin * 2)
    if width <= 0 or height <= 0:
        return spec

    if canvas_height > canvas_width:
        xs = [margin, max(margin, (canvas_width - width) // 2), max(margin, canvas_width - width - margin)]
        ys = [
            margin,
            int(canvas_height * 0.18),
            int(canvas_height * 0.42),
            int(canvas_height * 0.64),
            canvas_height - height - margin,
        ]
    else:
        # Avoid the right third by default. In screen recordings it often contains webcam/ads/sidebar.
        xs = [
            margin,
            int(canvas_width * 0.08),
            int(canvas_width * 0.22),
            max(margin, int(canvas_width * 0.50) - width // 2),
        ]
        ys = [
            margin,
            int(canvas_height * 0.16),
            int(canvas_height * 0.36),
            int(canvas_height * 0.58),
            canvas_height - height - margin,
        ]

    best: tuple[float, int, int] | None = None
    for raw_x in xs:
        for raw_y in ys:
            x = min(max(int(raw_x), margin), max(margin, canvas_width - width - margin))
            y = min(max(int(raw_y), margin), max(margin, canvas_height - height - margin))
            crop = frame.crop((x, y, x + width, y + height)).resize((96, 96))
            edges = crop.filter(ImageFilter.FIND_EDGES)
            edge_mean = ImageStat.Stat(edges).mean[0]
            stat = ImageStat.Stat(crop)
            brightness = stat.mean[0]
            contrast = stat.stddev[0]
            score = edge_mean * 1.7 + contrast * 0.7
            if brightness > 190:
                score += (brightness - 190) * 0.45
            if canvas_width > canvas_height and x + width > canvas_width * 0.70:
                score += 80
            if canvas_width > canvas_height and y + height > canvas_height * 0.88:
                score += 18
            if canvas_height > canvas_width and y + height > canvas_height * 0.82:
                score += 22
            if best is None or score < best[0]:
                best = (score, x, y)

    if best is None:
        return spec
    return spec.model_copy(update={"x": best[1], "y": best[2], "width": int(width), "height": int(height)})


def prompt_to_motion(prompt: str, duration_hint: float, preset: str) -> MotionSpec:
    system = """Convert a motion graphics prompt into JSON.
Return JSON only.
Allowed design preset: soft-neumorphism.
Return fields when confident: text, duration, x, y, width, height, background.
The text field must be real user-facing copy from the prompt, not placeholder UI copy.
Never use: CLICK TO WATCH, END SCREEN, Text Here, Welcome to the Future, Dynamic Element, Main Idea.
"""
    user = json.dumps(
        {
            "prompt": prompt,
            "duration_hint": duration_hint,
            "design_preset": "soft-neumorphism",
        },
        ensure_ascii=False,
    )
    has_explicit_text = bool(re.search(r"[\"'«“].+?[\"'»”]", prompt))
    if has_explicit_text:
        data = {}
    else:
        try:
            data = chat_json(system, user, timeout=3)
        except OllamaError:
            data = {}

    preset = "soft-neumorphism"
    defaults = PRESET_DEFAULTS[preset]
    prompt_lower = prompt.casefold()

    duration, enter_duration, exit_duration = _motion_timing_from_prompt(
        prompt_lower,
        float(data.get("duration", duration_hint or 4.0)),
    )
    enter_from, exit_to = _motion_direction_from_prompt(prompt_lower)
    enter_animation, exit_animation, easing = _motion_animation_from_prompt(prompt_lower, enter_from, exit_to)
    if not _has_explicit_exit_intent(prompt_lower):
        exit_animation = "none"
        exit_to = "center"
    animation = _legacy_animation(enter_animation, enter_from)

    text = _derive_motion_text(prompt, data.get("text"))
    is_black = _has_any(prompt_lower, ["черн", "black", "темн", "dark"])
    is_white = _has_any(prompt_lower, ["бел", "white", "светл", "light"])
    background = "rgba(242, 241, 237, 0.98)"
    accent = "#2b8cff"

    x = int(data.get("x", defaults["x"]))
    y = int(data.get("y", defaults["y"]))
    width = int(data.get("width", defaults["width"]))
    height = int(data.get("height", defaults["height"]))
    if _has_any(prompt_lower, ["крупн", "больш", "large", "big"]):
        width = int(width * 1.2)
        height = int(height * 1.08)
    if _has_any(prompt_lower, ["маленьк", "small", "compact"]):
        width = int(width * 0.78)
        height = int(height * 0.86)

    if _has_any(prompt_lower, ["центр", "center", "middle"]):
        x, y = 420, 240
    if _has_any(prompt_lower, ["верх", "сверху", "top"]):
        y = 76
    if _has_any(prompt_lower, ["низ", "снизу", "bottom"]):
        y = 520
    if _has_any(prompt_lower, ["справа", "право", "right"]):
        x = 920
    if _has_any(prompt_lower, ["слева", "лево", "left"]):
        x = 72

    return MotionSpec(
        id=f"motion-{uuid.uuid4().hex[:8]}",
        kind="glass-card",
        design_preset=preset,
        text=text,
        start=float(data.get("start", 0.0)),
        duration=duration,
        x=x,
        y=y,
        width=max(120, min(1200, width)),
        height=max(64, min(520, height)),
        text_scale=1.0,
        accent=accent,
        background=background,
        animation=animation,
        enter_animation=enter_animation,
        exit_animation=exit_animation,
        enter_from=enter_from,
        exit_to=exit_to,
        enter_duration=enter_duration,
        exit_duration=exit_duration,
        easing=easing,
        prompt=prompt,
    )


def apply_animation_prompt(motion: MotionSpec, prompt: str) -> MotionSpec:
    prompt_lower = prompt.casefold()
    duration, enter_duration, exit_duration = _motion_timing_from_prompt(prompt_lower, float(motion.duration or 4.0))
    enter_from, exit_to = _motion_direction_from_prompt(prompt_lower)
    enter_animation, exit_animation, easing = _motion_animation_from_prompt(prompt_lower, enter_from, exit_to)
    has_explicit_exit = _has_explicit_exit_intent(prompt_lower)
    if not has_explicit_exit:
        exit_animation = "none"
        exit_to = "center"

    if _has_any(prompt_lower, ["без выхода", "не уходит", "остается", "остаться", "держится до конца", "no exit"]):
        exit_animation = "none"
        exit_to = "center"
    if _has_any(prompt_lower, ["без входа", "сразу", "статично", "no enter"]):
        enter_animation = "none"
        enter_from = "center"

    prompt_note = re.sub(r"\s+", " ", prompt).strip()
    return motion.model_copy(
        update={
            "duration": duration,
            "animation": _legacy_animation(enter_animation, enter_from),
            "enter_animation": enter_animation,
            "exit_animation": exit_animation,
            "enter_from": enter_from,
            "exit_to": exit_to,
            "enter_duration": enter_duration,
            "exit_duration": exit_duration,
            "easing": easing,
            "prompt": f"Animation prompt: {prompt_note}",
        }
    )


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text or "").splitlines():
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    if not lines:
        return [""]
    return lines


def _style_profile(spec: MotionSpec) -> dict:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    style = plan.get("style") if isinstance(plan.get("style"), dict) else {}
    return style


def _style_tokens(spec: MotionSpec) -> dict:
    style = _style_profile(spec)
    tokens = style.get("tokens") if isinstance(style.get("tokens"), dict) else {}
    return tokens


def _style_family(spec: MotionSpec) -> str:
    style = _style_profile(spec)
    tokens = _style_tokens(spec)
    return str(style.get("style_family") or tokens.get("shape_language") or "").strip()


def _safe_rgb(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(value)
    except ValueError:
        return fallback


def _draw_editorial_grid(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
    eyebrow = str(beat.get("eyebrow") or "motion beat").strip()
    label = f"{beat_id} - {eyebrow}" if beat_id else eyebrow
    accent = _safe_rgb(spec.accent, (221, 81, 61))
    variant_seed = sum(ord(ch) for ch in spec.id) % 4

    if variant_seed == 2:
        panel_fill = (246, 246, 241, 232)
        text_fill = (9, 12, 16, 255)
        guide_fill = (25, 28, 32, 86)
    elif variant_seed == 3:
        panel_fill = accent + (232,)
        text_fill = (5, 6, 8, 255)
        guide_fill = (255, 255, 255, 92)
    else:
        panel_fill = (12, 14, 15, 216)
        text_fill = (246, 247, 242, 255)
        guide_fill = (255, 255, 255, 92)

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rectangle((8, 12, spec.width - 4, spec.height - 2), fill=(0, 0, 0, 95))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    image.alpha_composite(shadow)

    draw.rectangle((0, 0, spec.width - 1, spec.height - 1), fill=panel_fill, outline=guide_fill, width=1)
    draw.rectangle((0, 0, 14, spec.height), fill=accent + (245,))

    grid_step = max(36, min(72, spec.width // 7))
    for x in range(grid_step, spec.width, grid_step):
        draw.line((x, 0, x, spec.height), fill=guide_fill, width=1)
    for y in range(grid_step, spec.height, grid_step):
        draw.line((0, y, spec.width, y), fill=guide_fill, width=1)

    mark = max(18, min(42, spec.width // 10))
    for x0, y0, sx, sy in [(10, 10, 1, 1), (spec.width - 10, 10, -1, 1), (10, spec.height - 10, 1, -1), (spec.width - 10, spec.height - 10, -1, -1)]:
        draw.line((x0, y0, x0 + sx * mark, y0), fill=(255, 255, 255, 210), width=2)
        draw.line((x0, y0, x0, y0 + sy * mark), fill=(255, 255, 255, 210), width=2)

    label_font = _font(max(10, min(17, int(spec.height * 0.10))))
    title_font = _font(max(22, min(66, int(spec.height * 0.34))))
    left = max(28, int(spec.width * 0.08))
    right_pad = max(24, int(spec.width * 0.07))
    label_color = accent + (255,) if variant_seed != 3 else (5, 6, 8, 230)
    draw.text((left, max(14, int(spec.height * 0.14))), label.upper()[:44], font=label_font, fill=label_color)

    text = str(spec.text or "").upper()
    lines = _wrap_text(draw, text, title_font, spec.width - left - right_pad)
    line_height = int(getattr(title_font, "size", 34) * 0.90)
    y = max(34, int((spec.height - min(3, len(lines)) * line_height) / 2) + 12)
    for line in lines[:3]:
        draw.text((left, y), line, font=title_font, fill=text_fill)
        y += line_height


def _draw_liquid_glass(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
    eyebrow = str(beat.get("eyebrow") or "motion beat").strip()
    accent = ImageColor.getrgb(spec.accent)
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (10, 20, spec.width - 10, spec.height - 10),
        radius=38,
        fill=(80, 170, 255, 36),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    image.alpha_composite(shadow)

    glass = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glass_draw = ImageDraw.Draw(glass)
    glass_draw.rounded_rectangle(
        (0, 0, spec.width - 1, spec.height - 1),
        radius=36,
        fill=(20, 30, 48, 132),
        outline=(255, 255, 255, 90),
        width=2,
    )
    glass_draw.rounded_rectangle(
        (18, 16, spec.width - 18, spec.height * 0.46),
        radius=28,
        fill=(255, 255, 255, 26),
    )
    image.alpha_composite(glass)

    draw.rounded_rectangle((34, 36, 48, spec.height - 36), radius=12, fill=accent + (255,))
    draw.ellipse((72, 46, 84, 58), fill=accent + (255,))

    eyebrow_font = _font(20)
    title_font = _font(42)
    detail_font = _font(22)
    eyebrow_text = f"{beat_id} · {eyebrow}" if beat_id else eyebrow
    draw.text((104, 40), eyebrow_text.upper()[:40], font=eyebrow_font, fill=(168, 221, 255, 230))

    lines = _wrap_text(draw, spec.text, title_font, spec.width - 150)
    y = 92
    for line in lines[:3]:
        draw.text((104, y), line, font=title_font, fill=(248, 250, 252, 255))
        y += 48

    chip_w, chip_h = 132, 62
    chip_x = spec.width - chip_w - 34
    draw.rounded_rectangle(
        (chip_x, 34, chip_x + chip_w, 34 + chip_h),
        radius=22,
        fill=(255, 255, 255, 28),
        outline=(255, 255, 255, 48),
        width=1,
    )
    draw.text((chip_x + 22, 48), (beat_id or "BEAT")[:6], font=_font(28), fill=(240, 249, 255, 255))
    draw.text((104, spec.height - 48), eyebrow.upper()[:32], font=detail_font, fill=(170, 185, 205, 220))


def _director_style_key(spec: MotionSpec) -> str:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    director = plan.get("director") if isinstance(plan.get("director"), dict) else {}
    return re.sub(r"\s+", "-", str(director.get("style") or "").strip().casefold())


def _director_type_key(spec: MotionSpec) -> str:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    director = plan.get("director") if isinstance(plan.get("director"), dict) else {}
    return re.sub(r"\s+", "-", str(director.get("type") or "").strip().casefold())


def _draw_glass(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    width, height = spec.width, spec.height
    style = _director_style_key(spec)
    block_type = _director_type_key(spec)
    radius = max(18, min(34, height // 3))
    accent = ImageColor.getrgb(spec.accent)
    dark = style in {"cinematic", "gaming-energy"}

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (10, 18, width - 10, height - 8),
        radius=radius,
        fill=(2, 6, 23, 150 if dark else 104),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    image.alpha_composite(shadow)

    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.rounded_rectangle((6, 8, width - 8, height - 8), radius=radius, outline=accent + (150 if dark else 112,), width=3)
    glow_draw.arc((7, 8, width - 9, height - 8), 185, 320, fill=(255, 255, 255, 128), width=3)
    glow_draw.arc((7, 8, width - 9, height - 8), 300, 20, fill=accent + (112,), width=3)
    glow = glow.filter(ImageFilter.GaussianBlur(2))
    image.alpha_composite(glow)

    glass = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glass_draw = ImageDraw.Draw(glass)
    fill = (9, 14, 27, 214) if dark else (248, 250, 252, 176)
    outline = (255, 255, 255, 74) if dark else (255, 255, 255, 210)
    glass_draw.rounded_rectangle(
        (8, 10, width - 10, height - 10),
        radius=radius,
        fill=fill,
        outline=outline,
        width=2,
    )
    glass_draw.rounded_rectangle(
        (18, 18, width - 22, max(36, int(height * 0.38))),
        radius=max(14, radius - 8),
        fill=(255, 255, 255, 24 if dark else 48),
    )
    if block_type == "lower-third":
        glass_draw.rounded_rectangle((24, height - 24, width - 26, height - 16), radius=5, fill=accent + (230,))
    else:
        glass_draw.rounded_rectangle((24, max(24, height // 4), 34, min(height - 24, int(height * 0.74))), radius=6, fill=accent + (238,))
    glass_draw.line((42, 20, width - 34, 20), fill=(255, 255, 255, 150 if not dark else 70), width=2)
    glass_draw.line((44, height - 18, width - 44, height - 18), fill=accent + (86,), width=2)
    image.alpha_composite(glass)

    if style == "editorial":
        draw.line((44, height - 34, min(width - 44, 180), height - 34), fill=accent + (255,), width=4)
        draw.line((width - 110, 34, width - 44, 34), fill=(15, 23, 42, 140), width=3)
    elif style == "gaming-energy":
        for step in range(0, 3):
            offset = step * 15
            draw.line((width - 82 - offset, 22, width - 44 - offset, height - 26), fill=accent + (120,), width=5)

    title_font = _font(max(24, min(46, int(height * (0.36 if block_type == "lower-third" else 0.34)))))
    text_left = 52 if block_type != "lower-third" else 42
    lines = _wrap_text(draw, spec.text, title_font, max(120, width - text_left - 38))
    line_height = int(getattr(title_font, "size", 28) * 1.04)
    text_block_height = min(2, len(lines)) * line_height
    y = max(22, int((height - text_block_height) / 2))
    text_color = (248, 250, 252, 252) if dark else (15, 23, 42, 252)
    for line in lines[:2]:
        draw.text((text_left, y), line, font=title_font, fill=text_color)
        y += line_height


def _draw_data_panel(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
    eyebrow = str(beat.get("eyebrow") or "timeline logic").strip()
    accent = ImageColor.getrgb(spec.accent)
    bg = Image.new("RGBA", image.size, (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    bg_draw.rounded_rectangle(
        (0, 0, spec.width - 1, spec.height - 1),
        radius=34,
        fill=(18, 28, 38, 188),
        outline=(220, 240, 255, 70),
        width=2,
    )
    bg_draw.rounded_rectangle(
        (28, 24, spec.width - 28, spec.height - 28),
        radius=28,
        fill=(255, 255, 255, 10),
    )
    bg = bg.filter(ImageFilter.GaussianBlur(1))
    image.alpha_composite(bg)

    title_font = _font(22)
    metric_font = _font(30)
    draw.ellipse((44, 46, 58, 60), fill=accent + (255,))
    header = f"{beat_id} · {eyebrow}" if beat_id else eyebrow
    draw.text((74, 38), header.upper()[:42], font=title_font, fill=(174, 225, 255, 230))

    metric_box = (spec.width - 188, 30, spec.width - 42, 98)
    draw.rounded_rectangle(metric_box, radius=20, fill=(255, 255, 255, 26), outline=(255, 255, 255, 42))
    draw.text((metric_box[0] + 28, metric_box[1] + 18), (beat_id or "B")[:6], font=metric_font, fill=(242, 248, 255, 255))

    bar_left = 68
    bar_bottom = spec.height - 134
    bar_width = 82
    bar_gap = 34
    heights = [90, 130, 170, 150, 220, 260]
    for idx, height in enumerate(heights):
        x0 = bar_left + idx * (bar_width + bar_gap)
        x1 = x0 + bar_width
        y0 = bar_bottom - height
        gradient = Image.new("RGBA", (bar_width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gradient)
        for step in range(height):
            ratio = step / max(1, height - 1)
            color = (
                int(50 + ratio * 80),
                int(120 + ratio * 90),
                int(235 + ratio * 20),
                230,
            )
            gd.line((0, height - step - 1, bar_width, height - step - 1), fill=color)
        mask = Image.new("L", (bar_width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bar_width - 1, height - 1), radius=18, fill=255)
        image.paste(gradient, (x0, y0), mask)

    points = []
    line_top = spec.height - 96
    for idx, delta in enumerate([52, 36, 26, 30, 12, 0]):
        x = bar_left + idx * (bar_width + bar_gap) + bar_width // 2
        y = line_top - delta
        points.append((x, y))
    draw.line(points, fill=(107, 192, 255, 255), width=4)

    body_font = _font(28)
    lines = _wrap_text(draw, spec.text, body_font, spec.width - 120)
    y = spec.height - 92
    for line in lines[:2]:
        draw.text((54, y), line, font=body_font, fill=(236, 244, 255, 248))
        y += 32


def _draw_bold_caption(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    accent = ImageColor.getrgb(spec.accent)
    style = _director_style_key(spec)
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((10, 14, spec.width - 8, spec.height - 4), radius=30, fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    image.alpha_composite(shadow)

    if style == "cinematic":
        outer_fill = (248, 250, 252, 230)
        inner_fill = (4, 8, 18, 232)
        text_fill = (248, 250, 252, 255)
        detail_fill = accent + (245,)
    elif style == "gaming-energy":
        outer_fill = accent + (245,)
        inner_fill = (2, 8, 15, 238)
        text_fill = (248, 255, 250, 255)
        detail_fill = accent + (255,)
    else:
        outer_fill = accent + (232,)
        inner_fill = (15, 23, 42, 230)
        text_fill = (255, 255, 255, 255)
        detail_fill = (251, 113, 133, 245)

    draw.rounded_rectangle((4, 6, spec.width - 4, spec.height - 8), radius=28, fill=outer_fill)
    draw.rounded_rectangle((18, 18, spec.width - 18, spec.height - 20), radius=22, fill=inner_fill, outline=(255, 255, 255, 76), width=2)

    if style == "gaming-energy":
        for index in range(4):
            x = spec.width - 104 + index * 16
            draw.line((x, 24, x - 28, spec.height - 26), fill=detail_fill, width=5)
        draw.rounded_rectangle((34, 32, 58, spec.height - 34), radius=8, fill=detail_fill)
    else:
        draw.rounded_rectangle((32, spec.height - 25, spec.width - 32, spec.height - 17), radius=4, fill=detail_fill)
        draw.rounded_rectangle((34, 30, 88, 38), radius=4, fill=detail_fill)

    title_font = _font(max(22, min(42, int(spec.height * 0.34))))
    text_x = 78 if style == "gaming-energy" else 34
    lines = _wrap_text(draw, spec.text, title_font, spec.width - text_x - 42)
    line_height = int(getattr(title_font, "size", 28) * 1.03)
    block_height = min(2, len(lines)) * line_height
    y = max(28, int((spec.height - block_height) / 2))
    for line in lines[:2]:
        draw.text((text_x, y), line, font=title_font, fill=text_fill)
        y += line_height


def _draw_creator_vibe(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    width, height = spec.width, spec.height
    is_dark = "0, 0, 0" in spec.background or spec.background.startswith("#000")
    fill = (0, 0, 0, 61) if is_dark else (255, 255, 255, 61)
    outline = (255, 255, 255, 20) if is_dark else (255, 255, 255, 42)
    text_color = (255, 255, 255, 236) if is_dark else (10, 10, 10, 236)
    muted_color = (255, 255, 255, 196) if is_dark else (10, 10, 10, 196)
    radius = max(24, min(34, height // 7))

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((10, 14, width - 10, height - 4), radius=radius, fill=(0, 0, 0, 14))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    image.alpha_composite(shadow)

    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=fill, outline=outline, width=1)

    text_scale = max(0.35, min(3.0, float(getattr(spec, "text_scale", 1.0) or 1.0)))
    title_font = _creator_font(max(10, min(90, int((height / 5) * text_scale))))
    small_font = _creator_font(max(9, min(72, int((height / 6) * text_scale))))
    padding_x = max(26, int(width * 0.075))
    lines = _wrap_text(draw, spec.text, title_font, max(120, width - padding_x * 2))

    max_lines = 2
    line_gap = max(20, int(getattr(title_font, "size", 28) * 0.86))
    visible_lines = lines[:max_lines]
    block_height = len(visible_lines) * line_gap
    y = int((height - block_height) / 2)
    for index, line in enumerate(visible_lines):
        font = small_font if index == 0 and len(lines) > 1 else title_font
        color = muted_color if index == 0 and len(lines) > 1 else text_color
        text_width = draw.textlength(line, font=font)
        x = int((width - text_width) / 2)
        draw.text((x, y), line, font=font, fill=color)
        y += line_gap


def _draw_soft_shadow_panel(
    image: Image.Image,
    rect: tuple[int, int, int, int],
    *,
    radius: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int] = (255, 255, 255, 170),
    inset: bool = False,
) -> None:
    width, height = image.size
    x0, y0, x1, y1 = rect

    def _safe_rect(candidate: tuple[int, int, int, int], pad: int = 4) -> tuple[int, int, int, int]:
        rx0, ry0, rx1, ry1 = candidate
        rx0 = max(pad, min(width - pad - 1, int(rx0)))
        ry0 = max(pad, min(height - pad - 1, int(ry0)))
        rx1 = max(rx0 + 1, min(width - pad, int(rx1)))
        ry1 = max(ry0 + 1, min(height - pad, int(ry1)))
        return (rx0, ry0, rx1, ry1)

    if inset:
        dark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        dark_draw = ImageDraw.Draw(dark)
        dark_draw.rounded_rectangle(rect, radius=radius, outline=(132, 132, 128, 92), width=3)
        dark = dark.filter(ImageFilter.GaussianBlur(3))
        image.alpha_composite(dark)
        light = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        light_draw = ImageDraw.Draw(light)
        light_draw.rounded_rectangle((x0 + 2, y0 + 2, x1 - 2, y1 - 2), radius=max(1, radius - 2), outline=(255, 255, 255, 190), width=2)
        image.alpha_composite(light)
    else:
        dark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        dark_draw = ImageDraw.Draw(dark)
        dark_draw.rounded_rectangle(_safe_rect((x0 + 6, y0 + 8, x1 - 4, y1 - 4)), radius=radius, fill=(75, 76, 72, 82))
        dark = dark.filter(ImageFilter.GaussianBlur(13))
        image.alpha_composite(dark)
        light = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        light_draw = ImageDraw.Draw(light)
        light_draw.rounded_rectangle(_safe_rect((x0 + 2, y0 + 2, x1 - 8, y1 - 8)), radius=max(1, radius - 2), fill=(255, 255, 255, 110))
        light = light.filter(ImageFilter.GaussianBlur(10))
        image.alpha_composite(light)

    panel = ImageDraw.Draw(image)
    panel.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=1)


def _fade_transparent_edges(image: Image.Image, edge: int = 12) -> None:
    if image.mode != "RGBA":
        return
    width, height = image.size
    edge = max(1, min(int(edge), max(1, min(width, height) // 3)))
    if width <= edge * 2 or height <= edge * 2:
        return

    alpha = image.getchannel("A")
    mask = Image.new("L", (width, height), 255)
    pixels = mask.load()
    for y in range(height):
        vy = min(y, height - 1 - y)
        if vy >= edge:
            continue
        y_value = int(255 * vy / edge)
        for x in range(width):
            vx = min(x, width - 1 - x)
            value = min(y_value, 255 if vx >= edge else int(255 * vx / edge))
            if value < 255:
                pixels[x, y] = value
    for x in range(width):
        vx = min(x, width - 1 - x)
        if vx >= edge:
            continue
        x_value = int(255 * vx / edge)
        for y in range(edge, height - edge):
            if x_value < pixels[x, y]:
                pixels[x, y] = x_value
    image.putalpha(ImageChops.multiply(alpha, mask))


def _soft_component_key_from_text(text: str, explicit: str | None = None) -> str:
    normalized = str(explicit or "").strip().casefold().replace("_", "-")
    aliases = {
        "text": "text",
        "hero": "hero",
        "title": "hero",
        "intro": "hero",
        "hook": "hero",
        "callout": "callout",
        "label": "callout",
        "badge": "callout",
        "card": "card",
        "slider": "slider",
        "progress": "slider",
        "volume": "slider",
        "meter": "slider",
        "toggle": "toggle",
        "switch": "toggle",
        "check": "check",
        "success": "check",
        "rows": "rows",
        "list": "rows",
        "table": "rows",
    }
    if normalized in aliases:
        return aliases[normalized]
    semantic = re.sub(r"\s+", " ", str(text or "")).casefold()
    if any(item in semantic for item in ("hero", "title", "intro", "opening", "hook", "заголов", "вступлен", "хук")):
        return "hero"
    if any(item in semantic for item in ("volume", "sound", "loud", "increase", "boost", "progress", "loading", "level", "meter", "громк", "звук", "прибав", "увелич", "прогресс", "загруз", "уров")):
        return "slider"
    if any(item in semantic for item in ("toggle", "switch", "on off", "turn on", "turn off", "enable", "disable", "переключ", "тумблер", "включ", "выключ")):
        return "toggle"
    if any(item in semantic for item in ("done", "complete", "ready", "success", "approved", "check", "готов", "заверш", "успеш", "галоч")):
        return "check"
    if any(item in semantic for item in ("table", "row", "list", "steps", "items", "spacing", "план", "таблиц", "спис", "строк", "пункт", "шаг")):
        return "rows"
    if any(item in semantic for item in ("hyperframes", "remotion", "code", "html", "api")):
        return "card"
    if any(item in semantic for item in ("callout", "badge", "lower third", "plate", "label", "corner", "point", "here", "this spot", "плашк", "лейбл", "угол", "сюда", "тут", "показыв")):
        return "callout"
    if any(item in semantic for item in ("input", "field", "dropdown", "select", "form", "figma", "frame", "layer", "инпут", "поле", "дропдаун", "форма", "фигм", "фрейм", "слой")):
        return "card"
    return "text"


def _soft_component_key(spec: MotionSpec) -> str:
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    director = plan.get("director") if isinstance(plan.get("director"), dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    slot = plan.get("agent_slot") if isinstance(plan.get("agent_slot"), dict) else {}
    explicit = plan.get("soft_component") or director.get("soft_component") or beat.get("soft_component") or slot.get("soft_component")
    semantic_text = " ".join(
        str(value or "")
        for value in (
            spec.text,
            spec.prompt,
            director.get("type"),
            beat.get("intent"),
            beat.get("quote"),
            beat.get("label"),
            slot.get("intent"),
            slot.get("quote"),
            slot.get("label"),
        )
    )
    return _soft_component_key_from_text(semantic_text, str(explicit or ""))


def _draw_soft_neumorphism(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    width, height = spec.width, spec.height
    accent = _safe_rgb(spec.accent, (43, 140, 255))
    ink = (24, 27, 31, 255)
    muted = (93, 96, 99, 235)
    paper = (242, 241, 237, 250)
    radius = max(18, min(36, int(min(width, height) * 0.16)))
    component = _soft_component_key(spec)
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
    eyebrow = str(beat.get("eyebrow") or "").strip()
    label = (beat_id or eyebrow).upper()[:18]

    _draw_soft_shadow_panel(
        image,
        (6, 8, width - 8, height - 10),
        radius=radius,
        fill=paper,
        outline=(255, 255, 255, 150),
    )

    pad = max(16, int(min(width, height) * 0.12))
    small_font = _font(max(11, min(20, int(height * 0.13))))
    title_font = _font(max(18, min(40, int(height * 0.21))))
    body_font = _font(max(14, min(30, int(height * 0.18))))

    if component == "hero":
        if label:
            draw.text((pad, pad - 2), label, font=small_font, fill=muted)
            for index in range(3):
                x = width - pad - 22 + index * 7
                draw.line((x, pad + index * 5, x + 15, pad + index * 5), fill=(69, 72, 74, 210), width=2)
        slot_y = pad + max(28, int(height * 0.16))
        slot_h = max(28, int(height * 0.18))
        slot_w = int((width - pad * 2) * 0.68)
        _draw_soft_shadow_panel(
            image,
            (pad, slot_y, pad + slot_w, min(height - pad - 48, slot_y + slot_h)),
            radius=max(12, radius // 2),
            fill=(236, 235, 231, 238),
            outline=(255, 255, 255, 165),
            inset=True,
        )
        draw.line((pad + 18, slot_y + slot_h // 2, pad + max(74, slot_w // 2), slot_y + slot_h // 2), fill=(128, 130, 130, 200), width=4)
        lines = _wrap_text(draw, spec.text, _font(max(24, min(58, int(height * 0.25)))), max(120, width - pad * 2))
        line_height = int(max(24, min(58, int(height * 0.25))) * 0.98)
        y = max(slot_y + slot_h + 18, int((height - min(3, len(lines)) * line_height) / 2) + 20)
        for line in lines[:3]:
            draw.text((pad, y), line, font=_font(max(24, min(58, int(height * 0.25)))), fill=ink)
            y += line_height
        return

    if component == "callout":
        lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
        line_height = int(getattr(title_font, "size", 28) * 1.0)
        y = max(pad, int((height - min(2, len(lines)) * line_height) / 2) + 4)
        for line in lines[:2]:
            draw.text((pad, y), line, font=title_font, fill=ink)
            y += line_height
        return

    if component == "text":
        lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
        line_height = int(getattr(title_font, "size", 28) * 1.02)
        y = max(pad, int((height - min(3, len(lines)) * line_height) / 2) + 8)
        for line in lines[:3]:
            draw.text((pad, y), line, font=title_font, fill=ink)
            y += line_height
        return

    if component == "card" and height >= 130:
        draw.text((pad, pad - 2), "Cards", font=small_font, fill=ink)
        for index in range(3):
            x = width - pad - 22 + index * 7
            draw.line((x, pad + index * 5, x + 15, pad + index * 5), fill=(69, 72, 74, 210), width=2)
        slot_y = pad + 32
        slot_h = max(30, int(height * 0.24))
        _draw_soft_shadow_panel(
            image,
            (pad, slot_y, width - pad, min(height - pad - 42, slot_y + slot_h)),
            radius=max(10, radius // 2),
            fill=(236, 235, 231, 240),
            outline=(255, 255, 255, 160),
            inset=True,
        )
        draw.line((pad + 18, slot_y + slot_h // 2, min(width - pad - 68, pad + 122), slot_y + slot_h // 2), fill=(128, 130, 130, 210), width=4)
        draw.ellipse((pad + 132, slot_y + slot_h // 2 - 3, pad + 138, slot_y + slot_h // 2 + 3), fill=(107, 111, 112, 200))
        text_y = max(slot_y + slot_h + 18, height - pad - int(height * 0.25))
        lines = _wrap_text(draw, spec.text, body_font, width - pad * 2 - 34)
        for line in lines[:2]:
            draw.text((pad, text_y), line, font=body_font, fill=ink)
            text_y += int(getattr(body_font, "size", 22) * 1.02)
        draw.line((width - pad - 10, height - pad - 18, width - pad - 10, height - pad + 4), fill=ink, width=2)
        draw.line((width - pad - 21, height - pad - 7, width - pad + 1, height - pad - 7), fill=ink, width=2)
        return

    if component == "toggle":
        pill_h = max(48, min(height - pad * 2, int(height * 0.44)))
        pill_y = max(pad, (height - pill_h) // 2)
        _draw_soft_shadow_panel(
            image,
            (pad, pill_y, width - pad, pill_y + pill_h),
            radius=pill_h // 2,
            fill=(242, 241, 237, 244),
            outline=(255, 255, 255, 170),
            inset=True,
        )
        knob = max(28, pill_h - 14)
        knob_x = width - pad - knob - 8
        knob_y = pill_y + (pill_h - knob) // 2
        _draw_soft_shadow_panel(
            image,
            (knob_x, knob_y, knob_x + knob, knob_y + knob),
            radius=knob // 2,
            fill=(226, 226, 223, 255),
            outline=(255, 255, 255, 150),
        )
        lines = _wrap_text(draw, spec.text, body_font, max(80, width - pad * 2 - knob - 28))
        text_y = pill_y + (pill_h - len(lines[:2]) * int(getattr(body_font, "size", 22) * 1.05)) // 2
        for line in lines[:2]:
            draw.text((pad + 20, text_y), line, font=body_font, fill=ink)
            text_y += int(getattr(body_font, "size", 22) * 1.05)
        return

    if component == "check":
        draw.text((pad, pad), label, font=small_font, fill=muted)
        title_y = pad + int(getattr(small_font, "size", 14) * 1.55)
        lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2 - 72))
        for line in lines[:2]:
            draw.text((pad, title_y), line, font=title_font, fill=ink)
            title_y += int(getattr(title_font, "size", 28) * 0.96)
        check = max(34, min(58, int(height * 0.28)))
        cx0 = width - pad - check
        cy0 = height - pad - check
        _draw_soft_shadow_panel(
            image,
            (cx0, cy0, cx0 + check, cy0 + check),
            radius=check // 2,
            fill=(230, 230, 227, 255),
            outline=(255, 255, 255, 160),
        )
        draw.line((cx0 + check * 0.27, cy0 + check * 0.54, cx0 + check * 0.43, cy0 + check * 0.70, cx0 + check * 0.75, cy0 + check * 0.34), fill=(55, 57, 58, 235), width=max(2, check // 12), joint="curve")
        return

    if component == "rows" and height >= 145:
        title_lines = _wrap_text(draw, spec.text, body_font, max(120, width - pad * 2))
        draw.text((pad, pad), title_lines[0] if title_lines else "Table row", font=body_font, fill=ink)
        row_y = pad + max(34, int(height * 0.23))
        row_h = max(28, min(48, int((height - row_y - pad) / 3)))
        for index in range(3):
            y = row_y + index * row_h
            draw.line((pad, y, width - pad, y), fill=(198, 198, 194, 140), width=1)
            icon = max(12, min(18, row_h - 14))
            icon_x = pad + 8
            icon_y = int(y + (row_h - icon) / 2)
            if index == 2:
                draw.rounded_rectangle((icon_x, icon_y, icon_x + icon, icon_y + icon), radius=4, outline=muted, width=2)
            else:
                draw.ellipse((icon_x, icon_y, icon_x + icon, icon_y + icon), fill=muted)
            label_text = title_lines[index + 1] if index + 1 < len(title_lines) else ["Table row", "Selected state", "Panel hover"][index]
            draw.text((pad + 44, y + row_h * 0.24), label_text[:26], font=small_font, fill=ink)
            knob = max(20, row_h - 14)
            _draw_soft_shadow_panel(
                image,
                (width - pad - knob, int(y + (row_h - knob) / 2), width - pad, int(y + (row_h - knob) / 2) + knob),
                radius=knob // 2,
                fill=(229, 229, 226, 255),
                outline=(255, 255, 255, 140),
            )
        return

    if component != "slider":
        draw.text((pad, pad), label, font=small_font, fill=muted)
        lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
        line_height = int(getattr(title_font, "size", 28) * 1.02)
        y = max(pad + 24, int((height - min(3, len(lines)) * line_height) / 2) + 8)
        for line in lines[:3]:
            draw.text((pad, y), line, font=title_font, fill=ink)
            y += line_height
        return

    if label:
        draw.text((pad, pad), label, font=small_font, fill=muted)
    track_y = height - pad - 28
    _draw_soft_shadow_panel(
        image,
        (pad, track_y, width - pad, track_y + 12),
        radius=6,
        fill=(232, 231, 227, 255),
        outline=(255, 255, 255, 150),
        inset=True,
    )
    fill_w = int((width - pad * 2) * 0.52)
    draw.rounded_rectangle((pad + 3, track_y + 4, pad + fill_w, track_y + 8), radius=3, fill=accent + (245,))
    knob = 30
    _draw_soft_shadow_panel(
        image,
        (pad + fill_w - knob // 2, track_y - 9, pad + fill_w + knob // 2, track_y + 21),
        radius=knob // 2,
        fill=(226, 226, 223, 255),
        outline=(255, 255, 255, 150),
    )
    lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
    y = max(pad + 26, int((height - len(lines[:2]) * int(getattr(title_font, "size", 28) * 1.0)) / 2) - 6)
    for line in lines[:2]:
        draw.text((pad, y), line, font=title_font, fill=ink)
        y += int(getattr(title_font, "size", 28) * 1.0)


def _draw_frosted_glass(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    width, height = spec.width, spec.height
    accent = _safe_rgb(spec.accent, (166, 255, 240))
    ink = (28, 32, 37, 255)
    radius = max(16, min(28, int(min(width, height) * 0.13)))
    component = _soft_component_key(spec)
    paper = (248, 249, 247, 178 if component in {"hero", "card", "rows"} else 224)
    plan = spec.motion_plan if isinstance(spec.motion_plan, dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
    eyebrow = str(beat.get("eyebrow") or "").strip()
    label = (eyebrow or beat_id or "OPTION").upper()[:28]

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((10, 12, width - 6, height - 4), radius=radius, fill=(18, 22, 28, 76))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.rounded_rectangle((3, 3, width - 14, height - 16), radius=radius, fill=(255, 255, 255, 64))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
    draw.rounded_rectangle((8, 8, width - 8, height - 8), radius=radius, fill=paper, outline=(255, 255, 255, 130), width=1)
    if component in {"hero", "card", "rows"}:
        draw.rounded_rectangle((10, 10, width - 10, max(12, int(height * 0.42))), radius=radius - 2, fill=(255, 255, 255, 38))

    pad = max(16, int(min(width, height) * 0.13))
    label_font = _font(max(9, min(14, int(height * 0.085))))
    title_font = _font(max(18, min(42, int(height * (0.22 if component in {"hero", "card", "rows"} else 0.26)))))
    body_font = _font(max(13, min(20, int(height * 0.105))))

    if component in {"hero", "card", "rows"} and height >= 170:
        draw.text((pad, pad - 3), label, font=label_font, fill=(246, 252, 251, 230))
        title_y = pad + max(18, int(height * 0.12))
        title_bottom = title_y
        lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
        for line in lines[:2]:
            draw.text((pad, title_y), line, font=title_font, fill=(245, 249, 249, 245))
            bbox = draw.textbbox((pad, title_y), line, font=title_font)
            title_bottom = max(title_bottom, bbox[3])
            title_y += int(getattr(title_font, "size", 28) * 1.02)
        code_y = max(title_bottom + 22, int(height * 0.44))
        code_h = max(62, height - code_y - max(16, int(pad * 0.55)))
        draw.rounded_rectangle((pad, code_y, width - pad, code_y + code_h), radius=max(10, radius // 2), fill=(25, 28, 33, 172), outline=(255, 255, 255, 45), width=1)
        code_lines = ["<div class=\"clip\">", "  data-start=\"1.5\"", "  data-duration=\"3.0\"", "</div>"]
        if component == "rows":
            code_lines = ["B01  planned", "B02  overlay", "B03  render"]
        y = code_y + 14
        for index, line in enumerate(code_lines[:4]):
            color = accent + (230,) if index in {1, 2} else (235, 237, 238, 210)
            draw.text((pad + 16, y), line, font=body_font, fill=color)
            y += int(getattr(body_font, "size", 16) * 1.25)
        return

    if component == "slider":
        lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
        y = max(pad, int(height * 0.32))
        for line in lines[:1]:
            draw.text((pad, y), line, font=title_font, fill=ink)
        track_y = height - pad - 18
        draw.rounded_rectangle((pad, track_y, width - pad, track_y + 7), radius=4, fill=(255, 255, 255, 88))
        fill_w = int((width - pad * 2) * 0.55)
        draw.rounded_rectangle((pad, track_y, pad + fill_w, track_y + 7), radius=4, fill=accent + (230,))
        knob = 22
        draw.ellipse((pad + fill_w - knob // 2, track_y - 8, pad + fill_w + knob // 2, track_y + 14), fill=(244, 246, 245, 240), outline=(255, 255, 255, 150))
        return

    lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))
    line_height = int(getattr(title_font, "size", 28) * 1.0)
    y = max(pad, int((height - min(2, len(lines)) * line_height) / 2) + 4)
    for line in lines[:2]:
        draw.text((pad, y), line, font=title_font, fill=ink)
        y += line_height


def _draw_warm_teal_ui(spec: MotionSpec, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    width, height = spec.width, spec.height
    accent = _safe_rgb(spec.accent, (0, 109, 107))
    ink = (24, 26, 24, 255)
    muted = (92, 87, 76, 230)
    paper = (239, 231, 215, 248)
    component = _soft_component_key(spec)
    radius = max(12, min(24, int(min(width, height) * 0.16)))
    pad = max(14, int(min(width, height) * 0.13))
    title_font = _font(max(17, min(36, int(height * (0.24 if component in {"text", "callout"} else 0.18)))))
    label_font = _font(max(9, min(13, int(height * 0.075))))
    body_font = _font(max(12, min(20, int(height * 0.105))))

    def safe_panel_rect(rect: tuple[int, int, int, int], pad_limit: int = 4) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = [int(value) for value in rect]
        x0 = max(pad_limit, min(width - pad_limit - 1, x0))
        y0 = max(pad_limit, min(height - pad_limit - 1, y0))
        x1 = max(x0 + 1, min(width - pad_limit, x1))
        y1 = max(y0 + 1, min(height - pad_limit, y1))
        return (x0, y0, x1, y1)

    def raised(rect: tuple[int, int, int, int], r: int, fill: tuple[int, int, int, int] = paper) -> None:
        rect = safe_panel_rect(rect)
        x0, y0, x1, y1 = rect

        def safe_rect(candidate: tuple[int, int, int, int], pad: int = 4) -> tuple[int, int, int, int]:
            rx0, ry0, rx1, ry1 = candidate
            rx0 = max(pad, min(width - pad - 1, int(rx0)))
            ry0 = max(pad, min(height - pad - 1, int(ry0)))
            rx1 = max(rx0 + 1, min(width - pad, int(rx1)))
            ry1 = max(ry0 + 1, min(height - pad, int(ry1)))
            return (rx0, ry0, rx1, ry1)

        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.rounded_rectangle(safe_rect((x0 + 5, y0 + 7, x1 - 4, y1 - 4)), radius=r, fill=(94, 77, 52, 58))
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
        light = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ld = ImageDraw.Draw(light)
        ld.rounded_rectangle(safe_rect((x0 + 2, y0 + 2, x1 - 8, y1 - 8)), radius=max(1, r - 2), fill=(255, 252, 242, 115))
        image.alpha_composite(light.filter(ImageFilter.GaussianBlur(10)))
        draw.rounded_rectangle(rect, radius=r, fill=fill, outline=(255, 250, 239, 155), width=1)

    def inset(rect: tuple[int, int, int, int], r: int) -> None:
        rect = safe_panel_rect(rect)
        draw.rounded_rectangle(rect, radius=r, fill=(229, 220, 204, 238), outline=(190, 178, 158, 128), width=1)
        x0, y0, x1, y1 = rect
        draw.line((x0 + r, y0 + 2, x1 - r, y0 + 2), fill=(172, 158, 135, 88), width=2)
        draw.line((x0 + r, y1 - 2, x1 - r, y1 - 2), fill=(255, 255, 248, 120), width=2)

    raised((8, 8, width - 8, height - 8), radius)

    lines = _wrap_text(draw, spec.text, title_font, max(120, width - pad * 2))

    if component == "slider":
        y = max(pad, int(height * 0.23))
        for line in lines[:1]:
            draw.text((pad, y), line, font=title_font, fill=ink)
        track_y = height - pad - 18
        inset((pad, track_y, width - pad, track_y + 8), 5)
        fill_w = int((width - pad * 2) * 0.58)
        draw.rounded_rectangle((pad, track_y + 2, pad + fill_w, track_y + 6), radius=3, fill=accent + (245,))
        knob = max(18, min(30, int(height * 0.22)))
        raised((pad + fill_w - knob // 2, track_y - knob // 2 + 4, pad + fill_w + knob // 2, track_y + knob // 2 + 4), knob // 2, (233, 225, 210, 255))
        return

    if component == "toggle":
        control_h = max(42, int(height * 0.46))
        control_y = int((height - control_h) / 2)
        inset((pad, control_y, width - pad, control_y + control_h), control_h // 2)
        fill_w = int((width - pad * 2) * 0.56)
        draw.rounded_rectangle((pad + 5, control_y + 5, pad + fill_w, control_y + control_h - 5), radius=control_h // 2, fill=accent + (245,))
        knob = control_h - 12
        raised((pad + fill_w - knob - 4, control_y + 6, pad + fill_w - 4, control_y + control_h - 6), knob // 2, (239, 231, 215, 255))
        for line in lines[:1]:
            draw.text((pad + 16, control_y + int(control_h * 0.32)), line, font=body_font, fill=(255, 255, 248, 245))
        return

    if component == "check":
        check = max(34, min(54, int(height * 0.33)))
        cx1 = width - pad
        cy0 = height - pad - check
        raised((cx1 - check, cy0, cx1, cy0 + check), check // 3, accent + (245,))
        draw.line((cx1 - check + 11, cy0 + check // 2, cx1 - check // 2, cy0 + check - 12, cx1 - 10, cy0 + 12), fill=(255, 255, 248, 255), width=max(3, check // 10), joint="curve")
        y = max(pad, int((height - len(lines[:2]) * int(getattr(title_font, "size", 28) * 1.0)) / 2) - 4)
        for line in lines[:2]:
            draw.text((pad, y), line, font=title_font, fill=ink)
            y += int(getattr(title_font, "size", 28) * 1.0)
        return

    if component in {"hero", "card", "rows"} and height >= 170:
        label = "FOUNDATION" if component == "hero" else "CONTROL"
        draw.text((pad, pad - 4), label, font=label_font, fill=muted)
        title_y = pad + max(18, int(height * 0.10))
        for line in lines[:2]:
            draw.text((pad, title_y), line, font=title_font, fill=ink)
            title_y += int(getattr(title_font, "size", 28) * 1.0)
        if component == "rows":
            row_y = max(title_y + 14, int(height * 0.45))
            row_h = max(30, min(42, int(height * 0.16)))
            for index in range(3):
                rect = (pad, row_y + index * row_h, width - pad, row_y + (index + 1) * row_h - 4)
                inset(rect, 8)
                dot_x = rect[0] + 16
                dot_y = int((rect[1] + rect[3]) / 2)
                draw.rounded_rectangle((dot_x - 5, dot_y - 5, dot_x + 5, dot_y + 5), radius=3, fill=accent + (230,) if index == 1 else (148, 142, 130, 210))
                draw.line((dot_x + 18, dot_y, rect[2] - 22, dot_y), fill=(105, 98, 87, 120), width=2)
            return
        field_y = max(title_y + 16, int(height * 0.48))
        inset((pad, field_y, width - pad, min(height - pad - 50, field_y + max(36, int(height * 0.16)))), 10)
        button_h = max(34, min(48, int(height * 0.16)))
        button_w = min(width - pad * 2, max(120, int(width * 0.34)))
        raised((pad, height - pad - button_h, pad + button_w, height - pad), button_h // 2, accent + (245,))
        draw.text((pad + 20, height - pad - button_h + int(button_h * 0.28)), "View More", font=body_font, fill=(255, 255, 248, 245))
        return

    y = max(pad, int((height - min(2, len(lines)) * int(getattr(title_font, "size", 28) * 1.0)) / 2) + 2)
    for line in lines[:2]:
        draw.text((pad, y), line, font=title_font, fill=ink)
        y += int(getattr(title_font, "size", 28) * 1.0)


def _is_root_figma_layer(layer: dict, spec: MotionSpec) -> bool:
    return str(layer.get("id") or "") == str(getattr(spec, "figma_node_id", "") or "") or (
        str(layer.get("node_type") or "").upper() == "FRAME"
        and abs(float(layer.get("x", 0) or 0)) < 0.001
        and abs(float(layer.get("y", 0) or 0)) < 0.001
    )


def _figma_layer_bounds(spec: MotionSpec, layers: list[dict]) -> tuple[float, float]:
    for layer in layers:
        if _is_root_figma_layer(layer, spec):
            width = float(layer.get("width") or 0)
            height = float(layer.get("height") or 0)
            if width > 0 and height > 0:
                return width, height
    max_x = max((float(layer.get("x", 0)) + float(layer.get("width", 0)) for layer in layers), default=float(spec.width))
    max_y = max((float(layer.get("y", 0)) + float(layer.get("height", 0)) for layer in layers), default=float(spec.height))
    return max(1.0, max_x), max(1.0, max_y)


def _draw_figma_layers(
    spec: MotionSpec,
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    project_root: Path,
    skip_ids: set[str] | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
) -> bool:
    layers = list(getattr(spec, "figma_layers", []) or [])
    if not layers:
        return False
    skip_ids = skip_ids or set()
    bounds_width, bounds_height = _figma_layer_bounds(spec, layers)
    scale_x = spec.width / bounds_width
    scale_y = spec.height / bounds_height
    text_scale = max(0.35, min(3.0, float(getattr(spec, "text_scale", 1.0) or 1.0)))
    image_mask_ids = {
        str(mask.get("id") or "")
        for layer in layers
        if layer.get("kind") == "image" and layer.get("asset_path")
        for mask in [_find_visual_mask_for_layer(layer, layers)]
        if mask and mask.get("id")
    }
    for layer_index, layer in enumerate(layers):
        if start_index is not None and layer_index < start_index:
            continue
        if end_index is not None and layer_index >= end_index:
            continue
        if layer.get("visible") is False:
            continue
        if str(layer.get("id") or "") in skip_ids:
            continue
        if layer.get("mask_role") == "visual-mask":
            continue
        if layer.get("render_cluster_source") and str(layer.get("id") or "").startswith("__frame_choreo_"):
            continue
        if str(layer.get("id") or "") in image_mask_ids:
            continue
        x = int(round(float(layer.get("x", 0)) * scale_x))
        y = int(round(float(layer.get("y", 0)) * scale_y))
        width = max(1, int(round(float(layer.get("width", 0)) * scale_x)))
        height = max(1, int(round(float(layer.get("height", 0)) * scale_y)))
        is_root_frame = _is_root_figma_layer(layer, spec)
        opacity = 1.0 if is_root_frame else max(0.0, min(1.0, float(layer.get("opacity", 1) or 1)))
        kind = layer.get("kind")
        if kind == "shape":
            fill = _parse_rgba(str(layer.get("fill") or "rgba(0,0,0,0)"))
            if is_root_frame and fill[3] > 0:
                fill = (fill[0], fill[1], fill[2], 255)
            fill = (fill[0], fill[1], fill[2], int(fill[3] * opacity))
            radius = max(0, int(round(float(layer.get("radius", 0)) * min(scale_x, scale_y))))
            stroke = _parse_rgba(str(layer.get("stroke") or "rgba(0,0,0,0)"), (0, 0, 0, 0))
            stroke = (stroke[0], stroke[1], stroke[2], int(stroke[3] * opacity))
            stroke_width = max(0, int(round(float(layer.get("stroke_weight", 0) or 0) * min(scale_x, scale_y))))
            draw.rounded_rectangle(
                (x, y, x + width, y + height),
                radius=radius,
                fill=fill,
                outline=stroke if stroke_width else None,
                width=stroke_width if stroke_width else 1,
            )
        elif kind == "text":
            text = str(layer.get("text") or "")
            if not text:
                continue
            color = _parse_rgba(str(layer.get("color") or "rgba(0,0,0,1)"))
            color = (color[0], color[1], color[2], int(color[3] * opacity))
            font_size = max(6, int(round(float(layer.get("font_size", 16) or 16) * min(scale_x, scale_y) * text_scale)))
            font = _creator_font(font_size)
            lines = _wrap_text(draw, text, font, width)
            line_height = max(font_size, int(round(float(layer.get("line_height", font_size) or font_size) * min(scale_x, scale_y) * text_scale)))
            text_y = y
            for line in lines:
                text_width = draw.textlength(line, font=font)
                align = str(layer.get("text_align") or "left")
                if align == "center":
                    text_x = x + int((width - text_width) / 2)
                elif align == "right":
                    text_x = x + int(width - text_width)
                else:
                    text_x = x
                draw.text((text_x, text_y), line, font=font, fill=color)
                text_y += line_height
        elif kind == "image" and layer.get("asset_path"):
            visual_rect = _find_visual_mask_for_layer(layer, layers) or layer
            sprite = _render_single_figma_layer(layer, visual_rect, scale_x, scale_y, project_root)
            if sprite is not None:
                image.alpha_composite(
                    sprite,
                    (
                        int(round(float(visual_rect.get("x") or 0) * scale_x)),
                        int(round(float(visual_rect.get("y") or 0) * scale_y)),
                    ),
                )
    return True


def _base_motion_state() -> dict:
    return {"x": 0.0, "y": 0.0, "scale": 1.0, "scaleX": 1.0, "scaleY": 1.0, "rotate": 0.0, "opacity": 1.0, "blur": 0.0, "brightness": 1.0}


def _motion_action_list(recipe: dict) -> list[dict]:
    if not isinstance(recipe, dict) or not isinstance(recipe.get("motion_actions"), list):
        return []
    actions = []
    for action in recipe.get("motion_actions") or []:
        if isinstance(action, dict):
            clean = dict(action)
            clean.pop("motion_actions", None)
            actions.append(clean)
    return actions


def _combine_motion_states(states: list[dict]) -> dict:
    combined = _base_motion_state()
    for state in states:
        if not isinstance(state, dict):
            continue
        combined["x"] += float(state.get("x", 0) or 0)
        combined["y"] += float(state.get("y", 0) or 0)
        combined["rotate"] += float(state.get("rotate", 0) or 0)
        combined["blur"] += float(state.get("blur", 0) or 0)
        combined["scale"] *= float(state.get("scale", 1) or 1)
        combined["scaleX"] *= float(state.get("scaleX", 1) or 1)
        combined["scaleY"] *= float(state.get("scaleY", 1) or 1)
        opacity = state.get("opacity", 1)
        combined["opacity"] *= float(1 if opacity is None else opacity)
        combined["brightness"] *= float(state.get("brightness", 1) or 1)
    return combined


def _motion_dsl_state(recipe: dict, local_time: float, total_duration: float | None = None) -> dict:
    base = _base_motion_state()
    actions = _motion_action_list(recipe)
    if actions:
        return _combine_motion_states([_motion_dsl_state(action, local_time, total_duration) for action in actions])
    dsl = recipe.get("motion_dsl") if isinstance(recipe, dict) else None
    keyframes = list((dsl or {}).get("keyframes") or [])
    if not keyframes:
        return base

    def ease(value: float, name: str = "smooth") -> float:
        t = max(0.0, min(1.0, float(value)))
        if name == "linear":
            return t
        if name == "sine":
            return 0.5 - __import__("math").cos(t * __import__("math").pi) / 2
        if name in {"power", "gravity"}:
            return 1 - (1 - t) ** 3
        if name == "expo":
            return 1 if t >= 1 else 1 - 2 ** (-10 * t)
        return t * t * (3 - 2 * t)

    def norm(frame: dict, previous: dict | None = None) -> dict:
        result = dict(base if previous is None else previous)
        for key in base:
            if key in frame:
                try:
                    result[key] = float(frame[key])
                except (TypeError, ValueError):
                    pass
        result["time"] = max(0.0, float(frame.get("time") or 0))
        result["ease"] = str(frame.get("ease") or "smooth")
        return result

    frames = sorted((item for item in keyframes if isinstance(item, dict)), key=lambda item: float(item.get("time") or 0))
    if not frames:
        return base
    if local_time <= float(frames[0].get("time") or 0):
        state = norm(frames[0])
    elif local_time >= float(frames[-1].get("time") or 0):
        state = norm(frames[-1], norm(frames[-2]) if len(frames) > 1 else None)
    else:
        previous = norm(frames[0])
        state = previous
        for frame in frames[1:]:
            current = norm(frame, previous)
            if local_time <= current["time"]:
                span = max(0.001, current["time"] - previous["time"])
                p = ease((local_time - previous["time"]) / span, current.get("ease", "smooth"))
                state = {key: previous[key] + (current[key] - previous[key]) * p for key in base}
                break
            previous = current

    import math

    for effect in list((dsl or {}).get("effects") or []):
        if not isinstance(effect, dict):
            continue
        start = float(effect.get("start") or 0)
        duration = max(0.05, float(effect.get("duration") or 1))
        if local_time < start or local_time > start + duration:
            continue
        t = local_time - start
        amp = float(effect.get("amplitude") or 0)
        freq = float(effect.get("frequency") or 1)
        wave = math.sin(t * math.pi * 2 * freq)
        if effect.get("type") == "shake":
            state["x"] += wave * amp
            state["y"] += math.cos(t * math.pi * 2 * freq * 1.13) * amp * 0.55
        elif effect.get("type") == "pulse":
            state["scale"] *= 1 + max(0, wave) * amp
        elif effect.get("type") == "float":
            state["y"] += wave * amp
        elif effect.get("type") == "wiggle":
            state["rotate"] += wave * amp
        elif effect.get("type") == "glow":
            state["brightness"] += max(0, wave) * amp
    outro = recipe.get("outro") if isinstance(recipe, dict) else None
    if isinstance(outro, dict) and total_duration is not None:
        out_duration = max(0.05, min(float(total_duration), float(outro.get("duration") or 0.35)))
        out_start = max(0.0, float(total_duration) - out_duration)
        if local_time >= out_start:
            p = ease((local_time - out_start) / out_duration, str(outro.get("ease") or "sine"))
            if outro.get("type") == "drop":
                state["y"] += float(outro.get("distance") or 180) * p
                state["opacity"] *= 1 - p
                state["blur"] += 2 * p
            else:
                state["opacity"] *= 1 - p
                if outro.get("type") == "blur-fade":
                    state["blur"] += 9 * p
    return state


def _motion_dsl_effects(recipe: dict) -> list[dict]:
    if not isinstance(recipe, dict):
        return []
    actions = _motion_action_list(recipe)
    if actions:
        effects: list[dict] = []
        for action in actions:
            effects.extend(_motion_dsl_effects(action))
        return effects
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    return [effect for effect in list(dsl.get("effects") or []) if isinstance(effect, dict)]


def _visual_effect_progress(effect: dict, local_time: float) -> tuple[float, str]:
    start = float(effect.get("start") or 0)
    duration = max(0.05, float(effect.get("duration") or 1))
    if local_time < start:
        return 0.0, "before"
    if local_time >= start + duration:
        return 1.0, "after"
    return max(0.0, min(1.0, (float(local_time) - start) / duration)), "active"


def _visual_effect_is_out(effect: dict) -> bool:
    value = str(effect.get("direction") or effect.get("mode") or "").casefold()
    return value in {"out", "exit", "hide", "disappear", "away"}


def _apply_alpha_mask(output: Image.Image, mask: Image.Image) -> Image.Image:
    result = output.convert("RGBA")
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask.resize(result.size)))
    return result


def _full_mask(size: tuple[int, int], value: int) -> Image.Image:
    return Image.new("L", size, max(0, min(255, int(value))))


def _linear_wipe_mask(size: tuple[int, int], progress: float, direction: str = "right") -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return _full_mask(size, 0)
    if progress >= 1:
        return _full_mask(size, 255)
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    direction = str(direction or "right").casefold()
    if direction in {"left", "from-right"}:
        x0 = int(round(width * (1.0 - progress)))
        draw.rectangle((x0, 0, width, height), fill=255)
    elif direction in {"up", "top", "from-bottom"}:
        y0 = int(round(height * (1.0 - progress)))
        draw.rectangle((0, y0, width, height), fill=255)
    elif direction in {"down", "bottom", "from-top"}:
        y1 = int(round(height * progress))
        draw.rectangle((0, 0, width, y1), fill=255)
    else:
        x1 = int(round(width * progress))
        draw.rectangle((0, 0, x1, height), fill=255)
    return mask


def _iris_mask(size: tuple[int, int], progress: float, effect: dict) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return _full_mask(size, 0)
    if progress >= 1:
        return _full_mask(size, 255)
    center_x = float(effect.get("centerX", effect.get("cx", 0.5)) or 0.5) * width
    center_y = float(effect.get("centerY", effect.get("cy", 0.5)) or 0.5) * height
    radius = math.hypot(max(center_x, width - center_x), max(center_y, height - center_y)) * progress
    softness = max(1.0, float(effect.get("softness") or 0.035) * max(width, height))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=255)
    if 0 < softness and radius > softness:
        outer = Image.new("L", size, 0)
        outer_draw = ImageDraw.Draw(outer)
        outer_draw.ellipse((center_x - radius - softness, center_y - radius - softness, center_x + radius + softness, center_y + radius + softness), fill=120)
        outer.paste(mask, (0, 0), mask)
        mask = outer.filter(ImageFilter.GaussianBlur(max(1.0, softness * 0.35)))
    return mask


def _typewriter_mask(size: tuple[int, int], progress: float, effect: dict) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    steps = max(2, min(240, int(float(effect.get("steps") or effect.get("characters") or 42))))
    stepped = math.floor(progress * steps) / steps
    return _linear_wipe_mask(size, stepped, str(effect.get("direction") or "right"))


def _line_reveal_mask(size: tuple[int, int], progress: float, effect: dict) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return _full_mask(size, 0)
    if progress >= 1:
        return _full_mask(size, 255)
    lines = max(1, min(24, int(float(effect.get("lines") or 4))))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    band_h = height / lines
    stagger = 0.42
    visible_span = max(0.2, 1.0 - stagger)
    for index in range(lines):
        line_start = (index / max(1, lines - 1)) * stagger if lines > 1 else 0.0
        line_progress = max(0.0, min(1.0, (progress - line_start) / visible_span))
        if line_progress <= 0:
            continue
        y0 = int(round(index * band_h))
        y1 = int(round(min(height, (index + 1) * band_h)))
        draw.rectangle((0, y0, width, y1), fill=int(round(255 * line_progress)))
    return mask


def _liquid_wipe_mask(size: tuple[int, int], progress: float, effect: dict) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return _full_mask(size, 0)
    if progress >= 1:
        return _full_mask(size, 255)
    direction = str(effect.get("direction") or "right").casefold()
    amplitude = max(2.0, float(effect.get("amplitude") or 0.055) * max(width, height))
    frequency = max(0.5, float(effect.get("frequency") or 2.1))
    seed = float(effect.get("seed") or 0.37)
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if direction in {"up", "down", "top", "bottom"}:
        points: list[tuple[int, int]] = []
        step = max(2, int(width / 120))
        for x in range(0, width + step, step):
            wave = math.sin((x / max(1, width)) * math.tau * frequency + seed) * amplitude
            y = int(round(height * (progress if direction in {"down", "bottom"} else 1 - progress) + wave))
            points.append((x, y))
        if direction in {"up", "top"}:
            polygon = [(0, height), (width, height), *reversed(points)]
        else:
            polygon = [(0, 0), (width, 0), *reversed(points)]
    else:
        points = []
        step = max(2, int(height / 120))
        for y in range(0, height + step, step):
            wave = math.sin((y / max(1, height)) * math.tau * frequency + seed) * amplitude
            x = int(round(width * (progress if direction not in {"left", "from-right"} else 1 - progress) + wave))
            points.append((x, y))
        if direction in {"left", "from-right"}:
            polygon = [(width, 0), (width, height), *reversed(points)]
        else:
            polygon = [(0, 0), *points, (0, height)]
    draw.polygon(polygon, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(max(0.5, min(6.0, amplitude * 0.08))))


def _hash01(value: int) -> float:
    value = (value ^ (value >> 16)) * 0x7FEB352D
    value = (value ^ (value >> 15)) * 0x846CA68B
    value = value ^ (value >> 16)
    return (value & 0xFFFFFFFF) / 0xFFFFFFFF


def _particle_dissolve_mask(size: tuple[int, int], progress: float, effect: dict, out: bool) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if not out and progress <= 0:
        return _full_mask(size, 0)
    if not out and progress >= 1:
        return _full_mask(size, 255)
    if out and progress <= 0:
        return _full_mask(size, 255)
    if out and progress >= 1:
        return _full_mask(size, 0)
    cells = max(8, min(80, int(float(effect.get("cells") or 28))))
    cell_size = max(2, int(math.ceil(max(width, height) / cells)))
    seed = int(float(effect.get("seed") or 173))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for y in range(0, height, cell_size):
        cy = y // cell_size
        for x in range(0, width, cell_size):
            cx = x // cell_size
            rank = _hash01((cx + 1) * 73856093 ^ (cy + 1) * 19349663 ^ seed)
            visible = rank >= progress if out else rank <= progress
            if visible:
                draw.rectangle((x, y, min(width, x + cell_size + 1), min(height, y + cell_size + 1)), fill=255)
    return mask


def _luma_wipe_mask(output: Image.Image, progress: float, out: bool) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    if not out and progress <= 0:
        return _full_mask(output.size, 0)
    if not out and progress >= 1:
        return _full_mask(output.size, 255)
    if out and progress <= 0:
        return _full_mask(output.size, 255)
    if out and progress >= 1:
        return _full_mask(output.size, 0)
    luma = output.convert("L")
    threshold = int(round(255 * progress))
    if out:
        return luma.point(lambda value: 255 if value > threshold else 0)
    return luma.point(lambda value: 255 if value <= threshold else 0)


def _soft_noise_mask(size: tuple[int, int], progress: float, effect: dict, out: bool) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if not out and progress <= 0:
        return _full_mask(size, 0)
    if not out and progress >= 1:
        return _full_mask(size, 255)
    if out and progress <= 0:
        return _full_mask(size, 255)
    if out and progress >= 1:
        return _full_mask(size, 0)
    grid_w = max(12, min(96, int(width / 6)))
    grid_h = max(12, min(96, int(height / 6)))
    seed = int(float(effect.get("seed") or 311))
    noise = Image.new("L", (grid_w, grid_h), 0)
    pixels = noise.load()
    for y in range(grid_h):
        for x in range(grid_w):
            pixels[x, y] = int(_hash01((x + 1) * 83492791 ^ (y + 1) * 2654435761 ^ seed) * 255)
    noise = noise.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(max(1.0, min(width, height) * 0.015)))
    threshold = int(round(255 * progress))
    if out:
        return noise.point(lambda value: 255 if value > threshold else 0)
    return noise.point(lambda value: 255 if value <= threshold else 0)


def _paper_tear_mask(size: tuple[int, int], progress: float, effect: dict, out: bool) -> Image.Image:
    width, height = size
    progress = max(0.0, min(1.0, progress))
    if not out and progress <= 0:
        return _full_mask(size, 0)
    if not out and progress >= 1:
        return _full_mask(size, 255)
    if out and progress <= 0:
        return _full_mask(size, 255)
    if out and progress >= 1:
        return _full_mask(size, 0)
    direction = str(effect.get("direction") or "right").casefold()
    tear_amp = max(6.0, float(effect.get("amplitude") or 0.06) * max(width, height))
    seed = int(float(effect.get("seed") or 421))
    mask = Image.new("L", size, 255 if out else 0)
    draw = ImageDraw.Draw(mask)
    if direction in {"up", "down", "top", "bottom"}:
        points: list[tuple[int, int]] = []
        step = max(3, int(width / 60))
        base_y = height * (progress if direction in {"down", "bottom"} else 1 - progress)
        for x in range(0, width + step, step):
            n = (_hash01((x + 1) * 1103515245 ^ seed) - 0.5) * tear_amp
            wave = math.sin((x / max(1, width)) * math.tau * 3.0 + seed) * tear_amp * 0.35
            points.append((x, int(round(base_y + n + wave))))
        polygon = [(0, 0), (width, 0), *reversed(points)] if direction in {"down", "bottom"} else [(0, height), (width, height), *reversed(points)]
    else:
        points = []
        step = max(3, int(height / 60))
        base_x = width * (progress if direction not in {"left", "from-right"} else 1 - progress)
        for y in range(0, height + step, step):
            n = (_hash01((y + 1) * 1103515245 ^ seed) - 0.5) * tear_amp
            wave = math.sin((y / max(1, height)) * math.tau * 3.0 + seed) * tear_amp * 0.35
            points.append((int(round(base_x + n + wave)), y))
        polygon = [(0, 0), *points, (0, height)] if direction not in {"left", "from-right"} else [(width, 0), (width, height), *reversed(points)]
    draw.polygon(polygon, fill=0 if out else 255)
    return mask


def _apply_pixelate(output: Image.Image, progress: float, out: bool) -> Image.Image:
    amount = progress if out else 1.0 - progress
    if amount <= 0.02:
        return output
    width, height = output.size
    pixel = max(2, int(round(2 + amount * min(18, max(width, height) * 0.035))))
    small = output.resize((max(1, width // pixel), max(1, height // pixel)), Image.Resampling.BILINEAR)
    return small.resize((width, height), Image.Resampling.NEAREST)


def _offset_channel(channel: Image.Image, dx: int, dy: int) -> Image.Image:
    shifted = Image.new("L", channel.size, 0)
    shifted.paste(channel, (dx, dy))
    return shifted


def _apply_glitch(output: Image.Image, progress: float, effect: dict) -> Image.Image:
    if progress <= 0.02 or progress >= 0.98:
        return output
    width, height = output.size
    amount = max(1.0, float(effect.get("amplitude") or 0.026) * max(width, height))
    wave = math.sin(progress * math.tau * 5.0)
    offset = int(round(max(1.0, amount * 0.18) * (1 if wave >= 0 else -1)))
    r, g, b, a = output.convert("RGBA").split()
    result = Image.merge("RGBA", (_offset_channel(r, offset, 0), g, _offset_channel(b, -offset, 0), a))
    bands = Image.new("RGBA", output.size, (0, 0, 0, 0))
    source = output.convert("RGBA")
    seed = int(float(effect.get("seed") or 509))
    for index in range(5):
        rank = _hash01(seed ^ index * 928371)
        y = int(rank * height)
        band_h = max(1, int(height * (0.008 + _hash01(seed ^ index * 277) * 0.018)))
        dx = int(round((_hash01(seed ^ index * 619) - 0.5) * amount))
        crop = source.crop((0, max(0, y), width, min(height, y + band_h)))
        bands.alpha_composite(crop, (dx, max(0, y)))
    return Image.alpha_composite(result, bands)


def _apply_signal_scan(output: Image.Image, progress: float, effect: dict) -> Image.Image:
    if progress <= 0.01 or progress >= 0.99:
        return output
    source = output.convert("RGBA")
    width, height = source.size
    peak = math.sin(progress * math.pi)
    strength = max(0.0, min(1.0, float(effect.get("strength") or 0.32))) * peak
    offset = max(1, int(round(max(width, height) * 0.0025 * strength)))
    r, g, b, a = source.split()
    result = Image.merge("RGBA", (_offset_channel(r, offset, 0), g, _offset_channel(b, -offset, 0), a))

    overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    scan_x = int(round((-0.12 + progress * 1.24) * width))
    band = max(10, int(width * 0.055))
    draw.rectangle((scan_x - band, 0, scan_x + band, height), fill=(255, 255, 255, int(58 * strength)))
    draw.line((scan_x, 0, scan_x, height), fill=(95, 228, 255, int(78 * strength)), width=max(1, int(width * 0.002)))
    draw.line((scan_x + max(1, band // 5), 0, scan_x + max(1, band // 5), height), fill=(255, 80, 190, int(44 * strength)), width=1)
    line_step = max(8, int(height * 0.032))
    seed = int(float(effect.get("seed") or 719))
    for y in range(0, height, line_step):
        alpha = int((10 + _hash01(seed ^ y * 31) * 18) * strength)
        draw.line((0, y, width, y), fill=(30, 160, 210, alpha), width=1)
    return Image.alpha_composite(result, overlay.filter(ImageFilter.GaussianBlur(max(0.4, width * 0.0015))))


def _apply_film_burn(output: Image.Image, progress: float, effect: dict) -> Image.Image:
    if progress <= 0.02 or progress >= 0.98:
        return output
    width, height = output.size
    peak = math.sin(progress * math.pi)
    strength = max(0.0, min(1.0, float(effect.get("strength") or 0.5))) * peak
    overlay = Image.new("RGBA", output.size, (255, 118, 44, 0))
    mask = Image.new("L", output.size, 0)
    draw = ImageDraw.Draw(mask)
    edge = int(round(width * (0.08 + progress * 0.84)))
    draw.ellipse((edge - width * 0.55, -height * 0.25, edge + width * 0.25, height * 1.2), fill=int(255 * strength))
    draw.rectangle((0, 0, int(width * 0.08 * peak), height), fill=int(180 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(max(6, int(min(width, height) * 0.04))))
    overlay.putalpha(mask)
    return Image.alpha_composite(output.convert("RGBA"), overlay)


def _apply_shimmer(output: Image.Image, progress: float, effect: dict) -> Image.Image:
    width, height = output.size
    if progress <= 0 or progress >= 1:
        return output
    band = max(12, int(width * float(effect.get("band") or 0.18)))
    center = int(round(-band + (width + band * 2) * progress))
    mask = Image.new("L", output.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(
        [
            (center - band, height),
            (center, 0),
            (center + band, 0),
            (center, height),
        ],
        fill=int(max(0.0, min(1.0, float(effect.get("strength") or 0.24))) * 255),
    )
    mask = mask.filter(ImageFilter.GaussianBlur(max(2, int(band * 0.22))))
    overlay = Image.new("RGBA", output.size, (255, 255, 255, 0))
    overlay.putalpha(mask)
    return Image.alpha_composite(output.convert("RGBA"), overlay)


def _apply_sprite_visual_effects(sprite: Image.Image, recipe: dict, local_time: float) -> Image.Image:
    effects = _motion_dsl_effects(recipe)
    if not effects:
        return sprite
    output = sprite.convert("RGBA")
    for effect in effects:
        effect_type = str(effect.get("type") or "").casefold()
        if effect_type not in {
            "venetian-blinds",
            "wipe-reveal",
            "iris-reveal",
            "radial-wipe",
            "typewriter",
            "type-on",
            "line-reveal",
            "fade-up-lines",
            "luma-wipe",
            "liquid-wipe",
            "particle-dissolve",
            "smoke-dissolve",
            "paper-tear",
            "pixelate",
            "glitch",
            "signal-scan",
            "film-burn",
            "shimmer",
        }:
            continue
        progress, phase = _visual_effect_progress(effect, local_time)
        out = _visual_effect_is_out(effect)
        if phase == "after" and not out:
            continue
        if phase == "before" and out:
            continue
        visible_progress = 1.0 - progress if out and effect_type != "particle-dissolve" else progress
        if effect_type == "shimmer":
            output = _apply_shimmer(output, progress, effect)
            continue
        if effect_type == "pixelate":
            output = _apply_pixelate(output, progress, out)
            continue
        if effect_type == "glitch":
            output = _apply_glitch(output, progress, effect)
            continue
        if effect_type == "signal-scan":
            output = _apply_signal_scan(output, progress, effect)
            continue
        if effect_type == "film-burn":
            output = _apply_film_burn(output, progress, effect)
            continue
        if effect_type == "wipe-reveal":
            mask = _linear_wipe_mask(output.size, visible_progress, str(effect.get("direction") or "right"))
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type in {"iris-reveal", "radial-wipe"}:
            mask = _iris_mask(output.size, visible_progress, effect)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type in {"typewriter", "type-on"}:
            mask = _typewriter_mask(output.size, visible_progress, effect)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type in {"line-reveal", "fade-up-lines"}:
            mask = _line_reveal_mask(output.size, visible_progress, effect)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type == "liquid-wipe":
            mask = _liquid_wipe_mask(output.size, visible_progress, effect)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type == "luma-wipe":
            mask = _luma_wipe_mask(output, progress, out)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type == "particle-dissolve":
            mask = _particle_dissolve_mask(output.size, progress, effect, out)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type == "smoke-dissolve":
            mask = _soft_noise_mask(output.size, progress, effect, out)
            output = _apply_alpha_mask(output, mask)
            continue
        if effect_type == "paper-tear":
            mask = _paper_tear_mask(output.size, visible_progress if not out else progress, effect, out)
            output = _apply_alpha_mask(output, mask)
            continue
        blades = max(2, min(64, int(float(effect.get("blades") or 12))))
        orientation = str(effect.get("orientation") or "vertical").casefold()
        mask = Image.new("L", output.size, 0)
        draw = ImageDraw.Draw(mask)
        if orientation == "horizontal":
            blade_h = output.height / blades
            for index in range(blades):
                y0 = int(round(index * blade_h))
                y1 = int(round(index * blade_h + blade_h * progress))
                if y1 > y0:
                    draw.rectangle((0, y0, output.width, min(output.height, y1)), fill=255)
        else:
            blade_w = output.width / blades
            for index in range(blades):
                x0 = int(round(index * blade_w))
                x1 = int(round(index * blade_w + blade_w * progress))
                if x1 > x0:
                    draw.rectangle((x0, 0, min(output.width, x1), output.height), fill=255)
        if 0.04 < progress < 0.98:
            shade = Image.new("RGBA", output.size, (0, 0, 0, 0))
            shade_draw = ImageDraw.Draw(shade)
            if orientation == "horizontal":
                blade_h = output.height / blades
                for index in range(blades):
                    y = int(round(index * blade_h + blade_h * progress))
                    if 0 <= y < output.height:
                        shade_draw.rectangle((0, max(0, y - 1), output.width, min(output.height, y + 2)), fill=(0, 0, 0, 46))
                        if y + 3 < output.height:
                            shade_draw.line((0, y + 3, output.width, y + 3), fill=(255, 255, 255, 42), width=1)
            else:
                blade_w = output.width / blades
                for index in range(blades):
                    x = int(round(index * blade_w + blade_w * progress))
                    if 0 <= x < output.width:
                        shade_draw.rectangle((max(0, x - 1), 0, min(output.width, x + 2), output.height), fill=(0, 0, 0, 46))
                        if x + 3 < output.width:
                            shade_draw.line((x + 3, 0, x + 3, output.height), fill=(255, 255, 255, 42), width=1)
            output = Image.alpha_composite(output, shade)
        output.putalpha(ImageChops.multiply(output.getchannel("A"), mask))
    return output


def _find_visual_mask_for_layer(layer: dict, layers: list[dict]) -> dict | None:
    if layer.get("kind") != "image":
        return None
    if layer.get("ltx_video_path"):
        return None
    explicit_mask_id = str(layer.get("visual_mask_id") or "").strip()
    if explicit_mask_id.casefold() in {"none", "null", "undefined"}:
        explicit_mask_id = ""
    lx, ly = float(layer.get("x") or 0), float(layer.get("y") or 0)
    lw, lh = float(layer.get("width") or 1), float(layer.get("height") or 1)
    layer_area = max(1.0, lw * lh)
    try:
        layer_index = layers.index(layer)
    except ValueError:
        layer_index = len(layers)

    def _has_image_between(mask_index: int) -> bool:
        if mask_index < 0 or layer_index < 0:
            return False
        return any(
            item.get("kind") == "image" and item.get("visible") is not False
            for item in layers[mask_index + 1 : layer_index]
        )

    best: tuple[int, float, float, dict] | None = None
    if explicit_mask_id:
        for index, item in enumerate(layers):
            if (
                str(item.get("id") or "") == explicit_mask_id
                and item.get("kind") == "shape"
                and item.get("visible") is not False
                and not _has_image_between(index)
            ):
                return item
    for index, item in enumerate(layers):
        if item is layer or item.get("kind") != "shape" or item.get("visible") is False:
            continue
        if index >= layer_index:
            continue
        if _has_image_between(index):
            continue
        ix, iy = float(item.get("x") or 0), float(item.get("y") or 0)
        iw, ih = float(item.get("width") or 1), float(item.get("height") or 1)
        overlap_w = max(0.0, min(lx + lw, ix + iw) - max(lx, ix))
        overlap_h = max(0.0, min(ly + lh, iy + ih) - max(ly, iy))
        overlap = overlap_w * overlap_h
        area = max(1.0, iw * ih)
        coverage = overlap / min(layer_area, area)
        layer_coverage = overlap / layer_area
        distance = layer_index - index
        if coverage > 0.62 and layer_coverage > 0.12 and area <= layer_area * 1.18:
            score = (distance, -coverage, area)
            if best is None or score < (best[0], -best[1], best[2]):
                best = (distance, coverage, area, item)
    return best[3] if best else None


def _figma_motion_skip_ids(spec: MotionSpec) -> set[str]:
    layers = list(getattr(spec, "figma_layers", []) or [])
    skip_ids: set[str] = set()
    has_whole_frame_composite = any(
        "whole-frame-composite"
        in {
            str(tag).strip().casefold()
            for tag in list((layer.get("motion_recipe") or {}).get("tags") or [])
            if str(tag).strip()
        }
        for layer in layers
        if layer.get("visible") is not False
    )
    if has_whole_frame_composite:
        return {str(layer["id"]) for layer in layers if layer.get("id")}
    for layer in layers:
        if (layer.get("choreo_static_skip") or layer.get("whole_frame_static_skip")) and layer.get("id"):
            skip_ids.add(str(layer["id"]))
        if not layer.get("motion_recipe") or layer.get("visible") is False:
            continue
        if layer.get("id"):
            skip_ids.add(str(layer["id"]))
        for child_id in list(layer.get("cluster_child_ids") or []):
            if child_id:
                skip_ids.add(str(child_id))
        mask = _find_visual_mask_for_layer(layer, layers)
        if mask and mask.get("id"):
            skip_ids.add(str(mask["id"]))
    return skip_ids


def _figma_ltx_video_layers(spec: MotionSpec, project_root: Path) -> list[tuple[dict, Path]]:
    layers = list(getattr(spec, "figma_layers", []) or [])
    result: list[tuple[dict, Path]] = []
    for layer in layers:
        if layer.get("visible") is False:
            continue
        video_path = str(layer.get("ltx_video_path") or "")
        if not video_path:
            continue
        path = project_root / video_path
        if path.exists():
            result.append((layer, path))
    return result


def _figma_ltx_video_layers_with_index(spec: MotionSpec, project_root: Path) -> list[tuple[int, dict, Path]]:
    layers = list(getattr(spec, "figma_layers", []) or [])
    result: list[tuple[int, dict, Path]] = []
    for index, layer in enumerate(layers):
        if layer.get("visible") is False:
            continue
        video_path = str(layer.get("ltx_video_path") or "")
        if not video_path:
            continue
        path = project_root / video_path
        if path.exists():
            result.append((index, layer, path))
    return result


def _figma_ltx_skip_ids(spec: MotionSpec, ltx_layers: list[tuple] | list[tuple[dict, Path]]) -> set[str]:
    layers = list(getattr(spec, "figma_layers", []) or [])
    skip_ids: set[str] = set()
    for item in ltx_layers:
        layer = item[1] if len(item) == 3 else item[0]
        if layer.get("id"):
            skip_ids.add(str(layer["id"]))
        mask = _find_visual_mask_for_layer(layer, layers)
        if mask and mask.get("id"):
            skip_ids.add(str(mask["id"]))
    return skip_ids


def _save_static_figma_segment(
    spec: MotionSpec,
    project_root: Path,
    output_path: Path,
    skip_ids: set[str],
    start_index: int | None,
    end_index: int | None,
    clip_rect: tuple[int, int, int, int] | None = None,
) -> bool:
    segment = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    drew = _draw_figma_layers(
        spec,
        segment,
        ImageDraw.Draw(segment),
        project_root,
        skip_ids=skip_ids,
        start_index=start_index,
        end_index=end_index,
    )
    if not drew:
        return False
    if clip_rect is not None:
        left, top, right, bottom = clip_rect
        left = max(0, min(segment.width, int(left)))
        top = max(0, min(segment.height, int(top)))
        right = max(left, min(segment.width, int(right)))
        bottom = max(top, min(segment.height, int(bottom)))
        if right <= left or bottom <= top:
            return False
        mask = Image.new("L", segment.size, 0)
        ImageDraw.Draw(mask).rectangle((left, top, right, bottom), fill=255)
        segment.putalpha(ImageChops.multiply(segment.getchannel("A"), mask))
    if not segment.getbbox():
        return False
    segment.save(output_path)
    return True


def _render_single_figma_layer(layer: dict, visual_rect: dict, scale_x: float, scale_y: float, project_root: Path) -> Image.Image | None:
    width = max(1, int(round(float(visual_rect.get("width") or 1) * scale_x)))
    height = max(1, int(round(float(visual_rect.get("height") or 1) * scale_y)))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    opacity = max(0.0, min(1.0, float(layer.get("opacity", 1) or 1)))
    if layer.get("asset_path"):
        asset = project_root / str(layer["asset_path"])
        if not asset.exists() and layer.get("kind") == "image":
            return None
        if asset.exists():
            with Image.open(asset).convert("RGBA") as child:
                lx = int(round((float(layer.get("x") or 0) - float(visual_rect.get("x") or 0)) * scale_x))
                ly = int(round((float(layer.get("y") or 0) - float(visual_rect.get("y") or 0)) * scale_y))
                lw = max(1, int(round(float(layer.get("width") or 1) * scale_x)))
                lh = max(1, int(round(float(layer.get("height") or 1) * scale_y)))
                if _image_asset_matches_visual_rect(layer, visual_rect, child.size):
                    lx = 0
                    ly = 0
                    lw = width
                    lh = height
                child = child.resize((lw, lh), Image.Resampling.LANCZOS)
                if opacity < 1:
                    child.putalpha(child.getchannel("A").point(lambda value: int(value * opacity)))
                canvas.alpha_composite(child, (lx, ly))
        elif layer.get("kind") == "image":
            return None
    elif layer.get("kind") == "shape":
        draw = ImageDraw.Draw(canvas)
        fill = _parse_rgba(str(layer.get("fill") or "rgba(0,0,0,0)"))
        fill = (fill[0], fill[1], fill[2], int(fill[3] * opacity))
        stroke = _parse_rgba(str(layer.get("stroke") or "rgba(0,0,0,0)"), (0, 0, 0, 0))
        stroke = (stroke[0], stroke[1], stroke[2], int(stroke[3] * opacity))
        stroke_width = max(0, int(round(float(layer.get("stroke_weight", 0) or 0) * min(scale_x, scale_y))))
        radius = max(0, int(round(float(layer.get("radius", 0) or 0) * min(scale_x, scale_y))))
        draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=fill, outline=stroke if stroke_width else None, width=stroke_width if stroke_width else 1)
    elif layer.get("kind") == "text":
        text = str(layer.get("text") or "")
        if not text:
            return None
        draw = ImageDraw.Draw(canvas)
        color = _parse_rgba(str(layer.get("color") or "rgba(0,0,0,1)"))
        color = (color[0], color[1], color[2], int(color[3] * opacity))
        font_size = max(6, int(round(float(layer.get("font_size", 16) or 16) * min(scale_x, scale_y))))
        font = _creator_font(font_size)
        line_height = max(font_size, int(round(float(layer.get("line_height", font_size) or font_size) * min(scale_x, scale_y))))
        text_y = 0
        for line in _wrap_text(draw, text, font, width):
            text_width = draw.textlength(line, font=font)
            align = str(layer.get("text_align") or "left")
            if align == "center":
                text_x = int((width - text_width) / 2)
            elif align == "right":
                text_x = int(width - text_width)
            else:
                text_x = 0
            draw.text((text_x, text_y), line, font=font, fill=color)
            text_y += line_height
    else:
        return None
    radius = max(0, int(round(float(visual_rect.get("radius", layer.get("radius") or 0) or 0) * min(scale_x, scale_y))))
    if radius:
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
        canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), mask))
    return canvas


def _image_asset_matches_visual_rect(layer: dict, visual_rect: dict, asset_size: tuple[int, int]) -> bool:
    if layer.get("kind") != "image":
        return False
    try:
        layer_x = float(layer.get("x") or 0)
        layer_y = float(layer.get("y") or 0)
        layer_w = max(1.0, float(layer.get("width") or 1))
        layer_h = max(1.0, float(layer.get("height") or 1))
        visual_x = float(visual_rect.get("x") or 0)
        visual_y = float(visual_rect.get("y") or 0)
        visual_w = max(1.0, float(visual_rect.get("width") or 1))
        visual_h = max(1.0, float(visual_rect.get("height") or 1))
    except (TypeError, ValueError):
        return False
    has_visual_mask = (
        abs(layer_x - visual_x) > 0.01
        or abs(layer_y - visual_y) > 0.01
        or abs(layer_w - visual_w) > 0.01
        or abs(layer_h - visual_h) > 0.01
    )
    if not has_visual_mask:
        return False
    asset_w, asset_h = max(1, int(asset_size[0])), max(1, int(asset_size[1]))
    visual_ratio = visual_w / visual_h
    asset_ratio = asset_w / asset_h
    ratio_delta = abs(asset_ratio - visual_ratio) / max(visual_ratio, 0.001)
    size_delta = max(abs(asset_w - visual_w) / visual_w, abs(asset_h - visual_h) / visual_h)
    return ratio_delta <= 0.04 and size_delta <= 0.12


def _has_existing_layer_asset(layer: dict, project_root: Path) -> bool:
    asset_path = str(layer.get("asset_path") or "")
    return bool(asset_path and (project_root / asset_path).exists())


def _transparent_keyed_crop(crop: Image.Image) -> Image.Image:
    crop = crop.convert("RGBA")
    if crop.width < 1 or crop.height < 1:
        return crop
    samples = [
        crop.getpixel((0, 0)),
        crop.getpixel((crop.width - 1, 0)),
        crop.getpixel((0, crop.height - 1)),
        crop.getpixel((crop.width - 1, crop.height - 1)),
    ]
    bg = tuple(int(sum(pixel[index] for pixel in samples) / len(samples)) for index in range(3))
    pixels = []
    for r, g, b, a in crop.getdata():
        distance = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])
        keyed_alpha = max(0, min(a, int((distance - 12) * 5)))
        pixels.append((r, g, b, keyed_alpha))
    crop.putdata(pixels)
    return crop


def _transparent_fill_shape_crop(crop: Image.Image, layer: dict) -> Image.Image:
    crop = crop.convert("RGBA")
    if crop.width < 1 or crop.height < 1:
        return crop
    fill = _parse_rgba(str(layer.get("fill") or "rgba(255,255,255,1)"), (255, 255, 255, 255))
    target = fill[:3]
    width, height = crop.size
    source_pixels = list(crop.getdata())
    candidate = [False] * (width * height)
    for index, (r, g, b, a) in enumerate(source_pixels):
        distance = abs(r - target[0]) + abs(g - target[1]) + abs(b - target[2])
        candidate[index] = a > 0 and distance <= 170

    border_connected = [False] * (width * height)
    stack: list[int] = []
    for x in range(width):
        for y in (0, height - 1):
            idx = y * width + x
            if candidate[idx] and not border_connected[idx]:
                border_connected[idx] = True
                stack.append(idx)
    for y in range(height):
        for x in (0, width - 1):
            idx = y * width + x
            if candidate[idx] and not border_connected[idx]:
                border_connected[idx] = True
                stack.append(idx)

    while stack:
        idx = stack.pop()
        x = idx % width
        y = idx // width
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            next_idx = ny * width + nx
            if candidate[next_idx] and not border_connected[next_idx]:
                border_connected[next_idx] = True
                stack.append(next_idx)

    output = []
    for index, (r, g, b, a) in enumerate(source_pixels):
        if not candidate[index] or border_connected[index]:
            output.append((r, g, b, 0))
            continue
        distance = abs(r - target[0]) + abs(g - target[1]) + abs(b - target[2])
        alpha = max(0, min(a, 255 - int(distance * 1.5)))
        output.append((r, g, b, alpha))
    crop.putdata(output)
    return crop


def _apply_rounded_crop_mask(crop: Image.Image, radius: float) -> Image.Image:
    if radius <= 0 or crop.width < 2 or crop.height < 2:
        return crop
    crop = crop.convert("RGBA")
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, crop.width - 1, crop.height - 1),
        radius=max(0.0, min(float(radius), min(crop.width, crop.height) / 2.0)),
        fill=255,
    )
    crop.putalpha(ImageChops.multiply(crop.getchannel("A"), mask))
    return crop


def _render_figma_layer_source_crop(
    layer: dict,
    visual_rect: dict,
    bounds_width: float,
    bounds_height: float,
    output_width: int,
    output_height: int,
    source_frame: Image.Image | None,
    key_transparent: bool = True,
    key_strategy: str = "corner",
) -> Image.Image | None:
    if source_frame is None:
        return None
    source_scale_x = source_frame.width / max(1.0, bounds_width)
    source_scale_y = source_frame.height / max(1.0, bounds_height)
    left = int(round(float(visual_rect.get("x") or 0) * source_scale_x))
    top = int(round(float(visual_rect.get("y") or 0) * source_scale_y))
    right = int(round((float(visual_rect.get("x") or 0) + float(visual_rect.get("width") or 1)) * source_scale_x))
    bottom = int(round((float(visual_rect.get("y") or 0) + float(visual_rect.get("height") or 1)) * source_scale_y))
    left = max(0, min(source_frame.width, left))
    top = max(0, min(source_frame.height, top))
    right = max(left + 1, min(source_frame.width, right))
    bottom = max(top + 1, min(source_frame.height, bottom))
    crop = source_frame.crop((left, top, right, bottom))
    if key_transparent:
        if key_strategy == "fill-shape":
            crop = _transparent_fill_shape_crop(crop, layer)
        else:
            crop = _transparent_keyed_crop(crop)
    try:
        radius = float(visual_rect.get("radius") or layer.get("radius") or 0)
    except (TypeError, ValueError):
        radius = 0.0
    if radius > 0:
        crop = _apply_rounded_crop_mask(crop, radius * min(source_scale_x, source_scale_y))
    if crop.size != (output_width, output_height):
        crop = crop.resize((output_width, output_height), Image.Resampling.LANCZOS)
    return crop


def _tight_text_visual_rect(layer: dict, visual_rect: dict, scale_x: float, scale_y: float, text_scale: float = 1.0) -> dict:
    if layer.get("kind") != "text":
        return visual_rect
    text = str(layer.get("text") or "")
    if not text:
        return visual_rect
    width = max(1, int(round(float(visual_rect.get("width") or 1) * scale_x)))
    font_size = max(6, int(round(float(layer.get("font_size", 16) or 16) * min(scale_x, scale_y) * text_scale)))
    font = _creator_font(font_size)
    line_height = max(
        font_size,
        int(round(float(layer.get("line_height", font_size) or font_size) * min(scale_x, scale_y) * text_scale)),
    )
    probe = ImageDraw.Draw(Image.new("RGBA", (width, 1), (0, 0, 0, 0)))
    lines = _wrap_text(probe, text, font, width)
    max_line_width = max((probe.textlength(line, font=font) for line in lines), default=width)
    width_pad = max(4, int(round(font_size * 0.8)))
    target_width_px = min(width, int(math.ceil(max_line_width + width_pad * 2)))
    target_width = float(visual_rect.get("width") or 1)
    if target_width_px < width * 0.96:
        target_width = max(1.0, target_width_px / max(0.001, scale_x))
    content_height = max(1, len(lines) * line_height)
    visual_height = max(1, int(round(float(visual_rect.get("height") or 1) * scale_y)))
    pad = max(2, int(round(font_size * 0.22)))
    target_height = content_height + pad
    if target_height >= visual_height:
        new_height = target_height / max(0.001, scale_y)
    else:
        new_height = min(float(visual_rect.get("height") or 1), target_height / max(0.001, scale_y))
    align = str(layer.get("text_align") or "left").casefold()
    original_width = max(1.0, float(visual_rect.get("width") or 1))
    original_x = float(visual_rect.get("x") or 0)
    if target_width >= original_width:
        new_x = original_x
    elif align == "center":
        new_x = original_x + (original_width - target_width) / 2.0
    elif align == "right":
        new_x = original_x + (original_width - target_width)
    else:
        new_x = original_x
    return {**visual_rect, "x": new_x, "width": max(1.0, target_width), "height": max(1.0, new_height)}


def _content_text_visual_rect(layer: dict, visual_rect: dict, scale_x: float, scale_y: float, text_scale: float = 1.0) -> dict:
    if layer.get("kind") != "text":
        return visual_rect
    estimated = _tight_text_visual_rect(layer, visual_rect, scale_x, scale_y, text_scale)
    try:
        base_height = float(visual_rect.get("height") or 1)
        estimated_height = float(estimated.get("height") or base_height)
    except (TypeError, ValueError):
        return visual_rect
    if estimated_height > base_height:
        height = estimated_height
    elif base_height > estimated_height * 2.0:
        height = max(1.0, estimated_height)
    else:
        height = base_height
    return {
        **visual_rect,
        "x": estimated.get("x", visual_rect.get("x")),
        "width": estimated.get("width", visual_rect.get("width")),
        "height": height,
    }


def _is_settled_motion_frame(frame: dict) -> bool:
    try:
        opacity = float(frame.get("opacity", 1) or 0)
        x = abs(float(frame.get("x", 0) or 0))
        y = abs(float(frame.get("y", 0) or 0))
        scale = abs(float(frame.get("scale", 1) or 1) - 1)
        scale_x = abs(float(frame.get("scaleX", 1) or 1) - 1)
        scale_y = abs(float(frame.get("scaleY", 1) or 1) - 1)
        rotate = abs(float(frame.get("rotate", 0) or 0))
        blur = abs(float(frame.get("blur", 0) or 0))
    except (TypeError, ValueError):
        return False
    return opacity >= 0.99 and x <= 0.01 and y <= 0.01 and scale <= 0.01 and scale_x <= 0.01 and scale_y <= 0.01 and rotate <= 0.01 and blur <= 0.01


def _recipe_first_settled_time(recipe: dict) -> float:
    if not isinstance(recipe, dict):
        return 0.0
    actions = _motion_action_list(recipe)
    if actions:
        return max((_recipe_first_settled_time(action) for action in actions), default=0.0)
    tags = recipe.get("tags") if isinstance(recipe.get("tags"), list) else []
    if recipe.get("ignore_hold_window") or "shatter-overlay" in tags:
        return 0.0
    intro = recipe.get("intro") if isinstance(recipe.get("intro"), dict) else {}
    try:
        fallback = float(intro.get("delay") or 0) + float(intro.get("duration") or 0)
    except (TypeError, ValueError):
        fallback = 0.0
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    settled_times = []
    for frame in list(dsl.get("keyframes") or []):
        if isinstance(frame, dict) and _is_settled_motion_frame(frame):
            try:
                settled_times.append(float(frame.get("time") or 0))
            except (TypeError, ValueError):
                pass
    return min(settled_times) if settled_times else fallback


def _recipe_next_motion_after_settled(recipe: dict, settled_at: float) -> float | None:
    if not isinstance(recipe, dict):
        return None
    actions = _motion_action_list(recipe)
    if actions:
        values = [
            value
            for value in (_recipe_next_motion_after_settled(action, settled_at) for action in actions)
            if value is not None
        ]
        return min(values) if values else None
    tags = recipe.get("tags") if isinstance(recipe.get("tags"), list) else []
    if recipe.get("ignore_hold_window") or "shatter-overlay" in tags:
        return None
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    frames = sorted(
        (frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)),
        key=lambda frame: float(frame.get("time") or 0),
    )
    last_settled_time: float | None = None
    for frame in frames:
        try:
            time_value = float(frame.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if time_value + 0.05 < settled_at:
            continue
        if _is_settled_motion_frame(frame):
            last_settled_time = time_value
            continue
        if last_settled_time is not None:
            return max(settled_at, last_settled_time)
    return None


def _is_internal_frame_choreography_layer(layer: dict) -> bool:
    layer_id = str(layer.get("id") or "")
    return (
        layer_id.startswith("__frame_choreo_")
        or layer.get("choreo_static_skip") is True
        or bool(layer.get("mask_role"))
        or layer.get("motion_internal") is True
    )


def _figma_exact_source_frame_valid(spec: MotionSpec) -> bool:
    if getattr(spec, "source_type", "generated") != "figma" or not getattr(spec, "figma_layers", None):
        return False
    for layer in list(spec.figma_layers or []):
        if not isinstance(layer, dict) or _is_internal_frame_choreography_layer(layer):
            continue
        if layer.get("visible") is False or layer.get("manual_transform") or layer.get("ltx_video_path"):
            return False
    return True


def _figma_has_hidden_visible_layer_override(spec: MotionSpec) -> bool:
    if getattr(spec, "source_type", "generated") != "figma" or not getattr(spec, "figma_layers", None):
        return False
    return any(
        isinstance(layer, dict)
        and not _is_internal_frame_choreography_layer(layer)
        and layer.get("visible") is False
        for layer in list(spec.figma_layers or [])
    )


def _figma_static_hold_window(spec: MotionSpec) -> tuple[float, float] | None:
    if getattr(spec, "source_type", "generated") != "figma" or not getattr(spec, "figma_layers", None) or not spec.asset_path:
        return None
    if not _figma_exact_source_frame_valid(spec):
        return None
    settled_at = 0.0
    for layer in list(spec.figma_layers or []):
        if layer.get("visible") is False:
            continue
        settled_at = max(settled_at, _recipe_first_settled_time(layer.get("motion_recipe") or {}))
    hold_end = float(spec.duration)
    for layer in list(spec.figma_layers or []):
        if layer.get("visible") is False:
            continue
        next_motion = _recipe_next_motion_after_settled(layer.get("motion_recipe") or {}, settled_at)
        if next_motion is not None:
            hold_end = min(hold_end, next_motion)
    if hold_end - settled_at <= 0.1:
        return None
    return settled_at, hold_end


def _figma_motion_render_order(layer: dict, spec: MotionSpec, fallback_index: int) -> tuple[int, int]:
    recipe = layer.get("motion_recipe") or {}
    tags = {
        str(tag).strip().casefold()
        for tag in list(recipe.get("tags") or [])
        if str(tag).strip()
    }
    if "shatter-overlay" in tags:
        return 30, fallback_index
    if "white-intro" in tags or str(layer.get("id") or "").startswith("__frame_choreo_white_bg"):
        return 0, fallback_index
    if "background" in tags or _is_root_figma_layer(layer, spec):
        return 5, fallback_index
    return 10, fallback_index


def _figma_scene_camera_recipe(layers: list[dict]) -> dict | None:
    for layer in layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        camera = phase_plan.get("camera") if isinstance(phase_plan, dict) and isinstance(phase_plan.get("camera"), dict) else None
        dsl = camera.get("motion_dsl") if isinstance(camera, dict) and isinstance(camera.get("motion_dsl"), dict) else None
        if dsl and isinstance(dsl.get("keyframes"), list) and dsl.get("keyframes"):
            return {
                "id": str(camera.get("id") or "scene-camera"),
                "label": str(camera.get("label") or "Scene camera"),
                "motion_dsl": dsl,
                "tags": ["scene-camera", "post-composite"],
            }
    return None


def _figma_scene_camera_uses_exact_source(layers: list[dict]) -> bool:
    for layer in layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        for phase in list((phase_plan or {}).get("phases") or []):
            if isinstance(phase, dict) and phase.get("visibility") == "exact-source-frame":
                return True
    return False


def _figma_declared_exact_hold_window(spec: MotionSpec) -> tuple[float, float] | None:
    candidates: list[dict] = []
    plan = getattr(spec, "motion_plan", None)
    if isinstance(plan, dict):
        candidates.append(plan)
    for layer in list(getattr(spec, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        if phase_plan:
            candidates.append(phase_plan)
    for candidate in candidates:
        for phase in list(candidate.get("phases") or []):
            if not isinstance(phase, dict):
                continue
            phase_id = str(phase.get("id") or "")
            preset = str(phase.get("preset") or "")
            visibility = str(phase.get("visibility") or "")
            if visibility != "exact-source-frame" and not (phase_id == "hold" and preset == "static"):
                continue
            try:
                start = max(0.0, float(phase.get("start") or 0))
                duration = max(0.0, float(phase.get("duration") or 0))
            except (TypeError, ValueError):
                continue
            if duration > 0.1:
                return start, start + duration
    return None


def _mean_abs_rgb_diff(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def _edge_score(image: Image.Image) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return float(stat.mean[0]) if stat.mean else 0.0


def _median(values: list[float]) -> float:
    clean = sorted(float(value) for value in values if value == value)
    if not clean:
        return 0.0
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2.0


def _frame_index_for_time(local_time: float, fps: int, frame_count: int) -> int:
    return max(0, min(max(0, frame_count - 1), int(round(max(0.0, local_time) * fps))))


def _hold_sample_time(hold_window: tuple[float, float], duration: float, fps: int) -> float:
    start, end = hold_window
    frame_step = 1.0 / max(1, fps)
    safe_end = max(start, min(duration, end) - frame_step)
    return max(start, min(safe_end, start + (max(0.0, end - start) * 0.5)))


def _read_frame_at(frames_dir: Path, local_time: float, fps: int, frame_count: int) -> Image.Image | None:
    frame_index = _frame_index_for_time(local_time, fps, frame_count)
    frame_path = frames_dir / f"frame_{frame_index:05d}.png"
    if not frame_path.exists():
        return None
    return Image.open(frame_path).convert("RGB")


def _crop_rect(image: Image.Image, rect: dict[str, float]) -> Image.Image | None:
    left = max(0, int(round(float(rect.get("x") or 0))))
    top = max(0, int(round(float(rect.get("y") or 0))))
    right = min(image.width, int(round(left + float(rect.get("width") or 1))))
    bottom = min(image.height, int(round(top + float(rect.get("height") or 1))))
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom))


def _vertical_blinds_score(image: Image.Image) -> float:
    sample = image.convert("L").resize((240, 135), Image.Resampling.LANCZOS)
    values: list[float] = []
    for x in range(sample.width):
        total = 0
        for y in range(sample.height):
            total += sample.getpixel((x, y))
        values.append(total / max(1, sample.height))
    mean = sum(values) / max(1, len(values))
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values))
    return variance ** 0.5


def _phase_start_duration(phase: dict) -> tuple[float, float]:
    try:
        start = max(0.0, float(phase.get("start") or 0))
    except (TypeError, ValueError):
        start = 0.0
    try:
        duration = max(0.0, float(phase.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0.0
    return start, duration


def _phase_end(phase: dict) -> float:
    start, duration = _phase_start_duration(phase)
    try:
        return max(start, float(phase.get("end") or start + duration))
    except (TypeError, ValueError):
        return start + duration


def _figma_motion_phase_plan(spec: MotionSpec) -> dict | None:
    plan = getattr(spec, "motion_plan", None)
    if isinstance(plan, dict) and isinstance(plan.get("phases"), list):
        return plan
    for layer in list(getattr(spec, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        if isinstance(phase_plan, dict) and isinstance(phase_plan.get("phases"), list):
            return phase_plan
    return None


def _flatten_motion_phases(plan: dict | None) -> list[dict]:
    if not isinstance(plan, dict):
        return []
    phases: list[dict] = []
    for phase in list(plan.get("phases") or []):
        if not isinstance(phase, dict):
            continue
        phases.append(dict(phase))
        for subphase in list(phase.get("subphases") or []):
            if isinstance(subphase, dict):
                phases.append({**dict(subphase), "parent_phase_id": phase.get("id")})
    return phases


def _phase_with_preset(plan: dict | None, presets: set[str]) -> dict | None:
    for phase in _flatten_motion_phases(plan):
        if str(phase.get("preset") or "") in presets or str(phase.get("id") or "") in presets:
            return phase
    return None


def _layer_rect_pixels(spec: MotionSpec, layer: dict, render_width: int, render_height: int) -> dict[str, float] | None:
    layers = list(getattr(spec, "figma_layers", []) or [])
    bounds_width, bounds_height = _figma_layer_bounds(spec, layers)
    if bounds_width <= 0 or bounds_height <= 0:
        return None
    scale_x = render_width / bounds_width
    scale_y = render_height / bounds_height
    visual_rect = _find_visual_mask_for_layer(layer, layers) or layer
    if layer.get("kind") == "text":
        visual_rect = _content_text_visual_rect(layer, visual_rect, scale_x, scale_y)
    return {
        "x": float(visual_rect.get("x") or 0) * scale_x,
        "y": float(visual_rect.get("y") or 0) * scale_y,
        "width": max(1.0, float(visual_rect.get("width") or 1) * scale_x),
        "height": max(1.0, float(visual_rect.get("height") or 1) * scale_y),
    }


def _layers_with_recipe_tag(spec: MotionSpec, tag: str, limit: int = 8) -> list[dict]:
    result: list[dict] = []
    for layer in list(getattr(spec, "figma_layers", []) or []):
        if not isinstance(layer, dict) or layer.get("visible") is False or _is_root_figma_layer(layer, spec):
            continue
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else {}
        tags = {str(item).strip().casefold() for item in list(recipe.get("tags") or []) if str(item).strip()}
        if tag in tags:
            result.append(layer)
    return sorted(result, key=lambda item: float(item.get("width") or 1) * float(item.get("height") or 1), reverse=True)[:limit]


def _layers_with_any_recipe_tags(spec: MotionSpec, tags: tuple[str, ...], limit: int = 8) -> list[dict]:
    requested = {str(tag).strip().casefold() for tag in tags if str(tag).strip()}
    result: list[dict] = []
    seen: set[str] = set()
    for tag in requested:
        for layer in _layers_with_recipe_tag(spec, tag, limit=limit):
            layer_id = str(layer.get("id") or "")
            if layer_id in seen:
                continue
            seen.add(layer_id)
            result.append(layer)
    return sorted(result, key=lambda item: float(item.get("width") or 1) * float(item.get("height") or 1), reverse=True)[:limit]


def _layer_diff_scores(
    spec: MotionSpec,
    source_frame: Image.Image,
    sample_frame: Image.Image,
    layers: list[dict],
) -> list[float]:
    scores: list[float] = []
    for layer in layers:
        rect = _layer_rect_pixels(spec, layer, source_frame.width, source_frame.height)
        if not rect:
            continue
        source_crop = _crop_rect(source_frame.convert("RGB"), rect)
        sample_crop = _crop_rect(sample_frame.convert("RGB"), rect)
        if source_crop is None or sample_crop is None:
            continue
        scores.append(_mean_abs_rgb_diff(sample_crop, source_crop))
    return scores


def _motion_prompt_execution_audit(
    spec: MotionSpec,
    frames_dir: Path,
    source_frame: Image.Image | None,
    fps: int,
    duration: float,
    frame_count: int,
    hold_window: tuple[float, float] | None,
) -> dict[str, object]:
    plan = _figma_motion_phase_plan(spec)
    phases = _flatten_motion_phases(plan)
    checks: list[dict[str, object]] = []
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {}

    def add(check_id: str, status: str, detail: str, **extra: object) -> None:
        checks.append({"id": check_id, "status": status, "detail": detail, **extra})
        if status == "fail":
            errors.append(detail)

    if not phases or source_frame is None:
        return {
            "status": "pending",
            "checks": checks,
            "metrics": metrics,
            "errors": errors,
            "warnings": ["prompt execution audit needs whole-frame phases and source Figma PNG"],
        }

    intro = _phase_with_preset(plan, {"venetian-blinds-bg"})
    if intro:
        intro_start, intro_duration = _phase_start_duration(intro)
        sample_time = intro_start + max(0.04, intro_duration * 0.5)
        sample = _read_frame_at(frames_dir, sample_time, fps, frame_count)
        if sample is None:
            add("venetian_blinds_visual", "fail", "Venetian Blinds sample frame is missing.")
        else:
            stripe_score = _vertical_blinds_score(sample)
            metrics["venetian_blinds_score"] = round(stripe_score, 4)
            add(
                "venetian_blinds_visual",
                "pass" if stripe_score >= 1.2 else "fail",
                f"Venetian Blinds stripe score {stripe_score:.3f}.",
            )

    modern_intro = _phase_with_preset(plan, {"signal-scan-reveal", "glass-light-sweep", "soft-pixel-snap", "glitch-bg-fade"})
    if modern_intro:
        intro_start, intro_duration = _phase_start_duration(modern_intro)
        sample_time = intro_start + max(0.04, intro_duration * 0.45)
        sample = _read_frame_at(frames_dir, sample_time, fps, frame_count)
        if sample is None:
            add("modern_intro_visual", "fail", "Modern intro template sample frame is missing.")
        else:
            intro_diff = _mean_abs_rgb_diff(sample, source_frame)
            metrics["modern_intro_vs_source_diff"] = round(intro_diff, 4)
            add(
                "modern_intro_visual",
                "pass" if intro_diff >= 8.0 else "fail",
                f"Modern intro differs from settled source: diff {intro_diff:.3f}.",
            )

    hold_sample = None
    if hold_window:
        hold_sample = _read_frame_at(frames_dir, _hold_sample_time(hold_window, duration, fps), fps, frame_count)
    build = _phase_with_preset(plan, {"advanced-composition-build", "random-fly-in-stagger"})
    if build and hold_sample is not None:
        build_start, build_duration = _phase_start_duration(build)
        build_mid = _read_frame_at(frames_dir, build_start + max(0.04, build_duration * 0.5), fps, frame_count)
        build_end = _read_frame_at(frames_dir, min(duration, _phase_end(build) + 0.04), fps, frame_count)
        if build_mid is None or build_end is None:
            add("appearance_progression", "fail", "Build phase sample frames are missing.")
        else:
            mid_diff = _mean_abs_rgb_diff(build_mid, source_frame)
            end_diff = _mean_abs_rgb_diff(build_end, source_frame)
            hold_diff = _mean_abs_rgb_diff(hold_sample, source_frame)
            metrics["build_mid_vs_source_diff"] = round(mid_diff, 4)
            metrics["build_end_vs_source_diff"] = round(end_diff, 4)
            add(
                "appearance_progression",
                "pass" if mid_diff > max(3.0, hold_diff + 1.0) and end_diff <= max(12.0, hold_diff + 10.0) else "fail",
                f"Build moves toward source: mid diff {mid_diff:.3f}, end diff {end_diff:.3f}.",
            )

    role_checks = [
        ("photo_parallax_visual", {"parallax-photo"}, ("photo-phase",)),
        ("text_fade_up_lines_visual", {"fade-up-lines", "text-slide-up-lines"}, ("text-phase",)),
        ("button_y_rise_visual", {"button-y-rise"}, ("button-phase",)),
        ("tetris_build_visual", {"tetris-build"}, ("tetris-build", "element-phase", "photo-phase", "button-phase")),
    ]
    for check_id, presets, tags in role_checks:
        phase = _phase_with_preset(plan, presets)
        layers = _layers_with_any_recipe_tags(spec, tags)
        if not phase or not layers or hold_sample is None:
            if phase:
                warnings.append(f"{check_id}: no measurable tagged layers")
            continue
        phase_start, phase_duration = _phase_start_duration(phase)
        early_time = phase_start + max(0.03, phase_duration * 0.25)
        early_frame = _read_frame_at(frames_dir, early_time, fps, frame_count)
        if early_frame is None:
            add(check_id, "fail", f"{check_id} sample frame is missing.")
            continue
        early_scores = _layer_diff_scores(spec, source_frame, early_frame, layers)
        hold_scores = _layer_diff_scores(spec, source_frame, hold_sample, layers)
        early_median = _median(early_scores)
        hold_median = _median(hold_scores)
        metrics[f"{check_id}_early_diff"] = round(early_median, 4)
        metrics[f"{check_id}_hold_diff"] = round(hold_median, 4)
        add(
            check_id,
            "pass" if early_median > max(1.2, hold_median + 0.6) and hold_median <= 8.0 else "fail",
            f"{check_id} changes then settles: early diff {early_median:.3f}, hold diff {hold_median:.3f}.",
        )

    if _phase_with_preset(plan, {"fade-up-lines", "text-slide-up-lines"}) and hold_sample is not None:
        text_phase = _phase_with_preset(plan, {"fade-up-lines", "text-slide-up-lines"})
        text_layers = sorted(_layers_with_recipe_tag(spec, "text-phase", limit=10), key=lambda layer: (float(layer.get("y") or 0), float(layer.get("x") or 0)))
        if text_phase and len(text_layers) >= 2:
            recipe_reveal_times: list[float] = []
            for layer in text_layers:
                recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else {}
                frames = sorted(
                    (frame for frame in list((recipe.get("motion_dsl") or {}).get("keyframes") or []) if isinstance(frame, dict)),
                    key=lambda frame: float(frame.get("time") or 0),
                )
                reveal_time = None
                for frame in frames:
                    try:
                        opacity = float(frame.get("opacity", 1) or 0)
                        y_value = abs(float(frame.get("y", 0) or 0))
                        time_value = float(frame.get("time") or 0)
                    except (TypeError, ValueError):
                        continue
                    if opacity >= 0.98 and y_value <= 1.5:
                        reveal_time = time_value
                        break
                recipe_reveal_times.append(float(reveal_time if reveal_time is not None else _phase_end(text_phase)))
            recipe_inversions = sum(1 for earlier, later in zip(recipe_reveal_times, recipe_reveal_times[1:]) if earlier > later + 0.08)
            start, phase_duration = _phase_start_duration(text_phase)
            samples = [start + phase_duration * value for value in (0.2, 0.4, 0.6, 0.8, 1.0)]
            reveal_times: list[float] = []
            for layer in text_layers:
                rect = _layer_rect_pixels(spec, layer, source_frame.width, source_frame.height)
                source_crop = _crop_rect(source_frame.convert("RGB"), rect) if rect else None
                if source_crop is None:
                    continue
                revealed = samples[-1]
                for sample_time in samples:
                    frame = _read_frame_at(frames_dir, sample_time, fps, frame_count)
                    if frame is None:
                        continue
                    sample_crop = _crop_rect(frame, rect)
                    if sample_crop is not None and _mean_abs_rgb_diff(sample_crop, source_crop) <= 10.0:
                        revealed = sample_time
                        break
                reveal_times.append(revealed)
            inversions = sum(1 for earlier, later in zip(reveal_times, reveal_times[1:]) if earlier > later + 0.08)
            metrics["text_top_down_inversions"] = inversions
            metrics["text_top_down_recipe_inversions"] = recipe_inversions
            status = "pass" if inversions == 0 or (recipe_inversions == 0 and inversions <= 1) else "fail"
            add(
                "text_top_down_order",
                status,
                f"Text top-down reveal inversions: {inversions}; DSL schedule inversions: {recipe_inversions}.",
            )

    outro = _phase_with_preset(plan, {"layer-scatter-fall", "full-frame-shatter", "gravity-drop-fade", "full-frame-drop"})
    if outro:
        outro_start, outro_duration = _phase_start_duration(outro)
        outro_frame = _read_frame_at(frames_dir, outro_start + max(0.05, outro_duration * 0.82), fps, frame_count)
        if outro_frame is None:
            add("scatter_fall_visual", "fail", "Outro scatter/fall sample frame is missing.")
        else:
            outro_diff = _mean_abs_rgb_diff(outro_frame, source_frame)
            metrics["scatter_fall_outro_diff"] = round(outro_diff, 4)
            add(
                "scatter_fall_visual",
                "pass" if outro_diff >= 10.0 else "fail",
                f"Scatter/fall outro differs from settled source: diff {outro_diff:.3f}.",
            )

    status = "fail" if errors else "pass" if checks else "pending"
    return {
        "status": status,
        "checks": checks,
        "metrics": metrics,
        "errors": errors,
        "warnings": warnings,
    }


def _restore_exact_hold_frames(
    frames_dir: Path,
    source_frame: Image.Image,
    fps: int,
    frame_count: int,
    hold_window: tuple[float, float],
) -> int:
    start, end = hold_window
    if end - start <= 0.05:
        return 0
    exact = source_frame.convert("RGB")
    start_index = _frame_index_for_time(start, fps, frame_count)
    end_index = _frame_index_for_time(max(start, end - (1.0 / max(1, fps))), fps, frame_count)
    restored = 0
    for frame_index in range(start_index, end_index + 1):
        frame_path = frames_dir / f"frame_{frame_index:05d}.png"
        if not frame_path.exists():
            continue
        exact.save(frame_path)
        restored += 1
    return restored


def _figma_visual_self_check_from_frames(
    spec: MotionSpec,
    frames_dir: Path,
    source_frame: Image.Image | None,
    fps: int,
    duration: float,
    frame_count: int,
    hold_window: tuple[float, float] | None,
    auto_retry_actions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {}

    def add_check(check_id: str, status: str, detail: str, **extra: object) -> None:
        checks.append({"id": check_id, "status": status, "detail": detail, **extra})

    if source_frame is None:
        add_check("source_figma_png", "pending", "No source Figma PNG is available for pixel comparison.")
        warnings.append("missing source Figma PNG")
    else:
        add_check("source_figma_png", "pass", "Source Figma PNG is available.")

    if not frames_dir.exists():
        errors.append("motion frame directory missing")
        add_check("rendered_frames", "fail", "Rendered motion frames are missing.")
    else:
        existing_frames = len(list(frames_dir.glob("frame_*.png")))
        metrics["rendered_frame_count"] = existing_frames
        add_check("rendered_frames", "pass" if existing_frames >= max(1, frame_count // 2) else "fail", f"{existing_frames} rendered frames found.")
        if existing_frames < max(1, frame_count // 2):
            errors.append("not enough rendered frames")

    if source_frame is not None and hold_window:
        sample_time = _hold_sample_time(hold_window, duration, fps)
        sample_index = _frame_index_for_time(sample_time, fps, frame_count)
        frame_path = frames_dir / f"frame_{sample_index:05d}.png"
        metrics["exact_hold_sample_time"] = round(sample_time, 4)
        metrics["exact_hold_sample_frame"] = sample_index
        if not frame_path.exists():
            errors.append("exact hold sample frame missing")
            add_check("exact_hold_pixel_match", "fail", "Could not sample exact-source hold frame.")
        else:
            with Image.open(frame_path) as frame_image:
                rendered = frame_image.convert("RGB")
                expected = source_frame.convert("RGB")
                if rendered.size != expected.size:
                    expected = expected.resize(rendered.size, Image.Resampling.LANCZOS)
                diff = _mean_abs_rgb_diff(rendered, expected)
                source_edges = _edge_score(expected)
                rendered_edges = _edge_score(rendered)
                sharpness_ratio = rendered_edges / max(0.001, source_edges)
                metrics["exact_hold_mean_abs_diff"] = round(diff, 4)
                metrics["source_edge_score"] = round(source_edges, 4)
                metrics["rendered_edge_score"] = round(rendered_edges, 4)
                metrics["hold_sharpness_ratio"] = round(sharpness_ratio, 4)
                if diff <= 8.0:
                    add_check("exact_hold_pixel_match", "pass", f"Hold frame matches Figma source: diff {diff:.3f}.")
                else:
                    errors.append(f"exact hold diff too high: {diff:.3f}")
                    add_check("exact_hold_pixel_match", "fail", f"Hold frame differs from Figma source: diff {diff:.3f}.")
                if sharpness_ratio >= 0.55 or source_edges < 0.5:
                    add_check("settled_sharpness", "pass", f"Sharpness ratio {sharpness_ratio:.3f}.")
                else:
                    errors.append(f"settled sharpness ratio too low: {sharpness_ratio:.3f}")
                    add_check("settled_sharpness", "fail", f"Sharpness ratio {sharpness_ratio:.3f}.")
    elif source_frame is not None:
        warnings.append("no exact-source hold window declared")
        add_check("exact_hold_pixel_match", "pending", "No exact-source hold window was declared.")

    execution_audit = _motion_prompt_execution_audit(spec, frames_dir, source_frame, fps, duration, frame_count, hold_window)
    if execution_audit.get("checks"):
        checks.append(
            {
                "id": "prompt_execution_auditor",
                "status": "pass" if execution_audit.get("status") == "pass" else "fail",
                "detail": "Prompt effect execution was checked against rendered frames.",
            }
        )
    if execution_audit.get("status") == "fail":
        errors.extend(str(item) for item in list(execution_audit.get("errors") or []) if item)
    warnings.extend(str(item) for item in list(execution_audit.get("warnings") or []) if item)
    if isinstance(execution_audit.get("metrics"), dict) and execution_audit.get("metrics"):
        metrics["prompt_execution"] = execution_audit.get("metrics")

    actions = [dict(item) for item in list(auto_retry_actions or []) if isinstance(item, dict)]
    status = "fail" if errors else "pass"
    return {
        "version": MOTION_VISUAL_SELF_CHECK_VERSION,
        "status": status,
        "asset_signature": motion_asset_signature(spec),
        "scope": "figma-motion-render",
        "source_of_truth": "figma-frame-png",
        "preview_render_source": "motion_dsl",
        "checks": checks,
        "metrics": metrics,
        "prompt_execution_audit": execution_audit,
        "auto_retry": {
            "attempted": bool(actions),
            "status": "repaired" if actions and not errors else "failed" if actions else "not-needed",
            "actions": actions,
        },
        "errors": errors,
        "warnings": warnings,
    }


def _write_figma_visual_self_check(assets_dir: Path, spec: MotionSpec, output: Path, report: dict[str, object]) -> None:
    report = {
        **report,
        "motion_id": spec.id,
        "motion_video": str(output.name),
    }
    targets = [
        assets_dir / f"{spec.id}.visual-self-check.json",
        output.with_suffix(".visual-self-check.json"),
    ]
    for target in targets:
        try:
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            continue


def _apply_figma_scene_camera(frame: Image.Image, camera_recipe: dict | None, local_time: float, duration: float) -> Image.Image:
    if not camera_recipe:
        return frame
    state = _motion_dsl_state(camera_recipe, local_time, duration)
    scale = max(0.01, float(state.get("scale", 1) or 1))
    x_pct = float(state.get("x", 0) or 0)
    y_pct = float(state.get("y", 0) or 0)
    rotate = float(state.get("rotate", 0) or 0)
    blur = max(0.0, float(state.get("blur", 0) or 0))
    if abs(scale - 1) <= 0.001 and abs(x_pct) <= 0.001 and abs(y_pct) <= 0.001 and abs(rotate) <= 0.001 and blur <= 0.001:
        return frame
    width, height = frame.size
    dx = (x_pct / 100.0) * width
    dy = (y_pct / 100.0) * height
    cover_scale = max(1.0, 1.0 + abs(dx) * 2.0 / max(1, width), 1.0 + abs(dy) * 2.0 / max(1, height))
    scale = max(scale, cover_scale)
    working = frame.convert("RGBA")
    if abs(rotate) > 0.001:
        working = working.rotate(-rotate, resample=Image.Resampling.BICUBIC, expand=True)
    scaled_w = max(width, int(round(working.width * scale)))
    scaled_h = max(height, int(round(working.height * scale)))
    if scaled_w != working.width or scaled_h != working.height:
        working = working.resize((scaled_w, scaled_h), Image.Resampling.BICUBIC)
    left = int(round(working.width / 2 - width / 2 - dx))
    top = int(round(working.height / 2 - height / 2 - dy))
    left = max(0, min(max(0, working.width - width), left))
    top = max(0, min(max(0, working.height - height), top))
    output = working.crop((left, top, left + width, top + height))
    if blur > 0.05:
        output = output.filter(ImageFilter.GaussianBlur(blur))
    return output


def render_motion_video_asset(spec: MotionSpec, assets_dir: Path, fps: int = 30) -> Path | None:
    if getattr(spec, "source_type", "generated") != "figma" or not getattr(spec, "figma_layers", None):
        return None
    project_root = assets_dir.parent
    ltx_layers = _figma_ltx_video_layers_with_index(spec, project_root)
    ltx_layer_ids = {str(layer.get("id") or "") for _index, layer, _path in ltx_layers}
    animated_layers = [
        layer
        for layer in (spec.figma_layers or [])
        if layer.get("motion_recipe") and layer.get("visible") is not False and str(layer.get("id") or "") not in ltx_layer_ids
    ]
    if not animated_layers and not ltx_layers:
        return None
    assets_dir.mkdir(parents=True, exist_ok=True)
    output = assets_dir / f"{spec.id}-{uuid.uuid4().hex[:12]}.mp4"
    frames_dir = assets_dir / f"{spec.id}_frames_{uuid.uuid4().hex[:8]}"
    _prune_motion_frame_dirs(assets_dir, spec.id, keep=frames_dir)

    layers = list(spec.figma_layers or [])
    scene_camera_recipe = _figma_scene_camera_recipe(layers)
    scene_camera_exact_source = _figma_scene_camera_uses_exact_source(layers)
    has_frame_choreography = any(
        "frame"
        in {
            str(tag).strip().casefold()
            for tag in list((layer.get("motion_recipe") or {}).get("tags") or [])
            if str(tag).strip()
        }
        for layer in layers
    )
    has_whole_frame_composite = any(
        "whole-frame-composite"
        in {
            str(tag).strip().casefold()
            for tag in list((layer.get("motion_recipe") or {}).get("tags") or [])
            if str(tag).strip()
        }
        for layer in layers
    )
    skip_ids = _figma_motion_skip_ids(spec) | _figma_ltx_skip_ids(spec, ltx_layers)
    duration = max(0.1, min(float(spec.duration), MAX_FIGMA_MOTION_RENDER_SECONDS))
    frame_count = max(1, int(duration * fps))
    source_frame = None
    source = project_root / spec.asset_path if spec.asset_path else None
    if source and source.exists():
        source_frame = Image.open(source).convert("RGBA")
    render_width = max(1, int(getattr(spec, "width", 1) or 1))
    render_height = max(1, int(getattr(spec, "height", 1) or 1))
    if source_frame is not None:
        render_width, render_height = source_frame.size
    render_spec = spec.model_copy(update={"width": render_width, "height": render_height})
    bounds_width, bounds_height = _figma_layer_bounds(spec, layers)
    scale_x = render_width / bounds_width
    scale_y = render_height / bounds_height
    crop_source_frame = source_frame
    generated_crop_source_frame = None
    if source_frame is not None and _figma_has_hidden_visible_layer_override(spec):
        generated_crop_source_frame = Image.new("RGBA", (render_width, render_height), _figma_root_fill(spec, layers))
        _draw_figma_layers(
            render_spec,
            generated_crop_source_frame,
            ImageDraw.Draw(generated_crop_source_frame),
            project_root,
        )
        crop_source_frame = generated_crop_source_frame
    declared_hold_window = _figma_declared_exact_hold_window(render_spec)
    static_hold_window = declared_hold_window or _figma_static_hold_window(spec)
    if scene_camera_recipe:
        static_hold_window = None
    static_hold_frame = source_frame.resize((render_width, render_height), Image.Resampling.LANCZOS) if source_frame is not None and static_hold_window else None

    ltx_cover_layers = [layer for _index, layer, _path in ltx_layers]
    hidden_cover_layers = [
        layer
        for layer in layers
        if layer.get("visible") is False
        and not _is_internal_frame_choreography_layer(layer)
        and not _is_root_figma_layer(layer, spec)
    ]
    use_exact_clean_base = bool(source_frame is not None and not has_frame_choreography)
    if scene_camera_recipe and scene_camera_exact_source and source_frame is not None:
        base = source_frame.copy()
    elif use_exact_clean_base and source_frame is not None:
        base = _exact_figma_clean_base(
            render_spec,
            source_frame,
            layers,
            [*animated_layers, *ltx_cover_layers, *hidden_cover_layers],
            scale_x,
            scale_y,
        )
    else:
        base_fill = (0, 0, 0, 0) if has_whole_frame_composite else _figma_root_fill(spec, layers) if has_frame_choreography else (0, 0, 0, 0)
        base = Image.new("RGBA", (render_width, render_height), base_fill)
        _draw_figma_layers(render_spec, base, ImageDraw.Draw(base), project_root, skip_ids=skip_ids)
    base_media: Path
    base_is_video = False

    if animated_layers:
        _best_effort_rmtree(frames_dir)
        frames_dir.mkdir(parents=True, exist_ok=True)
        for frame_index in range(frame_count):
            local_time = frame_index / fps
            if static_hold_window and static_hold_frame is not None and static_hold_window[0] <= local_time <= static_hold_window[1]:
                frame = _apply_figma_scene_camera(static_hold_frame.convert("RGBA"), scene_camera_recipe, local_time, duration)
                frame.convert("RGB").save(frames_dir / f"frame_{frame_index:05d}.png")
                continue
            frame = base.copy()
            ordered_layers = [
                layer
                for _index, layer in sorted(
                    enumerate(animated_layers),
                    key=lambda item: _figma_motion_render_order(item[1], spec, item[0]),
                )
            ]
            for layer in ordered_layers:
                recipe = layer.get("motion_recipe") or {}
                state = _motion_dsl_state(recipe, local_time, duration)
                mask = _find_visual_mask_for_layer(layer, layers)
                raw_visual_rect = mask or layer
                recipe_tags = {
                    str(tag).strip().casefold()
                    for tag in list(recipe.get("tags") or [])
                    if str(tag).strip()
                }
                is_frame_choreo = "frame" in recipe_tags
                is_whole_frame_composite = "whole-frame-composite" in recipe_tags
                if is_whole_frame_composite:
                    # MP4 has no alpha channel. Keep the generated whole-frame
                    # asset static; final preview/render applies fade/drop at
                    # the overlay level so transparent gaps do not encode black.
                    state = {
                        **state,
                        "x": 0,
                        "y": 0,
                        "scale": 1,
                        "scaleX": 1,
                        "scaleY": 1,
                        "rotate": 0,
                        "blur": 0,
                        "opacity": 1,
                    }
                layer_area = float(raw_visual_rect.get("width") or 1) * float(raw_visual_rect.get("height") or 1)
                frame_area = max(1.0, bounds_width * bounds_height)
                node_type = str(layer.get("node_type") or "").upper()
                has_layer_asset = _has_existing_layer_asset(layer, project_root)
                can_source_crop = bool(
                    source_frame is not None
                    and not str(layer.get("id") or "").startswith("__frame_choreo_white_bg")
                    and not _is_root_figma_layer(layer, spec)
                )
                source_crop_required = (
                    bool(layer.get("render_cluster_source"))
                    or layer.get("kind") == "text"
                    or (layer.get("kind") == "image" and not has_layer_asset)
                    or (
                    layer.get("kind") == "shape"
                    and node_type not in {"RECTANGLE", "FRAME", "GROUP"}
                    and not _is_root_figma_layer(layer, spec)
                    and layer_area <= frame_area * 0.08
                    )
                )
                if is_frame_choreo:
                    exact_text_or_vector = bool(
                        layer.get("kind") == "text"
                        or (
                            layer.get("kind") == "shape"
                            and node_type not in {"RECTANGLE", "FRAME", "GROUP"}
                            and layer_area <= frame_area * 0.08
                        )
                    )
                    use_source_crop = bool(
                        can_source_crop
                        and (
                            "shatter-overlay" in recipe_tags
                            or bool(layer.get("render_cluster_source"))
                            or exact_text_or_vector
                        )
                    )
                else:
                    use_source_crop = bool(can_source_crop and source_crop_required)
                if use_source_crop and layer.get("kind") == "text":
                    visual_rect = _content_text_visual_rect(layer, raw_visual_rect, scale_x, scale_y)
                elif use_source_crop or has_layer_asset:
                    visual_rect = raw_visual_rect
                else:
                    visual_rect = _tight_text_visual_rect(layer, raw_visual_rect, scale_x, scale_y)
                sprite_width = max(1, int(round(float(visual_rect.get("width") or 1) * scale_x)))
                sprite_height = max(1, int(round(float(visual_rect.get("height") or 1) * scale_y)))
                if "source_crop_key_transparent" in layer:
                    key_source_transparency = bool(layer.get("source_crop_key_transparent"))
                else:
                    key_source_transparency = bool(
                        use_source_crop
                        and "shatter-overlay" not in recipe_tags
                        and (
                            not is_frame_choreo
                            or layer.get("kind") == "text"
                            or (
                                layer.get("kind") == "shape"
                                and node_type not in {"RECTANGLE", "FRAME", "GROUP"}
                                and not _is_root_figma_layer(layer, spec)
                            )
                        )
                    )
                key_strategy = "fill-shape" if (
                    key_source_transparency
                    and layer.get("kind") == "shape"
                    and node_type not in {"RECTANGLE", "FRAME", "GROUP"}
                ) else "corner"
                sprite = (
                    _render_figma_layer_source_crop(
                        layer,
                        visual_rect,
                        bounds_width,
                        bounds_height,
                        sprite_width,
                        sprite_height,
                        crop_source_frame,
                        key_transparent=key_source_transparency,
                        key_strategy=key_strategy,
                    )
                    if use_source_crop
                    else None
                )
                if sprite is None:
                    sprite = _render_single_figma_layer(layer, visual_rect, scale_x, scale_y, project_root)
                if sprite is None:
                    continue
                sprite = _apply_sprite_visual_effects(sprite, recipe, local_time)
                if state.get("blur", 0) > 0.05:
                    sprite = sprite.filter(ImageFilter.GaussianBlur(float(state["blur"])))
                opacity = 1.0 if is_whole_frame_composite else max(0.0, min(1.0, float(state.get("opacity", 1))))
                if opacity <= 0:
                    continue
                if opacity < 1:
                    sprite.putalpha(sprite.getchannel("A").point(lambda value: int(value * opacity)))
                scale = max(0.01, float(state.get("scale", 1)))
                sx = scale * max(0.01, float(state.get("scaleX", 1)))
                sy = scale * max(0.01, float(state.get("scaleY", 1)))
                if abs(sx - 1) > 0.001 or abs(sy - 1) > 0.001:
                    sprite = sprite.resize((max(1, int(sprite.width * sx)), max(1, int(sprite.height * sy))), Image.Resampling.BICUBIC)
                rotate = float(state.get("rotate", 0) or 0)
                if abs(rotate) > 0.001:
                    sprite = sprite.rotate(-rotate, resample=Image.Resampling.BICUBIC, expand=True)
                vx = float(visual_rect.get("x") or 0) * scale_x
                vy = float(visual_rect.get("y") or 0) * scale_y
                vw = float(visual_rect.get("width") or 1) * scale_x
                vh = float(visual_rect.get("height") or 1) * scale_y
                transform_reference = recipe.get("transform_reference") if isinstance(recipe.get("transform_reference"), dict) else {}
                ref_w = max(1.0, float(transform_reference.get("width") or visual_rect.get("width") or 1)) * scale_x
                ref_h = max(1.0, float(transform_reference.get("height") or visual_rect.get("height") or 1)) * scale_y
                center_x = vx + vw / 2 + (float(state.get("x", 0)) / 100.0) * ref_w
                center_y = vy + vh / 2 + (float(state.get("y", 0)) / 100.0) * ref_h
                paste_x = int(round(center_x - sprite.width / 2))
                paste_y = int(round(center_y - sprite.height / 2))
                frame.alpha_composite(sprite, (paste_x, paste_y))
            frame = _apply_figma_scene_camera(frame, scene_camera_recipe, local_time, duration)
            frame.convert("RGB").save(frames_dir / f"frame_{frame_index:05d}.png")

        visual_self_check: dict[str, object] | None = None
        auto_retry_actions: list[dict[str, object]] = []
        self_check_hold_window = static_hold_window or (None if scene_camera_recipe else declared_hold_window)
        if source_frame is not None:
            visual_self_check = _figma_visual_self_check_from_frames(
                render_spec,
                frames_dir,
                source_frame,
                fps,
                duration,
                frame_count,
                self_check_hold_window,
                auto_retry_actions,
            )
            if visual_self_check.get("status") != "pass" and self_check_hold_window:
                restored = _restore_exact_hold_frames(frames_dir, source_frame, fps, frame_count, self_check_hold_window)
                if restored > 0:
                    auto_retry_actions.append(
                        {
                            "kind": "exact-source-hold",
                            "target": "figma-frame-png",
                            "detail": f"restored {restored} exact Figma hold frames before mp4 encode",
                        }
                    )
                    visual_self_check = _figma_visual_self_check_from_frames(
                        render_spec,
                        frames_dir,
                        source_frame,
                        fps,
                        duration,
                        frame_count,
                        self_check_hold_window,
                        auto_retry_actions,
                    )
        if visual_self_check is None:
            visual_self_check = _figma_visual_self_check_from_frames(
                render_spec,
                frames_dir,
                source_frame,
                fps,
                duration,
                frame_count,
                self_check_hold_window,
                auto_retry_actions,
            )

        if source_frame is not None:
            source_frame.close()
        if generated_crop_source_frame is not None:
            generated_crop_source_frame.close()

        base_media = output if not ltx_layers else assets_dir / f"{spec.id}_base.mp4"
        encoded_base_media = output.with_name(f"{output.stem}.encoding.mp4") if not ltx_layers else base_media
        command = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "frame_%05d.png"),
            "-c:v",
            "libx264",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-preset",
            "veryfast",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-t",
            f"{duration:.3f}",
            str(encoded_base_media),
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base_is_video = True
        if not ltx_layers:
            encoded_base_media.replace(output)
            _write_figma_visual_self_check(assets_dir, render_spec, output, visual_self_check)
            _prune_versioned_motion_videos(assets_dir, spec.id, output.name)
            _best_effort_rmtree(frames_dir)
            return output
    else:
        base_media = assets_dir / f"{spec.id}_base.png"
        base.convert("RGB").save(base_media)

    command = ["ffmpeg", "-y"]
    if base_is_video:
        command.extend(["-i", str(base_media)])
    else:
        command.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(base_media)])
    overlay_specs: list[tuple[int, dict, int, int, int, int]] = []
    for layer_index, layer, path in ltx_layers:
        visual_rect = _find_visual_mask_for_layer(layer, layers) or layer
        x = int(round(float(visual_rect.get("x") or 0) * scale_x))
        y = int(round(float(visual_rect.get("y") or 0) * scale_y))
        width = max(1, int(round(float(visual_rect.get("width") or 1) * scale_x)))
        height = max(1, int(round(float(visual_rect.get("height") or 1) * scale_y)))
        command.extend(["-stream_loop", "-1", "-i", str(path)])
        overlay_specs.append((layer_index, layer, x, y, width, height))

    static_segment_inputs: list[tuple[int, int, Path]] = []
    ltx_count = len(overlay_specs)
    for index, (layer_index, _layer, _x, _y, _width, _height) in enumerate(overlay_specs):
        next_ltx_index = overlay_specs[index + 1][0] if index + 1 < len(overlay_specs) else None
        segment_path = assets_dir / f"{spec.id}_ltx_z_{index:02d}.png"
        if _save_static_figma_segment(
            render_spec,
            project_root,
            segment_path,
            skip_ids,
            start_index=layer_index + 1,
            end_index=next_ltx_index,
            clip_rect=(_x, _y, _x + _width, _y + _height),
        ):
            command.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(segment_path)])
            static_segment_inputs.append((index, ltx_count + len(static_segment_inputs) + 1, segment_path))

    filter_parts = ["[0:v]setpts=PTS-STARTPTS,format=rgba[v0]"]
    current_label = "[v0]"
    for index, (_layer_index, _layer, x, y, width, height) in enumerate(overlay_specs, start=1):
        scaled_label = f"[ltx{index}]"
        output_label = f"[v{index}_ltx]"
        filter_parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setpts=PTS-STARTPTS,format=rgba{scaled_label}"
        )
        filter_parts.append(f"{current_label}{scaled_label}overlay={x}:{y}:shortest=0:eof_action=repeat{output_label}")
        current_label = output_label
        segment = next((item for item in static_segment_inputs if item[0] == index - 1), None)
        if segment:
            segment_label = f"[seg{index}]"
            segment_output = f"[v{index}_z]"
            filter_parts.append(f"[{segment[1]}:v]setpts=PTS-STARTPTS,format=rgba{segment_label}")
            filter_parts.append(f"{current_label}{segment_label}overlay=0:0:shortest=0:eof_action=repeat{segment_output}")
            current_label = segment_output
    filter_parts.append(f"{current_label}format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2[vout]")
    encoded_output = output.with_name(f"{output.stem}.encoding.mp4")
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-an",
            "-r",
            str(fps),
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "16",
            str(encoded_output),
        ]
    )
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    encoded_output.replace(output)
    if 'visual_self_check' in locals() and isinstance(visual_self_check, dict):
        _write_figma_visual_self_check(assets_dir, render_spec, output, visual_self_check)
    _prune_versioned_motion_videos(assets_dir, spec.id, output.name)
    _best_effort_rmtree(frames_dir)
    return output


def _figma_root_fill(spec: MotionSpec, layers: list[dict]) -> tuple[int, int, int, int]:
    for layer in layers:
        if _is_root_figma_layer(layer, spec):
            fill = _parse_rgba(str(layer.get("fill") or "rgba(255,255,255,1)"), (255, 255, 255, 255))
            return (fill[0], fill[1], fill[2], 255)
    return (255, 255, 255, 255)


def _exact_figma_clean_base(
    spec: MotionSpec,
    source_frame: Image.Image,
    layers: list[dict],
    cover_layers: list[dict],
    scale_x: float,
    scale_y: float,
) -> Image.Image:
    base = source_frame.resize((max(1, spec.width), max(1, spec.height)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(base)
    fill = _figma_root_fill(spec, layers)
    pad = max(1, int(round(min(spec.width, spec.height) * 0.002)))
    for layer in cover_layers:
        if not isinstance(layer, dict) or _is_root_figma_layer(layer, spec):
            continue
        visual_rect = _find_visual_mask_for_layer(layer, layers) or layer
        if layer.get("kind") == "text":
            visual_rect = _content_text_visual_rect(layer, visual_rect, scale_x, scale_y)
        left = int(round(float(visual_rect.get("x") or 0) * scale_x)) - pad
        top = int(round(float(visual_rect.get("y") or 0) * scale_y)) - pad
        right = int(round((float(visual_rect.get("x") or 0) + float(visual_rect.get("width") or 1)) * scale_x)) + pad
        bottom = int(round((float(visual_rect.get("y") or 0) + float(visual_rect.get("height") or 1)) * scale_y)) + pad
        left = max(0, min(base.width, left))
        top = max(0, min(base.height, top))
        right = max(left + 1, min(base.width, right))
        bottom = max(top + 1, min(base.height, bottom))
        try:
            radius = max(0.0, float(visual_rect.get("radius") or layer.get("radius") or 0) * min(scale_x, scale_y))
        except (TypeError, ValueError):
            radius = 0.0
        if radius > 0:
            draw.rounded_rectangle((left, top, right, bottom), radius=radius + pad, fill=fill)
        else:
            draw.rectangle((left, top, right, bottom), fill=fill)
    return base


def _normalize_figma_rect(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("@{") and text.endswith("}"):
        text = text[2:-1]
    result: dict = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        raw_value = raw_value.strip()
        try:
            result[key] = float(raw_value)
        except ValueError:
            result[key] = raw_value
    if "width" not in result or "height" not in result:
        return None
    return result


def _inflated_manual_cover_rect(layer: dict, rect: dict, bounds_width: float, bounds_height: float) -> dict:
    rect = _normalize_figma_rect(rect) or {}
    width = max(1.0, float(rect.get("width") or 1))
    height = max(1.0, float(rect.get("height") or 1))
    text_pad = max(18.0, float(layer.get("font_size") or 16) * 0.35) if layer.get("kind") == "text" else 0.0
    pad = max(4.0, min(max(bounds_width, bounds_height) * 0.015, max(width, height) * 0.08), text_pad)
    x = max(0.0, float(rect.get("x") or 0) - pad)
    y = max(0.0, float(rect.get("y") or 0) - pad)
    right = min(bounds_width, float(rect.get("x") or 0) + width + pad)
    bottom = min(bounds_height, float(rect.get("y") or 0) + height + pad)
    inflated = dict(rect)
    inflated.update({"x": x, "y": y, "width": max(1.0, right - x), "height": max(1.0, bottom - y)})
    return inflated


def _disjoint_cover_rects(rects: list[dict]) -> list[dict]:
    valid = [rect for rect in rects if float(rect.get("width") or 0) > 0 and float(rect.get("height") or 0) > 0]
    if not valid:
        return []
    xs = sorted({float(rect.get("x") or 0) for rect in valid} | {float(rect.get("x") or 0) + float(rect.get("width") or 0) for rect in valid})
    ys = sorted({float(rect.get("y") or 0) for rect in valid} | {float(rect.get("y") or 0) + float(rect.get("height") or 0) for rect in valid})
    result: list[dict] = []
    for y, next_y in zip(ys, ys[1:]):
        if next_y <= y:
            continue
        row: dict | None = None
        for x, next_x in zip(xs, xs[1:]):
            if next_x <= x:
                continue
            covered = any(
                x >= float(rect.get("x") or 0)
                and next_x <= float(rect.get("x") or 0) + float(rect.get("width") or 0)
                and y >= float(rect.get("y") or 0)
                and next_y <= float(rect.get("y") or 0) + float(rect.get("height") or 0)
                for rect in valid
            )
            if not covered:
                if row:
                    result.append(row)
                    row = None
                continue
            if row and abs(float(row["x"]) + float(row["width"]) - x) < 0.001:
                row["width"] = next_x - float(row["x"])
            else:
                if row:
                    result.append(row)
                row = {"x": x, "y": y, "width": next_x - x, "height": next_y - y}
        if row:
            result.append(row)
    return result


def _draw_manual_figma_patches(spec: MotionSpec, image: Image.Image, project_root: Path) -> bool:
    layers = list(getattr(spec, "figma_layers", []) or [])
    ltx_mask_ids = {
        str(layer.get("visual_mask_id") or "")
        for layer in layers
        if layer.get("ltx_video_path") and layer.get("visual_mask_id")
    }
    manual_layers = [
        layer
        for layer in layers
        if layer.get("manual_transform") and layer.get("visible") is not False and not _is_root_figma_layer(layer, spec)
        and str(layer.get("id") or "") not in ltx_mask_ids
    ]
    if not manual_layers:
        return False
    bounds_width, bounds_height = _figma_layer_bounds(spec, layers)
    scale_x = spec.width / bounds_width
    scale_y = spec.height / bounds_height
    fill = _figma_root_fill(spec, layers)
    draw = ImageDraw.Draw(image)
    manual_skip_ids = {
        str(layer.get("id") or "")
        for layer in manual_layers
        if layer.get("id")
    }
    manual_skip_ids.update(
        str(layer.get("visual_mask_id") or "")
        for layer in manual_layers
        if layer.get("visual_mask_id")
    )

    raw_cover_rects = []
    for layer in manual_layers:
        if layer.get("ltx_video_path"):
            original_rect = _normalize_figma_rect(layer.get("original_geometry")) or _normalize_figma_rect(layer.get("original_visual_rect"))
        else:
            original_rect = _normalize_figma_rect(layer.get("original_visual_rect")) or _normalize_figma_rect(layer.get("original_geometry"))
        if original_rect:
            raw_cover_rects.append(_inflated_manual_cover_rect(layer, original_rect, bounds_width, bounds_height))

    cover_rects = _disjoint_cover_rects(raw_cover_rects)
    reconstructed = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    has_reconstruction = _draw_figma_layers(spec, reconstructed, ImageDraw.Draw(reconstructed), project_root, skip_ids=manual_skip_ids)
    for cover_rect in cover_rects:
        x = int(round(float(cover_rect.get("x") or 0) * scale_x))
        y = int(round(float(cover_rect.get("y") or 0) * scale_y))
        width = max(1, int(round(float(cover_rect.get("width") or 1) * scale_x)))
        height = max(1, int(round(float(cover_rect.get("height") or 1) * scale_y)))
        if has_reconstruction:
            patch = reconstructed.crop((x, y, x + width, y + height))
            image.alpha_composite(patch, (x, y))
        else:
            draw.rectangle((x, y, x + width, y + height), fill=fill)

    for layer in manual_layers:
        visual_rect = _find_visual_mask_for_layer(layer, layers) or layer
        sprite = _render_single_figma_layer(layer, visual_rect, scale_x, scale_y, project_root)
        if sprite is None:
            continue
        image.alpha_composite(
            sprite,
            (
                int(round(float(visual_rect.get("x") or 0) * scale_x)),
                int(round(float(visual_rect.get("y") or 0) * scale_y)),
            ),
        )
    return True


def render_motion_asset(spec: MotionSpec, assets_dir: Path) -> Path:
    assets_dir.mkdir(parents=True, exist_ok=True)
    output = assets_dir / f"{spec.id}.png"
    render_motion_video_asset(spec, assets_dir)
    manual_layers = [
        layer
        for layer in (getattr(spec, "figma_layers", None) or [])
        if layer.get("manual_transform") and layer.get("visible") is not False and not _is_root_figma_layer(layer, spec)
    ]
    if getattr(spec, "source_type", "generated") == "figma" and manual_layers:
        source = assets_dir.parent / spec.asset_path if spec.asset_path else None
        if source and source.exists():
            with Image.open(source).convert("RGBA") as image:
                image = image.resize((max(1, spec.width), max(1, spec.height)), Image.Resampling.LANCZOS)
                _draw_manual_figma_patches(spec, image, assets_dir.parent)
                image.save(output)
                return output
        image = Image.new("RGBA", (spec.width, spec.height), (255, 255, 255, 255))
        if _draw_manual_figma_patches(spec, image, assets_dir.parent):
            image.save(output)
            return output

    if getattr(spec, "source_type", "generated") == "figma" and spec.asset_path:
        source = assets_dir.parent / spec.asset_path
        if source.exists():
            with Image.open(source).convert("RGBA") as image:
                image = image.resize((max(1, spec.width), max(1, spec.height)), Image.Resampling.LANCZOS)
                image.save(output)
            return output

    if getattr(spec, "source_type", "generated") == "figma" and getattr(spec, "figma_layers", None):
        image = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if _draw_figma_layers(spec, image, draw, assets_dir.parent):
            image.save(output)
            return output

    image = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    spec.text = re.sub(r"\s+", " ", spec.text).strip()

    if spec.design_preset != "soft-neumorphism" and _style_family(spec) == "editorial-grid":
        _draw_editorial_grid(spec, image, draw)
    elif spec.design_preset == "soft-neumorphism":
        _draw_soft_neumorphism(spec, image, draw)
    elif spec.design_preset == "frosted-glass":
        _draw_frosted_glass(spec, image, draw)
    elif spec.design_preset == "warm-teal-ui":
        _draw_warm_teal_ui(spec, image, draw)
    elif spec.design_preset == "creator-vibe":
        _draw_creator_vibe(spec, image, draw)
    elif spec.design_preset == "glass":
        _draw_glass(spec, image, draw)
    elif spec.design_preset == "data-panel":
        _draw_data_panel(spec, image, draw)
    elif spec.design_preset == "bold-caption":
        _draw_bold_caption(spec, image, draw)
    else:
        _draw_liquid_glass(spec, image, draw)

    _fade_transparent_edges(image, edge=max(8, min(20, int(min(spec.width, spec.height) * 0.08))))
    image.save(output)
    return output
