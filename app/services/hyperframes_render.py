from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from app.models.schemas import MotionSpec


class HyperframesRenderError(RuntimeError):
    pass


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _motion_class(preset: str) -> str:
    if preset == "soft-neumorphism":
        return "hf-soft"
    if preset == "frosted-glass":
        return "hf-frosted"
    if preset == "warm-teal-ui":
        return "hf-warm"
    if preset == "creator-vibe":
        return "hf-creator"
    if preset == "data-panel":
        return "hf-data"
    if preset == "bold-caption":
        return "hf-bold"
    return "hf-glass"


def _creator_background(background: str) -> str:
    if "0, 0, 0" in background or background.startswith("#000"):
        return "rgba(0, 0, 0, 0.24)"
    return "rgba(255, 255, 255, 0.24)"


def _motion_style_profile(motion: MotionSpec) -> dict:
    plan = motion.motion_plan if isinstance(motion.motion_plan, dict) else {}
    style = plan.get("style") if isinstance(plan.get("style"), dict) else {}
    return style


def _motion_style_tokens(motion: MotionSpec) -> dict:
    style = _motion_style_profile(motion)
    tokens = style.get("tokens") if isinstance(style.get("tokens"), dict) else {}
    return tokens


def _motion_style_family(motion: MotionSpec) -> str:
    style = _motion_style_profile(motion)
    tokens = _motion_style_tokens(motion)
    return str(style.get("style_family") or tokens.get("shape_language") or "").strip()


def _motion_style_class(motion: MotionSpec) -> str:
    if motion.design_preset == "soft-neumorphism":
        return ""
    if _motion_style_family(motion) == "editorial-grid":
        return "hf-editorial-grid"
    return ""


def _motion_style_value(motion: MotionSpec, key: str, fallback: str) -> str:
    value = _motion_style_tokens(motion).get(key)
    return str(value) if value else fallback


def _motion_accent(motion: MotionSpec, index: int) -> str:
    tokens = _motion_style_tokens(motion)
    palette = tokens.get("accent_palette")
    if isinstance(palette, list):
        accents = [str(item) for item in palette if str(item).startswith("#")]
        if accents:
            return accents[index % len(accents)]
    return motion.accent


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


def _soft_component_key(motion: MotionSpec) -> str:
    plan = motion.motion_plan if isinstance(motion.motion_plan, dict) else {}
    director = plan.get("director") if isinstance(plan.get("director"), dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    slot = plan.get("agent_slot") if isinstance(plan.get("agent_slot"), dict) else {}
    explicit = plan.get("soft_component") or director.get("soft_component") or beat.get("soft_component") or slot.get("soft_component")
    semantic_text = " ".join(
        str(value or "")
        for value in (
            motion.text,
            motion.prompt,
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


def _motion_markup(motion: MotionSpec, index: int) -> str:
    text = _escape(motion.text.strip() or "Text")
    plan = motion.motion_plan if isinstance(motion.motion_plan, dict) else {}
    beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
    beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
    eyebrow = str(beat.get("eyebrow") or "").strip()
    label = eyebrow or "INSIGHT"
    if motion.design_preset == "bold-caption" and not eyebrow:
        label = "CALLOUT"
    elif motion.design_preset == "data-panel" and not eyebrow:
        label = "SIGNAL"
    if beat_id:
        label = f"{beat_id} · {label}"

    style = (
        f"--x:{motion.x}px;--y:{motion.y}px;--w:{motion.width}px;--h:{motion.height}px;"
        f"--accent:{_escape(_motion_accent(motion, index))};"
        f"--panel-bg:{_escape(_motion_style_value(motion, 'panel_background', motion.background))};"
        f"--style-ink:{_escape(_motion_style_value(motion, 'foreground_color', '#ffffff'))};"
        f"--style-guide:{_escape(_motion_style_value(motion, 'guide_color', 'rgba(255, 255, 255, 0.42)'))};"
    )
    class_name = " ".join(part for part in [_motion_class(motion.design_preset), _motion_style_class(motion)] if part)

    if motion.design_preset == "soft-neumorphism":
        component = _soft_component_key(motion)
        soft_label = _escape(label[:28]) if (beat_id or eyebrow) else ""
        return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name} soft-component-{component}" data-variant="{index % 5}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}">
      <div class="hf-card" data-layout-ignore>
        <div class="soft-head">
          <span>{soft_label}</span>
          <i></i>
        </div>
        <div class="soft-main">
          <div class="soft-slot" aria-hidden="true"><span></span><b></b></div>
          <div class="motion-text">{text}</div>
          <span class="soft-toggle-knob" aria-hidden="true"></span>
        </div>
        <div class="soft-rows" aria-hidden="true">
          <span></span><span></span><span></span>
        </div>
        <div class="soft-control" aria-hidden="true"><span></span><b></b></div>
      </div>
    </div>"""

    if motion.design_preset == "frosted-glass":
        component = _soft_component_key(motion)
        frost_label = _escape((label if (beat_id or eyebrow) else "OPTION").upper()[:28])
        return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name} frosted-component-{component}" data-variant="{index % 4}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}">
      <div class="hf-card" data-layout-ignore>
        <div class="frosted-head">{frost_label}</div>
        <div class="motion-text">{text}</div>
        <pre class="frosted-code" aria-hidden="true"><span>&lt;div class="clip"&gt;</span><span>  data-start="1.5"</span><span>  data-duration="3.0"</span><span>&lt;/div&gt;</span></pre>
        <div class="frosted-rows" aria-hidden="true"><span></span><span></span><span></span></div>
        <div class="frosted-control" aria-hidden="true"><span></span><b></b></div>
      </div>
    </div>"""

    if motion.design_preset == "warm-teal-ui":
        component = _soft_component_key(motion)
        warm_label = _escape((label if (beat_id or eyebrow) else "CONTROL").upper()[:28])
        return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name} warm-component-{component}" data-variant="{index % 5}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}">
      <div class="hf-card" data-layout-ignore>
        <div class="warm-head">{warm_label}</div>
        <div class="motion-text">{text}</div>
        <div class="warm-field" aria-hidden="true"><span></span><b></b></div>
        <div class="warm-rows" aria-hidden="true"><span></span><span></span><span></span></div>
        <div class="warm-control" aria-hidden="true"><span></span><b></b></div>
        <span class="warm-toggle-knob" aria-hidden="true"></span>
        <span class="warm-check" aria-hidden="true"></span>
      </div>
    </div>"""

    if motion.design_preset == "creator-vibe":
        background = _creator_background(motion.background)
        font_size = max(10, min(90, float(motion.height) * 0.20 * float(getattr(motion, "text_scale", 1.0) or 1.0)))
        return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name}" data-variant="{index % 4}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}--motion-bg:{_escape(background)};--motion-font-size:{font_size:.2f}px;">
      <div class="hf-card" data-layout-ignore>
        <div class="motion-text">{text}</div>
      </div>
    </div>"""

    if motion.design_preset == "data-panel":
        return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name}" data-variant="{index % 4}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}">
      <div class="hf-card" data-layout-ignore>
        <div class="panel-head">
          <span class="dot"></span>
          <span>{label}</span>
        </div>
        <div class="bars" aria-hidden="true">
          <i style="--b:42%"></i><i style="--b:64%"></i><i style="--b:54%"></i><i style="--b:88%"></i>
        </div>
        <div class="motion-text">{text}</div>
      </div>
    </div>"""

    if motion.design_preset == "bold-caption":
        return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name}" data-variant="{index % 4}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}">
      <div class="hf-card" data-layout-ignore>
        <div class="motion-label">{label}</div>
        <div class="motion-text">{text}</div>
        <div class="caption-stripe" aria-hidden="true"></div>
      </div>
    </div>"""

    return f"""
    <div id="motion-{index}" class="clip motion-clip {class_name}" data-variant="{index % 4}" data-start="{motion.start:.3f}" data-duration="{motion.duration:.3f}" data-track-index="{index + 4}" style="{style}">
      <div class="hf-card" data-layout-ignore>
        <div class="shine" aria-hidden="true"></div>
        <div class="edge edge-a" aria-hidden="true"></div>
        <div class="edge edge-b" aria-hidden="true"></div>
        <div class="corner-glow corner-a" aria-hidden="true"></div>
        <div class="corner-glow corner-b" aria-hidden="true"></div>
        <div class="motion-text">{text}</div>
      </div>
    </div>"""


