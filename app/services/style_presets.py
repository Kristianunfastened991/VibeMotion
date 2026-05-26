from __future__ import annotations

import colorsys
import io
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageStat

from app.core.config import settings
from app.models.schemas import MotionSpec


STYLE_PRESETS_DIR = settings.project_root / "style_presets"
SOFT_NEUMORPHISM_PRESET_ID = "soft-neumorphism"
FROSTED_GLASS_PRESET_ID = "frosted-glass"
WARM_TEAL_UI_PRESET_ID = "warm-teal-ui"
SOFT_NEUMORPHISM_PROFILE: dict[str, Any] = {
    "version": 1,
    "preset_id": SOFT_NEUMORPHISM_PRESET_ID,
    "name": "Soft Neumorphism",
    "style_name": "Soft Neumorphism",
    "source": "builtin-style-lock",
    "created_at": "builtin",
    "reference_count": 0,
    "style_family": "soft-neumorphism",
    "palette": ["#f2f1ed", "#deddd8", "#c8c9c7", "#171a1d", "#2b8cff"],
    "tokens": {
        "background_color": "#f2f1ed",
        "foreground_color": "#171a1d",
        "accent": "#2b8cff",
        "accent_palette": ["#2b8cff", "#9ba0a6", "#ffffff"],
        "overlay_background": "rgba(242, 241, 237, 0.98)",
        "panel_background": "rgba(242, 241, 237, 0.98)",
        "guide_color": "rgba(23, 26, 29, 0.16)",
        "shape_language": "soft-neumorphism",
        "font_family": "Segoe UI",
        "font_weight": 650,
        "motion_energy": "calm",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    "rules": [
        "Use only light soft-neumorphic panels: off-white matte surfaces, shallow outer shadows, inset controls, pills, rows, sliders, toggles, and small blue accents.",
        "Keep overlays compact and readable, with enough empty space around the face and existing video text.",
        "Do not use glass, neon, dark editorial, glitch, or poster-grid styling in this locked preset.",
        "Preserve Figma and LTX layers as independent sources of truth.",
    ],
    "sample_files": [],
}

FROSTED_GLASS_PROFILE: dict[str, Any] = {
    "version": 1,
    "preset_id": FROSTED_GLASS_PRESET_ID,
    "name": "Frosted Glass",
    "style_name": "Frosted Glass",
    "source": "builtin-style-lock",
    "created_at": "builtin",
    "reference_count": 2,
    "style_family": "frosted-glass",
    "palette": ["#f7f8f6", "#d9dedb", "#9ca6a2", "#1c2025", "#a6fff0"],
    "tokens": {
        "background_color": "#f7f8f6",
        "foreground_color": "#1c2025",
        "accent": "#a6fff0",
        "accent_palette": ["#a6fff0", "#d9c7ff", "#ffffff"],
        "overlay_background": "rgba(246, 248, 246, 0.58)",
        "panel_background": "rgba(246, 248, 246, 0.58)",
        "guide_color": "rgba(255, 255, 255, 0.46)",
        "shape_language": "frosted-glass",
        "font_family": "Segoe UI",
        "font_weight": 700,
        "motion_energy": "calm-tech",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    "rules": [
        "Use translucent frosted panels with blur, bright top highlights, soft gray shadows, and rounded 14-22px corners.",
        "Plain labels stay clean: text on a glass panel only, with no progress bars or controls unless the meaning requires them.",
        "Technical/comparison beats may use a small uppercase label and a dark inset code panel, like HyperFrames reference cards.",
        "Keep the panel readable over video and avoid covering faces when safe zones are available.",
        "Preserve Figma and LTX layers as independent sources of truth.",
    ],
    "sample_files": [],
}

WARM_TEAL_UI_PROFILE: dict[str, Any] = {
    "version": 1,
    "preset_id": WARM_TEAL_UI_PRESET_ID,
    "name": "Warm Teal UI",
    "style_name": "Warm Teal UI",
    "source": "builtin-style-lock",
    "created_at": "builtin",
    "reference_count": 1,
    "style_family": "warm-teal-ui",
    "palette": ["#efe7d7", "#ded2bd", "#bcae99", "#181a18", "#006d6b"],
    "tokens": {
        "background_color": "#efe7d7",
        "foreground_color": "#181a18",
        "accent": "#006d6b",
        "accent_palette": ["#006d6b", "#0b817d", "#b68b54"],
        "overlay_background": "rgba(239, 231, 215, 0.98)",
        "panel_background": "rgba(239, 231, 215, 0.98)",
        "guide_color": "rgba(0, 109, 107, 0.26)",
        "shape_language": "warm-teal-ui",
        "font_family": "Segoe UI",
        "font_weight": 720,
        "motion_energy": "calm-product-ui",
        "enter_animation": "rise",
        "exit_animation": "fade",
        "easing": "sine",
    },
    "rules": [
        "Use warm cream neumorphic surfaces with raised panels, inset fields, compact rounded controls, and dark teal accents.",
        "Plain labels stay simple: bold text on a warm raised panel without decorative controls.",
        "Use teal sliders, toggles, buttons, checkboxes, rows, and process pills only when the prompt or transcript context asks for those UI ideas.",
        "Keep components compact, tactile, and readable over video; avoid glass blur, neon, dark tech panels, and random blue lines.",
        "Preserve Figma and LTX layers as independent sources of truth.",
    ],
    "sample_files": [],
}


def soft_neumorphism_profile() -> dict[str, Any]:
    return json.loads(json.dumps(SOFT_NEUMORPHISM_PROFILE, ensure_ascii=False))


def frosted_glass_profile() -> dict[str, Any]:
    return json.loads(json.dumps(FROSTED_GLASS_PROFILE, ensure_ascii=False))


def warm_teal_ui_profile() -> dict[str, Any]:
    return json.loads(json.dumps(WARM_TEAL_UI_PROFILE, ensure_ascii=False))


def soft_neumorphism_summary() -> dict[str, Any]:
    return _style_summary(soft_neumorphism_profile())


def frosted_glass_summary() -> dict[str, Any]:
    return _style_summary(frosted_glass_profile())


def warm_teal_ui_summary() -> dict[str, Any]:
    return _style_summary(warm_teal_ui_profile())


def _style_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "preset_id": profile["preset_id"],
        "name": profile["name"],
        "style_name": profile["style_name"],
        "reference_count": profile.get("reference_count", 0),
        "style_family": profile.get("style_family"),
        "tokens": profile.get("tokens") or {},
        "palette": profile.get("palette") or [],
        "preview_path": profile.get("preview_path"),
        "created_at": profile.get("created_at"),
    }


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "style"


def _hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _luma(rgb: tuple[int, int, int]) -> float:
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722


def _contrast_color(rgb: tuple[int, int, int]) -> str:
    return "#000000" if _luma(rgb) > 150 else "#ffffff"


def _rgb_from_hex(value: str | None) -> tuple[int, int, int] | None:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) < 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def _rgb_from_css(value: str | None) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    if text.startswith("#"):
        return _rgb_from_hex(text)
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


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def _read_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGBA")