def _gsap_ease(easing: str, phase: str) -> str:
    direction = "out" if phase == "enter" else "in"
    if easing == "linear":
        return "none"
    if easing == "sine":
        return f"sine.{direction}"
    if easing == "power":
        return f"power3.{direction}"
    return f"expo.{direction}"


def _direction_offset(motion: MotionSpec, width: int, height: int, direction: str) -> tuple[int, int]:
    pad = 80
    if direction == "left":
        return -(motion.x + motion.width + pad), 0
    if direction == "right":
        return width - motion.x + pad, 0
    if direction == "top":
        return 0, -(motion.y + motion.height + pad)
    if direction == "bottom":
        return 0, height - motion.y + pad
    return 0, 0


def _js_vars(values: dict[str, object]) -> str:
    rows = []
    for key, value in values.items():
        if isinstance(value, str):
            rows.append(f"{key}: '{value}'")
        else:
            rows.append(f"{key}: {value}")
    return "{ " + ", ".join(rows) + " }"


def _enter_vars(motion: MotionSpec, width: int, height: int) -> tuple[str, str]:
    animation = getattr(motion, "enter_animation", "slide")
    if animation == "fade":
        from_vars = {"opacity": 0, "filter": "blur(8px)"}
    elif animation == "pop":
        from_vars = {"opacity": 0, "scale": 0.88, "filter": "blur(8px)"}
    elif animation == "rise":
        from_vars = {"y": 36, "opacity": 0, "scale": 0.98, "filter": "blur(8px)"}
    elif animation == "drop":
        y = _direction_offset(motion, width, height, "top")[1]
        from_vars = {"y": y, "opacity": 0, "scale": 0.98, "filter": "blur(8px)"}
    elif animation == "none":
        from_vars = {"opacity": 1}
    else:
        x, y = _direction_offset(motion, width, height, getattr(motion, "enter_from", "right"))
        from_vars = {"x": x, "y": y, "opacity": 0, "filter": "blur(6px)"}

    to_vars = {
        "x": 0,
        "y": 0,
        "scale": 1,
        "opacity": 1,
        "filter": "blur(0px)",
        "duration": max(0.05, min(20.0, float(getattr(motion, "enter_duration", 0.45) or 0.45))),
        "ease": _gsap_ease(getattr(motion, "easing", "expo"), "enter"),
    }
    return _js_vars(from_vars), _js_vars(to_vars)


def _exit_vars(motion: MotionSpec, width: int, height: int) -> str:
    animation = getattr(motion, "exit_animation", "slide")
    if animation == "fade":
        values = {"opacity": 0, "filter": "blur(8px)"}
    elif animation == "pop":
        values = {"opacity": 0, "scale": 0.92, "filter": "blur(8px)"}
    elif animation == "rise":
        values = {"y": -34, "opacity": 0, "filter": "blur(8px)"}
    elif animation == "drop":
        y = _direction_offset(motion, width, height, "bottom")[1]
        values = {"y": y, "opacity": 0, "filter": "blur(8px)"}
    elif animation == "none":
        values = {"opacity": 1}
    else:
        x, y = _direction_offset(motion, width, height, getattr(motion, "exit_to", "left"))
        values = {"x": x, "y": y, "opacity": 0, "filter": "blur(8px)"}
    values["duration"] = max(0.05, min(20.0, float(getattr(motion, "exit_duration", 0.35) or 0.35)))
    values["ease"] = _gsap_ease(getattr(motion, "easing", "expo"), "exit")
    return _js_vars(values)