def _sample_pixels(image: Image.Image, max_size: int = 160) -> list[tuple[int, int, int]]:
    sample = image.copy()
    sample.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    pixels: list[tuple[int, int, int]] = []
    for red, green, blue, alpha in sample.getdata():
        if alpha < 8:
            continue
        pixels.append((int(red), int(green), int(blue)))
    return pixels


def _average_color(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not pixels:
        return 255, 255, 255
    return tuple(int(sum(pixel[index] for pixel in pixels) / len(pixels)) for index in range(3))  # type: ignore[return-value]


def _edge_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    sample = image.copy()
    sample.thumbnail((160, 160), Image.Resampling.LANCZOS)
    width, height = sample.size
    data = sample.load()
    pixels: list[tuple[int, int, int]] = []
    if data is None:
        return pixels
    for x in range(width):
        for y in (0, height - 1):
            red, green, blue, alpha = data[x, y]
            if alpha >= 8:
                pixels.append((int(red), int(green), int(blue)))
    for y in range(height):
        for x in (0, width - 1):
            red, green, blue, alpha = data[x, y]
            if alpha >= 8:
                pixels.append((int(red), int(green), int(blue)))
    return pixels


def _palette_from_image(image: Image.Image, colors: int = 16) -> Counter[str]:
    rgb = image.convert("RGB")
    rgb.thumbnail((180, 180), Image.Resampling.LANCZOS)
    quantized = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts: Counter[str] = Counter()
    for count, index in quantized.getcolors(maxcolors=colors * 4096) or []:
        offset = index * 3
        if offset + 2 >= len(palette):
            continue
        rgb_value = (palette[offset], palette[offset + 1], palette[offset + 2])
        counts[_hex(rgb_value)] += int(count)
    return counts


def _saturation(rgb: tuple[int, int, int]) -> float:
    _hue, saturation, _value = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    return float(saturation)


def _pick_accent(palette: list[tuple[str, int]], background: tuple[int, int, int], foreground: str) -> str:
    best: tuple[float, str] | None = None
    for color, count in palette:
        rgb = _rgb_from_hex(color)
        if rgb is None:
            continue
        if _luma(rgb) < 34:
            continue
        distance = _color_distance(rgb, background)
        score = _saturation(rgb) * 140 + min(120, distance) + math.log(max(1, count), 10) * 12
        if distance < 32:
            score -= 80
        if best is None or score > best[0]:
            best = (score, color)
    return best[1] if best else foreground


def _accent_palette(palette: list[tuple[str, int]], background: tuple[int, int, int], foreground: str) -> list[str]:
    scored: list[tuple[float, str]] = []
    for color, count in palette:
        rgb = _rgb_from_hex(color)
        if rgb is None:
            continue
        saturation = _saturation(rgb)
        if _luma(rgb) < 34:
            continue
        distance = _color_distance(rgb, background)
        channel_spread = max(rgb) - min(rgb)
        if channel_spread < 24 and _luma(rgb) < 220:
            continue
        if saturation < 0.18 and distance < 90:
            continue
        score = saturation * 180 + min(160, distance) + math.log(max(1, count), 10) * 10
        scored.append((score, color))
    accents: list[str] = []
    for _score, color in sorted(scored, reverse=True):
        if color not in accents:
            accents.append(color)
        if len(accents) >= 6:
            break
    if not accents and foreground.startswith("#"):
        accents.append(foreground)
    return accents


def _vivid_pixel_palette(pixels: list[tuple[int, int, int]], background: tuple[int, int, int], limit: int = 6) -> list[str]:
    buckets: Counter[tuple[int, int, int]] = Counter()
    step = max(1, len(pixels) // 12000)
    for rgb in pixels[::step]:
        hue, saturation, value = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        if saturation < 0.34 or value < 0.32 or _luma(rgb) < 38:
            continue
        if _color_distance(rgb, background) < 70:
            continue
        bucket = tuple(max(0, min(255, int(round(channel / 24) * 24))) for channel in rgb)
        buckets[bucket] += 1
    scored: list[tuple[float, str]] = []
    for rgb, count in buckets.items():
        hue, saturation, value = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        score = saturation * 180 + value * 70 + math.log(max(1, count), 10) * 18
        scored.append((score, _hex(rgb)))
    return [color for _score, color in sorted(scored, reverse=True)[:limit]]


def _guide_grid_score(images: list[Image.Image]) -> int:
    score = 0
    for image in images:
        sample = image.convert("RGB")
        sample.thumbnail((320, 180), Image.Resampling.LANCZOS)
        width, height = sample.size
        pixels = sample.load()
        if pixels is None or width <= 0 or height <= 0:
            continue

        row_hits = 0
        for y in range(height):
            light_neutral = 0
            for x in range(width):
                rgb = pixels[x, y]
                if _luma(rgb) > 150 and _saturation(rgb) < 0.24:
                    light_neutral += 1
            if light_neutral / width > 0.14:
                row_hits += 1

        col_hits = 0
        for x in range(width):
            light_neutral = 0
            for y in range(height):
                rgb = pixels[x, y]
                if _luma(rgb) > 150 and _saturation(rgb) < 0.24:
                    light_neutral += 1
            if light_neutral / height > 0.14:
                col_hits += 1

        score += min(8, row_hits + col_hits)
    return score


def _create_preview(path: Path, images: list[Image.Image], palette: list[str]) -> None:
    width, height = 720, 180
    canvas = Image.new("RGB", (width, height), (245, 245, 242))
    draw = ImageDraw.Draw(canvas)
    x = 12
    for image in images[:5]:
        thumb = image.convert("RGB")
        thumb.thumbnail((116, 116), Image.Resampling.LANCZOS)
        y = 14 + (116 - thumb.height) // 2
        canvas.paste(thumb, (x + (116 - thumb.width) // 2, y))
        draw.rectangle((x, 14, x + 116, 130), outline=(220, 220, 216), width=1)
        x += 128
    swatch_y = 146
    swatch_w = max(24, min(86, (width - 24) // max(1, len(palette[:8]))))
    for index, color in enumerate(palette[:8]):
        left = 12 + index * swatch_w
        draw.rectangle((left, swatch_y, left + swatch_w - 4, swatch_y + 22), fill=color)
    canvas.save(path, quality=92)


def _profile_from_images(preset_id: str, name: str, images: list[Image.Image], sample_files: list[str]) -> dict[str, Any]:
    all_pixels: list[tuple[int, int, int]] = []
    edge_pixels: list[tuple[int, int, int]] = []
    palette_counts: Counter[str] = Counter()
    contrast_values: list[float] = []
    for image in images:
        pixels = _sample_pixels(image)
        if pixels:
            all_pixels.extend(pixels)
            stat_image = image.convert("L")
            stat_image.thumbnail((160, 160), Image.Resampling.LANCZOS)
            contrast_values.append(float(ImageStat.Stat(stat_image).stddev[0]))
        edge_pixels.extend(_edge_pixels(image))
        palette_counts.update(_palette_from_image(image))

    background = _average_color(edge_pixels or all_pixels)
    foreground = _contrast_color(background)
    palette = palette_counts.most_common(16)
    accent = _pick_accent(palette, background, foreground)
    accent_palette = _accent_palette(palette, background, foreground)
    for color in _vivid_pixel_palette(all_pixels, background):
        if color not in accent_palette:
            accent_palette.append(color)
    if accent_palette and accent == foreground:
        accent = accent_palette[0]
    avg_saturation = sum(_saturation(pixel) for pixel in all_pixels[:: max(1, len(all_pixels) // 4000)]) / max(1, min(4000, len(all_pixels)))
    avg_contrast = sum(contrast_values) / max(1, len(contrast_values))
    light_canvas = _luma(background) > 150
    high_energy = avg_saturation > 0.34 or avg_contrast > 58
    grid_score = _guide_grid_score(images)
    dark_canvas = not light_canvas
    editorial_grid = dark_canvas and (grid_score >= 4 or (avg_contrast > 46 and len(accent_palette) >= 2))

    overlay_background = "rgba(255, 255, 255, 0.24)" if light_canvas else "rgba(0, 0, 0, 0.30)"
    style_family = "user-reference"
    motion_energy = "high" if high_energy else "measured"
    enter_animation = "pop" if high_energy else "rise"
    easing = "power" if high_energy else "sine"
    font_family = "Manrope"
    font_weight = 700 if high_energy else 600
    rules = [
        "Use these reference-derived style tokens before inventing colors.",
        "Keep overlays readable at video scale.",
        "Keep text accents away from faces when possible.",
        "Preserve Figma and LTX layers as independent sources of truth.",
    ]
    if editorial_grid:
        style_family = "editorial-grid"
        overlay_background = "rgba(12, 14, 15, 0.82)"
        motion_energy = "high"
        enter_animation = "slide"
        easing = "power"
        font_family = "Arial Narrow"
        font_weight = 900
        rules = [
            "Use a dark editorial poster grid: charcoal panels, dotted guide lines, crop marks, and strong blocks.",
            "Use condensed uppercase typography and high contrast instead of rounded glass cards.",
            "Prefer vivid accent panels from the reference palette for emphasis.",
            "Keep overlays readable at video scale and away from faces when possible.",
            "Preserve Figma and LTX layers as independent sources of truth.",
        ]

    return {
        "version": 1,
        "preset_id": preset_id,
        "name": name,
        "source": "user-image-batch",
        "style_name": name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reference_count": len(images),
        "style_family": style_family,
        "palette": [color for color, _count in palette],
        "tokens": {
            "background_color": _hex(background),
            "foreground_color": foreground,
            "accent": accent,
            "accent_palette": accent_palette,
            "overlay_background": overlay_background,
            "panel_background": "rgba(12, 14, 15, 0.86)" if editorial_grid else overlay_background,
            "guide_color": "rgba(255, 255, 255, 0.62)" if editorial_grid else "rgba(255, 255, 255, 0.28)",
            "shape_language": "editorial-grid" if editorial_grid else "soft-overlay",
            "font_family": font_family,
            "font_weight": font_weight,
            "motion_energy": motion_energy,
            "enter_animation": enter_animation,
            "exit_animation": "fade",
            "easing": easing,
        },
        "rules": rules,
        "sample_files": sample_files,
    }


def create_style_preset(name: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
    clean_name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not clean_name:
        raise ValueError("Style preset name is required")
    if not files:
        raise ValueError("Upload at least one reference image")

    preset_id = f"{_slugify(clean_name)}-{uuid.uuid4().hex[:8]}"
    base = STYLE_PRESETS_DIR / preset_id
    refs = base / "refs"
    refs.mkdir(parents=True, exist_ok=True)

    images: list[Image.Image] = []
    sample_files: list[str] = []
    for index, (filename, data) in enumerate(files[:32], start=1):
        if not data:
            continue
        image = _read_image(data)
        images.append(image)
        suffix = Path(filename or "").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        ref_path = refs / f"ref_{index:02d}{suffix}"
        ref_path.write_bytes(data)
        sample_files.append(str(ref_path.relative_to(settings.project_root)))

    if not images:
        raise ValueError("No readable images were uploaded")

    profile = _profile_from_images(preset_id, clean_name, images, sample_files)
    preview_path = base / "preview.jpg"
    _create_preview(preview_path, images, profile.get("palette", []))
    profile["preview_path"] = str(preview_path.relative_to(settings.project_root))
    profile_path = base / "preset.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def load_style_preset(preset_id: str | None) -> dict[str, Any] | None:
    if not preset_id:
        return None
    if str(preset_id) == SOFT_NEUMORPHISM_PRESET_ID:
        return soft_neumorphism_profile()
    if str(preset_id) == FROSTED_GLASS_PRESET_ID:
        return frosted_glass_profile()
    if str(preset_id) == WARM_TEAL_UI_PRESET_ID:
        return warm_teal_ui_profile()
    return None


def list_style_presets() -> list[dict[str, Any]]:
    STYLE_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    return [soft_neumorphism_summary(), frosted_glass_summary(), warm_teal_ui_summary()]


def apply_style_to_motion(motion: MotionSpec, style_profile: dict[str, Any] | None) -> MotionSpec:
    if not style_profile:
        return motion
    tokens = dict(style_profile.get("tokens") or {})
    style_family = str(style_profile.get("style_family") or tokens.get("shape_language") or "")
    plan = dict(motion.motion_plan or {})
    directed_agent_motion = (
        plan.get("engine") == "video-use-hyperframes-style-plan"
        or plan.get("engine") == "native-motion-cue"
        or isinstance(plan.get("agent_slot"), dict)
        or isinstance(plan.get("agent_beat"), dict)
        or isinstance(plan.get("native_motion_cue"), dict)
    )
    updates: dict[str, Any] = {}
    if tokens.get("overlay_background"):
        updates["background"] = str(tokens["overlay_background"])
    if tokens.get("accent"):
        updates["accent"] = str(tokens["accent"])
    if style_family == "soft-neumorphism" and getattr(motion, "source_type", "generated") != "figma":
        updates["design_preset"] = SOFT_NEUMORPHISM_PRESET_ID
    if style_family == "frosted-glass" and getattr(motion, "source_type", "generated") != "figma":
        updates["design_preset"] = FROSTED_GLASS_PRESET_ID
    if style_family == "warm-teal-ui" and getattr(motion, "source_type", "generated") != "figma":
        updates["design_preset"] = WARM_TEAL_UI_PRESET_ID
    if style_family == "editorial-grid" and motion.design_preset in {"glass", "liquid-glass", "creator-vibe"}:
        updates["design_preset"] = "data-panel" if motion.height >= 150 else "bold-caption"
    if not directed_agent_motion and str(tokens.get("enter_animation") or "") in {"none", "slide", "fade", "pop", "rise", "drop"}:
        updates["enter_animation"] = str(tokens["enter_animation"])
    if not directed_agent_motion and str(tokens.get("exit_animation") or "") in {"none", "slide", "fade", "pop", "rise", "drop"}:
        updates["exit_animation"] = str(tokens["exit_animation"])
    if not directed_agent_motion and str(tokens.get("easing") or "") in {"expo", "power", "sine", "linear"}:
        updates["easing"] = str(tokens["easing"])
    plan["style"] = style_profile
    updates["motion_plan"] = plan
    return motion.model_copy(update=updates) if updates else motion