def _composition_html(width: int, height: int, duration: float, motions: list[MotionSpec], has_audio: bool) -> str:
    motion_nodes = "\n".join(_motion_markup(motion, index) for index, motion in enumerate(motions))
    audio_node = ""
    if has_audio:
        audio_node = f'    <audio id="base-audio" class="clip" data-start="0" data-duration="{duration:.3f}" data-track-index="1" src="media/audio.m4a" data-volume="1" crossorigin="anonymous"></audio>'
    animation_rows = []
    for index, motion in enumerate(motions):
        start = max(0.0, float(motion.start))
        duration_value = max(0.2, float(motion.duration))
        exit_duration = max(0.05, min(20.0, float(getattr(motion, "exit_duration", 0.35) or 0.35)))
        exit_at = max(start + 0.05, start + duration_value - exit_duration)
        enter_from, enter_to = _enter_vars(motion, width, height)
        exit_to = _exit_vars(motion, width, height)
        kill_at = min(start + duration_value, exit_at + exit_duration + 0.02)
        row = f"""
      tl.set("#motion-{index} .hf-card", {{ opacity: 0, x: 0, y: 0, scale: 1, filter: "blur(0px)" }}, 0);
      tl.fromTo("#motion-{index} .hf-card", {enter_from}, {enter_to}, {start + 0.08:.3f});
      tl.to("#motion-{index} .hf-card", {exit_to}, {exit_at:.3f});
      tl.set("#motion-{index} .hf-card", {{ opacity: 0 }}, {kill_at:.3f});"""
        if motion.design_preset == "soft-neumorphism":
            row += f"""
      tl.from("#motion-{index} .motion-text", {{ opacity: 0, y: 12, duration: 0.34, ease: "sine.out" }}, {start + 0.18:.3f});
      tl.from("#motion-{index}.soft-component-hero .soft-head", {{ opacity: 0, y: -10, duration: 0.30, ease: "sine.out" }}, {start + 0.16:.3f});
      tl.from("#motion-{index}.soft-component-hero .soft-slot, #motion-{index}.soft-component-card .soft-slot", {{ opacity: 0, y: -10, duration: 0.34, ease: "sine.out" }}, {start + 0.22:.3f});
      tl.from("#motion-{index}.soft-component-rows .soft-rows span", {{ opacity: 0, x: -18, stagger: 0.055, duration: 0.30, ease: "sine.out" }}, {start + 0.24:.3f});
      tl.fromTo("#motion-{index}.soft-component-slider .soft-control span", {{ scaleX: 0, transformOrigin: "left center" }}, {{ scaleX: 1, duration: 0.58, ease: "power2.out" }}, {start + 0.28:.3f});
      tl.from("#motion-{index}.soft-component-slider .soft-control b", {{ x: -42, duration: 0.50, ease: "power2.out" }}, {start + 0.28:.3f});
      tl.from("#motion-{index}.soft-component-toggle .soft-toggle-knob", {{ x: -24, duration: 0.42, ease: "sine.out" }}, {start + 0.28:.3f});"""
        if motion.design_preset == "frosted-glass":
            row += f"""
      tl.from("#motion-{index} .motion-text", {{ opacity: 0, y: 14, duration: 0.34, ease: "sine.out" }}, {start + 0.18:.3f});
      tl.from("#motion-{index} .frosted-head", {{ opacity: 0, y: -8, duration: 0.28, ease: "sine.out" }}, {start + 0.16:.3f});
      tl.from("#motion-{index} .frosted-code span", {{ opacity: 0, x: -12, stagger: 0.045, duration: 0.24, ease: "sine.out" }}, {start + 0.28:.3f});
      tl.from("#motion-{index}.frosted-component-rows .frosted-rows span", {{ opacity: 0, x: -18, stagger: 0.055, duration: 0.28, ease: "sine.out" }}, {start + 0.26:.3f});
      tl.fromTo("#motion-{index}.frosted-component-slider .frosted-control span", {{ scaleX: 0, transformOrigin: "left center" }}, {{ scaleX: 1, duration: 0.52, ease: "power2.out" }}, {start + 0.28:.3f});
      tl.from("#motion-{index}.frosted-component-slider .frosted-control b", {{ x: -34, duration: 0.46, ease: "power2.out" }}, {start + 0.28:.3f});"""
        if motion.design_preset == "warm-teal-ui":
            row += f"""
      tl.from("#motion-{index} .motion-text", {{ opacity: 0, y: 12, duration: 0.34, ease: "sine.out" }}, {start + 0.18:.3f});
      tl.from("#motion-{index}.warm-component-hero .warm-head, #motion-{index}.warm-component-card .warm-head, #motion-{index}.warm-component-rows .warm-head", {{ opacity: 0, y: -8, duration: 0.28, ease: "sine.out" }}, {start + 0.16:.3f});
      tl.from("#motion-{index}.warm-component-card .warm-field, #motion-{index}.warm-component-hero .warm-field", {{ opacity: 0, y: -10, duration: 0.32, ease: "sine.out" }}, {start + 0.24:.3f});
      tl.from("#motion-{index}.warm-component-rows .warm-rows span", {{ opacity: 0, x: -16, stagger: 0.05, duration: 0.28, ease: "sine.out" }}, {start + 0.26:.3f});
      tl.fromTo("#motion-{index}.warm-component-slider .warm-control span", {{ scaleX: 0, transformOrigin: "left center" }}, {{ scaleX: 1, duration: 0.58, ease: "power2.out" }}, {start + 0.28:.3f});
      tl.from("#motion-{index}.warm-component-slider .warm-control b", {{ x: -42, duration: 0.50, ease: "power2.out" }}, {start + 0.28:.3f});
      tl.from("#motion-{index}.warm-component-toggle .warm-toggle-knob", {{ x: -24, duration: 0.42, ease: "sine.out" }}, {start + 0.26:.3f});
      tl.from("#motion-{index}.warm-component-check .warm-check", {{ scale: 0.72, opacity: 0, duration: 0.30, ease: "back.out(1.5)" }}, {start + 0.24:.3f});"""
        if motion.design_preset == "data-panel":
            row += f"""
      tl.from("#motion-{index} .bars i", {{ scaleY: 0.2, transformOrigin: "bottom", stagger: 0.045, duration: 0.42, ease: "back.out(1.6)" }}, {start + 0.20:.3f});"""
        animation_rows.append(row)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VibeMotion v0.1.0 pre-alpha Hyperframes Preview</title>
  <style>
    :root {{
      --ink: #0d1424;
      --paper: rgba(246, 250, 255, 0.58);
      --line: rgba(255, 255, 255, 0.74);
      --shadow: rgba(7, 10, 18, 0.22);
      --deep-shadow: rgba(7, 10, 18, 0.34);
    }}
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      background: #000;
      overflow: hidden;
      font-family: "Segoe UI", Arial, "Helvetica Neue", sans-serif;
    }}
    [data-composition-id="vibemotion-preview"] {{
      position: relative;
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      background: #000;
      color: var(--ink);
    }}
    video, audio {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }}
    video {{
      object-fit: contain;
      background: #000;
    }}
    .motion-clip {{
      position: absolute;
      left: var(--x);
      top: var(--y);
      width: var(--w);
      height: var(--h);
      z-index: 20;
      pointer-events: none;
    }}
    .hf-card {{
      position: relative;
      width: 100%;
      height: 100%;
      box-sizing: border-box;
      transform-origin: 18% 55%;
      will-change: transform, opacity, filter;
    }}
    .hf-soft .hf-card {{
      display: flex;
      flex-direction: column;
      gap: clamp(8px, calc(var(--h) * 0.06), 16px);
      padding: clamp(14px, calc(var(--h) * 0.12), 28px);
      border-radius: clamp(22px, calc(var(--h) * 0.18), 36px);
      overflow: hidden;
      color: #171a1d;
      background: linear-gradient(145deg, rgba(255,255,255,0.30), rgba(226,225,221,0.22)), rgba(242, 241, 237, 0.98);
      border: 1px solid rgba(255,255,255,0.66);
      box-shadow:
        16px 20px 38px rgba(56, 58, 55, 0.24),
        -10px -10px 26px rgba(255,255,255,0.50),
        inset 1px 1px 0 rgba(255,255,255,0.72);
      backdrop-filter: none;
    }}
    .hf-soft .soft-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: clamp(18px, calc(var(--h) * 0.14), 30px);
      gap: 14px;
      color: #2a2d30;
      font-size: clamp(11px, calc(var(--h) * 0.085), 16px);
      line-height: 1;
      font-weight: 700;
    }}
    .hf-soft .soft-head i {{
      width: clamp(18px, calc(var(--h) * 0.13), 28px);
      height: clamp(13px, calc(var(--h) * 0.09), 22px);
      border-radius: 999px;
      background:
        linear-gradient(#34383c, #34383c) center 30% / 70% 2px no-repeat,
        linear-gradient(#34383c, #34383c) center 50% / 70% 2px no-repeat,
        linear-gradient(#34383c, #34383c) center 70% / 70% 2px no-repeat;
      opacity: 0.78;
    }}
    .hf-soft .soft-main {{
      position: relative;
      z-index: 2;
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 0;
    }}
    .hf-soft .soft-toggle-knob {{
      display: none;
    }}
    .hf-soft .motion-text {{
      color: #16191c;
      font-family: "Segoe UI", Arial, "Helvetica Neue", sans-serif;
      font-size: clamp(18px, min(calc(var(--h) * 0.20), calc(var(--w) * 0.080)), 38px);
      line-height: 1.02;
      letter-spacing: 0;
      font-weight: 750;
      text-shadow: 0 1px 0 rgba(255,255,255,0.62);
      text-wrap: balance;
      overflow-wrap: anywhere;
    }}
    .hf-soft .soft-slot {{
      height: clamp(24px, calc(var(--h) * 0.22), 50px);
      margin-bottom: clamp(8px, calc(var(--h) * 0.07), 16px);
      border-radius: clamp(12px, calc(var(--h) * 0.10), 20px);
      background: rgba(236, 235, 231, 0.92);
      box-shadow:
        inset 5px 5px 12px rgba(132,132,128,0.30),
        inset -6px -6px 13px rgba(255,255,255,0.72);
    }}
    .hf-soft .soft-slot span {{
      display: block;
      width: 34%;
      height: 4px;
      margin: clamp(10px, calc(var(--h) * 0.08), 18px) 0 0 clamp(12px, calc(var(--w) * 0.04), 26px);
      border-radius: 99px;
      background: rgba(117, 119, 118, 0.54);
    }}
    .hf-soft .soft-slot b {{
      display: block;
      width: 5px;
      height: 5px;
      margin: -4px 0 0 calc(clamp(12px, calc(var(--w) * 0.04), 26px) + 39%);
      border-radius: 50%;
      background: rgba(86, 89, 89, 0.60);
    }}
    .hf-soft .soft-control {{
      position: absolute;
      left: clamp(16px, calc(var(--w) * 0.05), 34px);
      right: clamp(16px, calc(var(--w) * 0.05), 34px);
      bottom: clamp(13px, calc(var(--h) * 0.09), 26px);
      height: clamp(10px, calc(var(--h) * 0.07), 16px);
      border-radius: 999px;
      background: rgba(235,234,230,0.92);
      box-shadow: inset 4px 4px 10px rgba(130,130,126,0.25), inset -5px -5px 11px rgba(255,255,255,0.76);
      opacity: 0;
    }}
    .hf-soft .soft-control span {{
      position: absolute;
      left: 3px;
      top: 50%;
      width: 46%;
      height: 4px;
      transform: translateY(-50%);
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), color-mix(in srgb, var(--accent) 40%, white));
    }}
    .hf-soft .soft-control b {{
      position: absolute;
      left: 44%;
      top: 50%;
      width: clamp(20px, calc(var(--h) * 0.16), 32px);
      height: clamp(20px, calc(var(--h) * 0.16), 32px);
      transform: translate(-50%, -50%);
      border-radius: 999px;
      background: #e7e6e2;
      box-shadow: 5px 6px 12px rgba(86,86,82,0.25), -4px -4px 10px rgba(255,255,255,0.70);
    }}
    .hf-soft .soft-rows {{
      display: none;
    }}
    .hf-soft .soft-head i,
    .hf-soft .soft-slot,
    .hf-soft .soft-rows,
    .hf-soft .soft-toggle-knob {{
      display: none;
    }}
    .hf-soft.soft-component-hero .hf-card {{
      justify-content: center;
      padding: clamp(18px, calc(var(--h) * 0.12), 34px);
      background:
        radial-gradient(circle at 18% 14%, rgba(255,255,255,0.88), transparent 24%),
        linear-gradient(145deg, rgba(255,255,255,0.40), rgba(226,225,221,0.20)),
        rgba(242, 241, 237, 0.99);
    }}
    .hf-soft.soft-component-hero .soft-head {{
      display: flex;
    }}
    .hf-soft.soft-component-hero .soft-head i,
    .hf-soft.soft-component-hero .soft-slot {{
      display: block;
    }}
    .hf-soft.soft-component-hero .soft-slot {{
      max-width: 72%;
      height: clamp(26px, calc(var(--h) * 0.18), 54px);
    }}
    .hf-soft.soft-component-hero .motion-text {{
      max-width: 88%;
      font-size: clamp(28px, min(calc(var(--h) * 0.25), calc(var(--w) * 0.090)), 58px);
      line-height: 0.96;
      font-weight: 820;
    }}
    .hf-soft.soft-component-callout .hf-card {{
      justify-content: center;
      padding: clamp(13px, calc(var(--h) * 0.14), 24px) clamp(20px, calc(var(--w) * 0.07), 38px);
      border-radius: clamp(18px, calc(var(--h) * 0.24), 34px);
    }}
    .hf-soft.soft-component-callout .soft-head {{
      display: none;
    }}
    .hf-soft.soft-component-callout .motion-text {{
      font-size: clamp(18px, min(calc(var(--h) * 0.25), calc(var(--w) * 0.075)), 34px);
      line-height: 1.0;
    }}
    .hf-soft.soft-component-card .soft-head i {{
      display: block;
    }}
    .hf-soft.soft-component-card .soft-slot {{
      display: block;
    }}
    .hf-soft.soft-component-text .hf-card {{
      justify-content: center;
      gap: clamp(4px, calc(var(--h) * 0.035), 10px);
    }}
    .hf-soft.soft-component-text .soft-head {{
      display: none;
    }}
    .hf-soft.soft-component-toggle .soft-main {{
      border-radius: 999px;
      padding: 0 clamp(42px, calc(var(--h) * 0.30), 72px) 0 clamp(18px, calc(var(--h) * 0.16), 34px);
      background: rgba(241,240,236,0.95);
      box-shadow: inset 6px 6px 14px rgba(128,128,124,0.26), inset -7px -7px 16px rgba(255,255,255,0.76);
    }}
    .hf-soft.soft-component-toggle .soft-toggle-knob {{
      display: block;
      position: absolute;
      right: clamp(8px, calc(var(--h) * 0.08), 16px);
      top: 50%;
      width: clamp(34px, calc(var(--h) * 0.30), 56px);
      height: clamp(34px, calc(var(--h) * 0.30), 56px);
      transform: translateY(-50%);
      border-radius: 999px;
      background: #e5e4e0;
      box-shadow: 7px 8px 16px rgba(86,86,82,0.25), -5px -5px 14px rgba(255,255,255,0.70);
    }}
    .hf-soft.soft-component-check .hf-card {{
      padding-right: clamp(58px, calc(var(--h) * 0.42), 94px);
    }}
    .hf-soft.soft-component-check .hf-card::after {{
      content: "";
      position: absolute;
      right: clamp(16px, calc(var(--h) * 0.12), 28px);
      bottom: clamp(16px, calc(var(--h) * 0.12), 28px);
      width: clamp(32px, calc(var(--h) * 0.26), 52px);
      height: clamp(32px, calc(var(--h) * 0.26), 52px);
      border-radius: 999px;
      background:
        linear-gradient(135deg, transparent 44%, #555 44% 52%, transparent 52%) 42% 56% / 42% 42% no-repeat,
        #e7e6e2;
      box-shadow: 6px 7px 15px rgba(82,82,78,0.25), -5px -5px 13px rgba(255,255,255,0.70);
    }}
    .hf-soft.soft-component-rows .soft-rows {{
      display: flex;
      flex-direction: column;
      gap: 0;
      margin-top: auto;
      border-radius: 20px;
      overflow: hidden;
      box-shadow: inset 1px 1px 0 rgba(255,255,255,0.58);
    }}
    .hf-soft.soft-component-rows .soft-rows span {{
      height: clamp(22px, calc(var(--h) * 0.15), 36px);
      border-top: 1px solid rgba(36, 38, 40, 0.13);
      background:
        radial-gradient(circle at calc(100% - 22px) 50%, #e2e1dd 0 10px, transparent 11px),
        linear-gradient(90deg, rgba(24,27,31,0.14) 0 8px, transparent 8px 30px, rgba(24,27,31,0.10) 30px 50%, transparent 50%);
    }}
    .hf-soft.soft-component-slider .soft-control {{
      opacity: 1;
    }}
    .hf-soft.soft-component-slider .motion-text {{
      margin-bottom: clamp(20px, calc(var(--h) * 0.20), 42px);
    }}
    .hf-frosted .hf-card {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: clamp(8px, calc(var(--h) * 0.055), 16px);
      padding: clamp(16px, calc(var(--h) * 0.13), 30px);
      border-radius: clamp(16px, calc(var(--h) * 0.13), 28px);
      overflow: hidden;
      color: #1d2228;
      background:
        linear-gradient(145deg, rgba(255,255,255,0.64), rgba(235,240,238,0.38)),
        rgba(246,248,246,0.54);
      border: 1px solid rgba(255,255,255,0.58);
      box-shadow:
        0 24px 54px rgba(14, 18, 24, 0.26),
        inset 0 1px 0 rgba(255,255,255,0.72),
        inset 0 -18px 42px rgba(86, 96, 104, 0.12);
      backdrop-filter: blur(18px) saturate(1.22);
    }}
    .hf-frosted .hf-card::before {{
      content: "";
      position: absolute;
      inset: 1px 1px auto 1px;
      height: 44%;
      border-radius: inherit;
      background: linear-gradient(180deg, rgba(255,255,255,0.36), rgba(255,255,255,0));
      pointer-events: none;
    }}
    .hf-frosted.frosted-component-text .hf-card::before,
    .hf-frosted.frosted-component-callout .hf-card::before,
    .hf-frosted.frosted-component-slider .hf-card::before {{
      content: none;
    }}
    .hf-frosted .frosted-head {{
      position: relative;
      z-index: 2;
      display: none;
      color: rgba(238, 250, 248, 0.92);
      font-size: clamp(9px, calc(var(--h) * 0.07), 14px);
      line-height: 1;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-shadow: 0 0 16px rgba(166,255,240,0.30);
    }}
    .hf-frosted .motion-text {{
      position: relative;
      z-index: 2;
      color: #20252b;
      font-size: clamp(19px, min(calc(var(--h) * 0.24), calc(var(--w) * 0.074)), 42px);
      line-height: 1.0;
      font-weight: 800;
      letter-spacing: 0;
      text-shadow: 0 1px 0 rgba(255,255,255,0.42);
      text-wrap: balance;
      overflow-wrap: anywhere;
    }}
    .hf-frosted .frosted-code,
    .hf-frosted .frosted-rows,
    .hf-frosted .frosted-control {{
      display: none;
    }}
    .hf-frosted.frosted-component-hero .hf-card,
    .hf-frosted.frosted-component-card .hf-card,
    .hf-frosted.frosted-component-rows .hf-card {{
      justify-content: flex-start;
      background:
        linear-gradient(145deg, rgba(255,255,255,0.46), rgba(207,214,211,0.30)),
        rgba(126, 134, 138, 0.42);
    }}
    .hf-frosted.frosted-component-hero .frosted-head,
    .hf-frosted.frosted-component-card .frosted-head,
    .hf-frosted.frosted-component-rows .frosted-head {{
      display: block;
    }}
    .hf-frosted.frosted-component-hero .motion-text,
    .hf-frosted.frosted-component-card .motion-text,
    .hf-frosted.frosted-component-rows .motion-text {{
      color: rgba(248, 252, 252, 0.95);
      font-size: clamp(28px, min(calc(var(--h) * 0.20), calc(var(--w) * 0.078)), 54px);
      text-shadow: 0 0 18px rgba(255,255,255,0.20);
    }}
    .hf-frosted.frosted-component-hero .frosted-code,
    .hf-frosted.frosted-component-card .frosted-code {{
      position: relative;
      z-index: 2;
      display: flex;
      flex-direction: column;
      gap: 0.35em;
      margin: clamp(8px, calc(var(--h) * 0.045), 16px) 0 0;
      padding: clamp(14px, calc(var(--h) * 0.08), 22px);
      min-height: clamp(68px, calc(var(--h) * 0.34), 150px);
      border-radius: clamp(10px, calc(var(--h) * 0.07), 18px);
      color: rgba(238,241,244,0.78);
      background: rgba(18, 21, 27, 0.58);
      border: 1px solid rgba(255,255,255,0.08);
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: clamp(10px, calc(var(--h) * 0.052), 15px);
      line-height: 1.24;
      white-space: pre;
      overflow: hidden;
    }}
    .hf-frosted .frosted-code span:nth-child(2),
    .hf-frosted .frosted-code span:nth-child(3) {{
      color: var(--accent);
      text-shadow: 0 0 14px color-mix(in srgb, var(--accent) 36%, transparent);
    }}
    .hf-frosted.frosted-component-callout .hf-card,
    .hf-frosted.frosted-component-text .hf-card {{
      justify-content: center;
      padding-inline: clamp(20px, calc(var(--w) * 0.045), 34px);
    }}
    .hf-frosted.frosted-component-callout .motion-text,
    .hf-frosted.frosted-component-text .motion-text {{
      color: #20242a;
    }}
    .hf-frosted.frosted-component-slider .frosted-control {{
      position: relative;
      z-index: 2;
      display: block;
      height: clamp(7px, calc(var(--h) * 0.055), 11px);
      margin-top: clamp(8px, calc(var(--h) * 0.075), 18px);
      border-radius: 999px;
      background: rgba(255,255,255,0.42);
      box-shadow: inset 0 1px 5px rgba(0,0,0,0.18);
    }}
    .hf-frosted.frosted-component-slider .frosted-control span {{
      position: absolute;
      inset: 0 auto 0 0;
      width: 54%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), rgba(255,255,255,0.78));
      box-shadow: 0 0 18px color-mix(in srgb, var(--accent) 36%, transparent);
    }}
    .hf-frosted.frosted-component-slider .frosted-control b {{
      position: absolute;
      left: 54%;
      top: 50%;
      width: clamp(18px, calc(var(--h) * 0.15), 28px);
      height: clamp(18px, calc(var(--h) * 0.15), 28px);
      transform: translate(-50%, -50%);
      border-radius: 999px;
      background: rgba(248,250,249,0.92);
      box-shadow: 0 8px 18px rgba(20,24,28,0.26), inset 0 1px 0 rgba(255,255,255,0.82);
    }}
    .hf-frosted.frosted-component-rows .frosted-rows {{
      position: relative;
      z-index: 2;
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-top: auto;
    }}
    .hf-frosted.frosted-component-rows .frosted-rows span {{
      height: clamp(22px, calc(var(--h) * 0.105), 34px);
      border-radius: 10px;
      background:
        linear-gradient(90deg, rgba(166,255,240,0.72) 0 18%, rgba(255,255,255,0.20) 18%),
        rgba(20,24,30,0.34);
      border: 1px solid rgba(255,255,255,0.08);
    }}
    .hf-warm .hf-card {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: clamp(8px, calc(var(--h) * 0.055), 15px);
      padding: clamp(15px, calc(var(--h) * 0.12), 28px);
      border-radius: clamp(14px, calc(var(--h) * 0.15), 26px);
      overflow: hidden;
      color: #181a18;
      background:
        linear-gradient(145deg, rgba(255,252,242,0.54), rgba(222,211,194,0.26)),
        rgba(239,231,215,0.98);
      border: 1px solid rgba(255,250,239,0.74);
      box-shadow:
        12px 16px 30px rgba(82, 67, 45, 0.24),
        -8px -8px 22px rgba(255, 253, 244, 0.58),
        inset 1px 1px 0 rgba(255,255,255,0.62);
    }}
    .hf-warm .motion-text {{
      position: relative;
      z-index: 2;
      color: #181a18;
      font-size: clamp(18px, min(calc(var(--h) * 0.22), calc(var(--w) * 0.073)), 38px);
      line-height: 1.02;
      font-weight: 800;
      letter-spacing: 0;
      text-shadow: 0 1px 0 rgba(255,250,239,0.72);
      text-wrap: balance;
      overflow-wrap: anywhere;
    }}
    .hf-warm .warm-head,
    .hf-warm .warm-field,
    .hf-warm .warm-rows,
    .hf-warm .warm-control,
    .hf-warm .warm-toggle-knob,
    .hf-warm .warm-check {{
      display: none;
    }}
    .hf-warm .warm-head {{
      position: relative;
      z-index: 2;
      color: #5b554c;
      font-size: clamp(9px, calc(var(--h) * 0.07), 13px);
      line-height: 1;
      font-weight: 850;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}
    .hf-warm.warm-component-hero .hf-card,
    .hf-warm.warm-component-card .hf-card,
    .hf-warm.warm-component-rows .hf-card {{
      justify-content: flex-start;
      gap: clamp(8px, calc(var(--h) * 0.05), 14px);
    }}
    .hf-warm.warm-component-hero .warm-head,
    .hf-warm.warm-component-card .warm-head,
    .hf-warm.warm-component-rows .warm-head {{
      display: block;
    }}
    .hf-warm.warm-component-hero .motion-text,
    .hf-warm.warm-component-card .motion-text,
    .hf-warm.warm-component-rows .motion-text {{
      font-size: clamp(25px, min(calc(var(--h) * 0.18), calc(var(--w) * 0.072)), 48px);
      line-height: 0.98;
    }}
    .hf-warm.warm-component-card .warm-field,
    .hf-warm.warm-component-hero .warm-field {{
      position: relative;
      z-index: 2;
      display: block;
      height: clamp(30px, calc(var(--h) * 0.16), 46px);
      margin-top: clamp(8px, calc(var(--h) * 0.05), 14px);
      border-radius: clamp(9px, calc(var(--h) * 0.055), 14px);
      background: rgba(230, 220, 203, 0.96);
      box-shadow:
        inset 5px 5px 12px rgba(126,108,84,0.24),
        inset -5px -5px 12px rgba(255,252,242,0.75);
    }}
    .hf-warm .warm-field span {{
      position: absolute;
      left: 16px;
      top: 50%;
      width: 44%;
      height: 4px;
      transform: translateY(-50%);
      border-radius: 999px;
      background: rgba(112, 104, 91, 0.42);
    }}
    .hf-warm .warm-field b {{
      position: absolute;
      left: calc(16px + 48%);
      top: 50%;
      width: 5px;
      height: 5px;
      transform: translateY(-50%);
      border-radius: 999px;
      background: rgba(0,109,107,0.74);
    }}
    .hf-warm.warm-component-callout .hf-card,
    .hf-warm.warm-component-text .hf-card {{
      justify-content: center;
      padding-inline: clamp(20px, calc(var(--w) * 0.06), 36px);
    }}
    .hf-warm.warm-component-slider .warm-control {{
      position: relative;
      z-index: 2;
      display: block;
      height: clamp(9px, calc(var(--h) * 0.065), 13px);
      margin-top: clamp(10px, calc(var(--h) * 0.11), 22px);
      border-radius: 999px;
      background: rgba(229, 219, 201, 0.96);
      box-shadow: inset 4px 4px 10px rgba(126,108,84,0.25), inset -5px -5px 11px rgba(255,252,242,0.78);
    }}
    .hf-warm.warm-component-slider .warm-control span {{
      position: absolute;
      inset: 2px auto 2px 2px;
      width: 58%;
      border-radius: inherit;
      background: var(--accent);
    }}
    .hf-warm.warm-component-slider .warm-control b {{
      position: absolute;
      left: 58%;
      top: 50%;
      width: clamp(20px, calc(var(--h) * 0.17), 32px);
      height: clamp(20px, calc(var(--h) * 0.17), 32px);
      transform: translate(-50%, -50%);
      border-radius: 999px;
      background: #eee5d5;
      box-shadow: 5px 6px 12px rgba(82,67,45,0.25), -4px -4px 10px rgba(255,252,242,0.72);
    }}
    .hf-warm.warm-component-toggle .hf-card {{
      border-radius: 999px;
      padding-right: clamp(56px, calc(var(--h) * 0.42), 88px);
    }}
    .hf-warm.warm-component-toggle .hf-card::before {{
      content: "";
      position: absolute;
      inset: clamp(8px, calc(var(--h) * 0.08), 14px);
      border-radius: 999px;
      background: var(--accent);
      opacity: 0.96;
      box-shadow: inset 3px 3px 9px rgba(0,0,0,0.20), inset -4px -4px 10px rgba(255,255,255,0.16);
    }}
    .hf-warm.warm-component-toggle .motion-text {{
      color: #fffdf4;
      text-shadow: none;
    }}
    .hf-warm.warm-component-toggle .warm-toggle-knob {{
      display: block;
      position: absolute;
      z-index: 3;
      right: clamp(14px, calc(var(--h) * 0.10), 22px);
      top: 50%;
      width: clamp(34px, calc(var(--h) * 0.31), 58px);
      height: clamp(34px, calc(var(--h) * 0.31), 58px);
      transform: translateY(-50%);
      border-radius: 999px;
      background: #efe7d7;
      box-shadow: 5px 7px 14px rgba(54,48,38,0.28), inset 1px 1px 0 rgba(255,255,255,0.68);
    }}
    .hf-warm.warm-component-check .hf-card {{
      padding-right: clamp(62px, calc(var(--h) * 0.46), 96px);
    }}
    .hf-warm.warm-component-check .warm-check {{
      display: block;
      position: absolute;
      right: clamp(16px, calc(var(--h) * 0.12), 28px);
      bottom: clamp(16px, calc(var(--h) * 0.12), 28px);
      width: clamp(34px, calc(var(--h) * 0.28), 54px);
      height: clamp(34px, calc(var(--h) * 0.28), 54px);
      border-radius: 12px;
      background:
        linear-gradient(135deg, transparent 43%, #fffdf4 43% 54%, transparent 54%) 44% 54% / 48% 48% no-repeat,
        var(--accent);
      box-shadow: 6px 8px 15px rgba(82,67,45,0.24), inset 1px 1px 0 rgba(255,255,255,0.35);
    }}
    .hf-warm.warm-component-rows .warm-rows {{
      position: relative;
      z-index: 2;
      display: flex;
      flex-direction: column;
      gap: 0;
      margin-top: auto;
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 7px 9px 18px rgba(82,67,45,0.16), -4px -4px 10px rgba(255,252,242,0.56);
    }}
    .hf-warm.warm-component-rows .warm-rows span {{
      height: clamp(26px, calc(var(--h) * 0.13), 40px);
      border-top: 1px solid rgba(93, 80, 61, 0.12);
      background:
        radial-gradient(circle at 20px 50%, var(--accent) 0 6px, transparent 7px) 0 0 / 100% 100% no-repeat,
        linear-gradient(90deg, transparent 0 38px, rgba(24,26,24,0.32) 38px calc(100% - 22px), transparent calc(100% - 22px)) 0 50% / 100% 2px no-repeat,
        rgba(236,227,211,0.96);
    }}
    .hf-creator .hf-card {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: clamp(18px, calc(var(--h) * 0.18), 36px) clamp(24px, calc(var(--h) * 0.22), 46px);
      border-radius: clamp(20px, calc(var(--h) * 0.16), 34px);
      overflow: hidden;
      opacity: 0;
      background: var(--motion-bg);
      border: 1px solid rgba(255, 255, 255, 0.22);
      box-shadow: 0 10px 22px rgba(7, 10, 18, 0.11);
      backdrop-filter: blur(6px) saturate(1.02);
    }}
    .hf-creator .hf-card::before {{
      content: none;
    }}
    .hf-creator .motion-text {{
      position: relative;
      z-index: 2;
      max-width: 100%;
      text-align: center;
      color: color-mix(in srgb, var(--motion-bg) 1%, #111 99%);
      font-family: Arial, "Helvetica Neue", Helvetica, sans-serif;
      font-size: var(--motion-font-size);
      line-height: 0.86;
      letter-spacing: 0;
      font-weight: 600;
      text-wrap: balance;
    }}
    .hf-creator[style*="0, 0, 0"] .motion-text {{
      color: #fff;
    }}
    .hf-glass .hf-card {{
      display: flex;
      align-items: center;
      min-height: 78px;
      padding: 18px 30px;
      border-radius: 30px;
      overflow: hidden;
      background:
        linear-gradient(145deg, rgba(255, 255, 255, 0.72), rgba(230, 241, 255, 0.34) 48%, rgba(255, 255, 255, 0.48)),
        radial-gradient(circle at 12% 16%, rgba(255, 255, 255, 0.92), transparent 22%),
        radial-gradient(circle at 88% 28%, rgba(255,255,255,0.58), transparent 26%),
        radial-gradient(circle at 88% 86%, color-mix(in srgb, var(--accent) 22%, transparent), transparent 24%);
      border: 1px solid rgba(255, 255, 255, 0.82);
      box-shadow:
        0 18px 38px var(--shadow),
        inset 0 1px 0 rgba(255, 255, 255, 0.95),
        inset 0 -16px 28px rgba(92, 117, 148, 0.13),
        0 0 0 1px rgba(86, 112, 140, 0.16);
      backdrop-filter: blur(18px) saturate(1.45);
    }}
    .hf-glass .shine {{
      position: absolute;
      inset: 8px 16px auto 18px;
      height: 38%;
      border-radius: 999px;
      background: linear-gradient(180deg, rgba(255,255,255,0.64), rgba(255,255,255,0.04));
      opacity: 0.86;
    }}
    .hf-glass .edge {{
      position: absolute;
      inset: 7px;
      border-radius: 28px;
      border: 1px solid rgba(255,255,255,0.48);
      filter: blur(0.2px);
    }}
    .hf-glass .edge-a {{
      clip-path: polygon(0 0, 100% 0, 100% 44%, 0 26%);
    }}
    .hf-glass .edge-b {{
      border-color: color-mix(in srgb, var(--accent) 38%, white);
      opacity: 0.42;
      clip-path: polygon(0 64%, 100% 78%, 100% 100%, 0 100%);
    }}
    .corner-glow {{
      position: absolute;
      width: 18%;
      height: 26%;
      border-radius: 999px;
      pointer-events: none;
      filter: blur(8px);
      opacity: 0.58;
    }}
    .corner-a {{
      left: -2%;
      bottom: 4%;
      background: rgba(255, 255, 255, 0.68);
    }}
    .corner-b {{
      right: -2%;
      top: 6%;
      background: color-mix(in srgb, var(--accent) 26%, white);
    }}
    .motion-text {{
      position: relative;
      z-index: 2;
      max-width: 100%;
      color: #101827;
      font-family: "Segoe UI", Arial, "Helvetica Neue", sans-serif;
      font-size: clamp(23px, calc(var(--h) * 0.34), 42px);
      line-height: 0.96;
      letter-spacing: 0;
      font-weight: 850;
      text-wrap: balance;
      text-shadow: 0 1px 0 rgba(255,255,255,0.5);
    }}
    .hf-bold .hf-card {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 16px 26px 18px;
      border-radius: 24px;
      background: linear-gradient(135deg, rgba(8,13,26,0.90), rgba(18,28,47,0.78));
      border: 2px solid color-mix(in srgb, var(--accent) 72%, white);
      box-shadow: 0 14px 34px var(--deep-shadow), inset 0 1px 0 rgba(255,255,255,0.18);
      overflow: hidden;
    }}
    .hf-bold .motion-label {{
      margin-bottom: 5px;
      color: color-mix(in srgb, var(--accent) 54%, white);
      font-size: 12px;
      line-height: 1;
      font-weight: 900;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-family: "Segoe UI", Arial, "Helvetica Neue", sans-serif;
    }}
    .hf-bold .motion-text {{
      color: #fff;
      font-family: "Segoe UI", Arial, "Helvetica Neue", sans-serif;
      font-size: clamp(20px, calc(var(--h) * 0.30), 36px);
      letter-spacing: 0;
      text-shadow: 0 2px 16px rgba(0,0,0,0.35);
    }}
    .caption-stripe {{
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 9px;
      background: linear-gradient(90deg, var(--accent), #fef3c7);
    }}
    .hf-data .hf-card {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      padding: 22px 24px;
      border-radius: 30px;
      color: #eaf6ff;
      background: linear-gradient(145deg, rgba(11, 18, 31, 0.82), rgba(21, 41, 64, 0.58));
      border: 1px solid rgba(183, 220, 255, 0.38);
      box-shadow: 0 24px 52px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.18);
      backdrop-filter: blur(16px) saturate(1.35);
    }}
    .panel-head {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 13px;
      font-weight: 900;
      letter-spacing: 0.18em;
      color: #b7ddff;
    }}
    .dot {{
      width: 9px;
      height: 9px;
      border-radius: 99px;
      background: var(--accent);
      box-shadow: 0 0 22px var(--accent);
    }}
    .bars {{
      display: flex;
      align-items: end;
      gap: 12px;
      height: 42%;
      padding-top: 8px;
    }}
    .bars i {{
      flex: 1;
      height: var(--b);
      min-height: 18px;
      border-radius: 12px 12px 5px 5px;
      background: linear-gradient(180deg, #bfe5ff, var(--accent));
      box-shadow: 0 0 22px color-mix(in srgb, var(--accent) 40%, transparent);
    }}
    .hf-data .motion-text {{
      color: #fff;
      font-size: clamp(24px, calc(var(--h) * 0.18), 40px);
      line-height: 1.02;
      text-shadow: none;
    }}
    .hf-editorial-grid .shine,
    .hf-editorial-grid .edge,
    .hf-editorial-grid .corner-glow,
    .hf-editorial-grid .caption-stripe {{
      display: none;
    }}
    .hf-editorial-grid .hf-card {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 8px;
      padding: clamp(16px, calc(var(--h) * 0.14), 30px) clamp(22px, calc(var(--w) * 0.07), 42px);
      border-radius: 4px;
      overflow: hidden;
      color: var(--style-ink);
      background:
        linear-gradient(90deg, var(--accent) 0 12px, transparent 12px),
        linear-gradient(135deg, rgba(255,255,255,0.07), rgba(255,255,255,0) 42%),
        var(--panel-bg);
      border: 1px dotted var(--style-guide);
      box-shadow: 0 18px 36px rgba(0,0,0,0.28), inset 0 0 0 1px rgba(255,255,255,0.08);
      backdrop-filter: none;
    }}
    .hf-editorial-grid .hf-card::before {{
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(to right, rgba(255,255,255,0.12) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(255,255,255,0.12) 1px, transparent 1px);
      background-size: 64px 64px;
      opacity: 0.36;
      mask-image: linear-gradient(90deg, transparent, black 14%, black 86%, transparent);
    }}
    .hf-editorial-grid .hf-card::after {{
      content: "";
      position: absolute;
      inset: 10px;
      pointer-events: none;
      border: 1px dashed rgba(255,255,255,0.34);
      box-shadow:
        -8px -8px 0 -6px #fff,
        8px -8px 0 -6px #fff,
        -8px 8px 0 -6px #fff,
        8px 8px 0 -6px #fff;
      opacity: 0.7;
    }}
    .hf-editorial-grid .panel-head,
    .hf-editorial-grid .motion-label {{
      position: relative;
      z-index: 2;
      margin: 0 0 2px;
      display: flex;
      align-items: center;
      gap: 10px;
      color: color-mix(in srgb, var(--accent) 68%, white);
      font-family: "Segoe UI", Arial, "Helvetica Neue", sans-serif;
      font-size: clamp(10px, calc(var(--h) * 0.09), 16px);
      line-height: 1;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .hf-editorial-grid .dot {{
      width: 10px;
      height: 10px;
      border-radius: 0;
      background: #fff;
      box-shadow: none;
    }}
    .hf-editorial-grid .motion-text {{
      position: relative;
      z-index: 2;
      max-width: 100%;
      color: var(--style-ink);
      font-family: "Arial Narrow", Impact, "Segoe UI", Arial, sans-serif;
      font-size: clamp(25px, calc(var(--h) * 0.34), 72px);
      line-height: 0.88;
      letter-spacing: 0;
      font-weight: 900;
      text-transform: uppercase;
      text-shadow: none;
      overflow-wrap: anywhere;
    }}
    .hf-data.hf-editorial-grid .motion-text {{
      max-width: 92%;
      font-size: clamp(22px, min(calc(var(--h) * 0.16), calc(var(--w) * 0.066)), 34px);
    }}
    .hf-data.hf-editorial-grid .bars {{
      position: absolute;
      z-index: 1;
      left: 24px;
      right: 24px;
      bottom: 18px;
      width: auto;
      height: 42%;
      display: flex;
      align-items: end;
      gap: 10px;
      padding: 0;
      opacity: 0.18;
    }}
    .hf-data.hf-editorial-grid .bars i {{
      flex: 1;
      min-height: 28px;
      border-radius: 0;
      background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 72%, white), var(--accent));
      box-shadow: none;
    }}
    .hf-editorial-grid[data-variant="1"] .hf-card {{
      background:
        linear-gradient(90deg, rgba(255,255,255,0.08), rgba(255,255,255,0)),
        var(--panel-bg);
    }}
    .hf-editorial-grid[data-variant="2"] .hf-card {{
      color: #0a0b0c;
      background:
        linear-gradient(90deg, var(--accent) 0 16px, transparent 16px),
        rgba(246, 246, 241, 0.92);
      border-color: rgba(255,255,255,0.84);
      box-shadow: 0 16px 32px rgba(0,0,0,0.22), inset 0 0 0 2px rgba(10,10,10,0.12);
    }}
    .hf-editorial-grid[data-variant="2"] .motion-text,
    .hf-editorial-grid[data-variant="2"] .panel-head,
    .hf-editorial-grid[data-variant="2"] .motion-label {{
      color: #111418;
    }}
    .hf-editorial-grid[data-variant="3"] .hf-card {{
      color: #050608;
      background:
        linear-gradient(90deg, rgba(0,0,0,0.88) 0 18px, transparent 18px),
        linear-gradient(135deg, color-mix(in srgb, var(--accent) 72%, white), var(--accent));
      border-color: rgba(255,255,255,0.72);
      box-shadow: 0 16px 34px rgba(0,0,0,0.24);
    }}
    .hf-editorial-grid[data-variant="3"] .motion-text,
    .hf-editorial-grid[data-variant="3"] .panel-head,
    .hf-editorial-grid[data-variant="3"] .motion-label {{
      color: #050608;
    }}
  </style>
</head>
<body>
  <div data-composition-id="vibemotion-preview" data-start="0" data-duration="{duration:.3f}" data-width="{width}" data-height="{height}">
    <video id="base-video" class="clip" data-start="0" data-duration="{duration:.3f}" data-track-index="0" src="media/video.mp4" muted playsinline crossorigin="anonymous"></video>
{audio_node}
{motion_nodes}
  </div>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
{''.join(animation_rows)}
    window.__timelines["vibemotion-preview"] = tl;
  </script>
</body>
</html>
"""


def _prepare_media(base_video: Path, media_dir: Path) -> bool:
    video_output = media_dir / "video.mp4"
    audio_output = media_dir / "audio.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(base_video),
            "-an",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30,setpts=PTS-STARTPTS",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            str(video_output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    audio_result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(base_video),
            "-vn",
            "-c:a",
            "aac",
            "-af",
            "aresample=async=1:first_pts=0",
            "-b:a",
            "192k",
            str(audio_output),
        ],
        capture_output=True,
        text=True,
    )
    return audio_result.returncode == 0 and audio_output.exists() and audio_output.stat().st_size > 0


def _run_hyperframes(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stdout, stderr = process.communicate()
        raise HyperframesRenderError(f"Hyperframes render timed out after {timeout}s") from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def render_hyperframes_preview(base_video: Path, motions: list[MotionSpec], output: Path, work_dir: Path, width: int, height: int, duration: float) -> Path:
    if not motions:
        raise HyperframesRenderError("No motion layers for Hyperframes render")

    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        raise HyperframesRenderError("npx is not available")

    _clean_dir(work_dir)
    media_dir = work_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    has_audio = _prepare_media(base_video, media_dir)
    (work_dir / "index.html").write_text(_composition_html(width, height, duration, motions, has_audio), encoding="utf-8")
    (work_dir / "package.json").write_text(
        json.dumps({"name": "vibemotion-hyperframes-preview", "private": True}, indent=2),
        encoding="utf-8",
    )

    log_path = work_dir / "hyperframes-render.log"
    command = [
        npx,
        "hyperframes",
        "render",
        str(work_dir),
        "--output",
        str(output),
        "--quality",
        "standard",
        "--fps",
        "30",
    ]
    result = _run_hyperframes(command, timeout=60)
    log_path.write_text((result.stdout or "") + "\n\n" + (result.stderr or ""), encoding="utf-8")
    if result.returncode != 0 or not output.exists():
        raise HyperframesRenderError(f"Hyperframes render failed. See {log_path}")
    return output
