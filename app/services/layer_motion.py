from __future__ import annotations

import json
import math
import re
import time
import uuid
from typing import Any

from app.services.motion_effects import EFFECT_PROMPT_REFERENCE, primary_supported_effect
from app.services.ollama import OllamaError, chat_json


RECIPE_LABELS = {
    "fade-in": "Fade in",
    "soft-slide": "Soft slide",
    "pop-in": "Pop",
    "drop-bounce": "Drop bounce",
    "wipe-reveal": "Wipe",
    "premium-float": "Float",
    "pulse-glow": "Pulse",
    "blur-fade": "Blur fade",
    "custom-dsl": "Custom motion",
}

FRAME_CHOREO_PRESETS = {
    "white-bg-fade": "White background fade",
    "glitch-bg-fade": "Glitch background fade",
    "signal-scan-reveal": "Signal scan reveal",
    "glass-light-sweep": "Glass light sweep",
    "soft-pixel-snap": "Soft pixel snap",
    "venetian-blinds-bg": "Venetian blinds background reveal",
    "random-fly-in-stagger": "Random fly-in stagger",
    "advanced-composition-build": "Semantic composition build",
    "parallax-photo": "Photo parallax reveal",
    "fade-up-lines": "Text fade-up lines",
    "text-slide-up-lines": "Text slide-up lines",
    "button-y-rise": "Button Y rise",
    "tetris-build": "Tetris build",
    "gravity-drop-fade": "Gravity drop fade",
    "full-frame-drop": "Full-frame drop",
    "layer-scatter-fall": "Layer scatter fall",
    "full-frame-shatter": "Full-frame shatter",
    "full-frame-fade-out": "Full-frame fade out",
    "scene-camera": "Scene camera",
    "static-reveal": "Static reveal",
}

ADVANCED_ROLE_PHASES = {
    "photo": (0.0, 0.34, 0.26),
    "text": (0.34, 0.42, 0.28),
    "button": (0.76, 0.24, 0.18),
    "element": (0.14, 0.62, 0.26),
}

SYSTEM_PROMPT = """You are a professional motion designer and animation planner.
Return JSON only. Do not return code.
Convert the user's prompt into a deterministic layer motion recipe for a Figma layer.

Allowed preset values:
- fade-in: layer is absent/invisible first, then appears only by opacity.
- soft-slide: layer slides in gently.
- pop-in: layer scales/pops in.
- drop-bounce: layer drops from above with gravity-like acceleration and a small bounce.
- wipe-reveal: layer is revealed by a mask/wipe.
- premium-float: layer appears cleanly and gently floats.
- pulse-glow: subtle emphasis/pulse.
- blur-fade: opacity plus blur.

Important:
- If the user says the layer is initially absent/not visible/not there, that only means opacity is 0 before intro.delay. It does not force fade-in.
- If the prompt asks for falling/dropping/physics/gravity/bounce, use preset=drop-bounce and intro.type=drop even if the prompt also says initially absent.
- If falling/dropping is described as disappearance, exit, outro, or leaving, keep the requested intro separately and set outro.type=drop with direction=bottom. Do not turn that exit into fade.
- If the user asks fade in, do not slide. Set intro.type=fade and intro.distance=0.
- Parse delays and durations exactly from the prompt when present.
- "через 3 секунды" means intro.delay=3.
- "в течение 3 секунд" means intro.duration=3.
- Do not invent motion that contradicts the prompt.

Required JSON shape:
{
  "preset": "fade-in",
  "label": "Fade in",
  "tags": ["image", "fade-in", "clean"],
  "intro": {"type": "fade", "delay": 3, "duration": 3, "distance": 0, "direction": "center", "ease": "smooth"},
  "hold": {"type": "none", "amount": 0, "speed": 1},
  "outro": null
}
"""

SYSTEM_PROMPT += """

Universal motion DSL requirements:
- Always fill motion_dsl. It is the source of truth for complex prompts.
- Use preset=custom-dsl for complex, multi-step, or unusual prompts.
- motion_dsl.keyframes times are seconds relative to the motion start.
- Keyframe properties may include x, y, scale, scaleX, scaleY, rotate, skewX, skewY, opacity, blur, brightness.
- Units: x/y are percent of layer size. rotate/skew are degrees. opacity is 0..1. blur is pixels.
- Effects may be shake, pulse, float, wiggle, glow, wipe-reveal, venetian-blinds,
  iris-reveal, luma-wipe, liquid-wipe, typewriter, line-reveal, particle-dissolve,
  smoke-dissolve, paper-tear, pixelate, glitch, film-burn, shimmer.
- If the prompt says the layer is initially absent, opacity must be 0 before the first visible keyframe.
- If the prompt asks fade in, use opacity only unless another transform is explicitly requested.
- If the prompt asks for falling/dropping/physics/gravity/bounce, create y keyframes with acceleration and overshoot, not fade.
- If falling/dropping is the exit/outro/disappearance action, keep it in outro and do not replace it with fade-out.

motion_dsl example:
{
  "version": 1,
  "keyframes": [
    {"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
    {"time": 3, "opacity": 0, "y": -180},
    {"time": 3.68, "opacity": 1, "y": 8, "ease": "gravity"},
    {"time": 3.82, "y": -3, "ease": "sine"},
    {"time": 3.95, "y": 0, "ease": "smooth"}
  ],
  "effects": []
}
"""

SYSTEM_PROMPT += f"""

Broad motion vocabulary:
The user may name many professional effects. Map those names to the safest allowed preset and still fill motion_dsl.
Never output a preset outside the allowed preset list. If an exact effect is not natively supported, decompose it into the closest safe primitive.

Known effect names and safe presets:
{EFFECT_PROMPT_REFERENCE}
"""

NUMBER_WORDS = {
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
    "\u043e\u0434\u043d\u0443": 1.0,
    "\u043e\u0434\u0438\u043d": 1.0,
    "\u043e\u0434\u043d\u0430": 1.0,
    "\u0434\u0432\u0435": 2.0,
    "\u0434\u0432\u0430": 2.0,
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

INTRO_STAGE_MARKERS = [
    "first",
    "begin",
    "start",
    "\u043f\u0435\u0440\u0432",
    "\u0432\u043d\u0430\u0447\u0430\u043b",
    "\u0441\u043d\u0430\u0447\u0430\u043b",
]
BACKGROUND_ONLY_MARKERS = [
    "white screen",
    "white background",
    "background only",
    "clean background",
    "only background",
    "only the background",
    "only the clean background",
    "show only the background",
    "show only the clean background",
    "\u0431\u0435\u043b\u044b\u0439 \u044d\u043a\u0440\u0430\u043d",
    "\u0431\u0435\u043b\u043e\u0433\u043e \u044d\u043a\u0440\u0430\u043d\u0430",
    "\u0431\u0435\u043b\u043e\u043c\u0443 \u044d\u043a\u0440\u0430\u043d\u0443",
    "\u0431\u0435\u043b\u044b\u043c \u044d\u043a\u0440\u0430\u043d\u043e\u043c",
    "\u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d",
    "\u0431\u0435\u043b\u043e\u0433\u043e \u0444\u043e\u043d\u0430",
    "\u0431\u0435\u043b\u044b\u043c \u0444\u043e\u043d\u043e\u043c",
    "\u0444\u043e\u043d\u0430 \u0431\u0435\u0437 \u0434\u0440\u0443\u0433\u0438\u0445 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u043e\u0432",
    "\u0444\u043e\u043d \u0431\u0435\u0437",
]
LAYER_ENTITY_MARKERS = [
    "layer",
    "layers",
    "element",
    "elements",
    "\u0441\u043b\u043e\u0439",
    "\u0441\u043b\u043e\u0438",
    "\u0441\u043b\u043e\u0435\u0432",
    "\u044d\u043b\u0435\u043c\u0435\u043d\u0442",
    "\u044d\u043b\u0435\u043c\u0435\u043d\u0442\u044b",
    "\u043a\u0443\u0441\u043a",
]
STAGGER_ENTRY_MARKERS = [
    "random",
    "stagger",
    "cascade",
    "waterfall",
    "fly into",
    "fly in",
    "\u0441\u043b\u0443\u0447\u0430\u0439\u043d",
    "\u043a\u0430\u0441\u043a\u0430\u0434",
    "\u0432\u043b\u0435\u0442",
    "\u0432\u043b\u0435\u0442\u0430\u0442",
    "\u0437\u0430\u043b\u0435\u0442",
    "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437",
    "\u0433\u0440\u0430\u0434\u0438\u0435\u043d\u0442",
    "\u0433\u0440\u0430\u0444\u0434\u0438\u0435\u043d\u0442",
]
FLY_ENTRY_MARKERS = [
    "fly into",
    "fly in",
    "\u0432\u043b\u0435\u0442",
    "\u0432\u043b\u0435\u0442\u0430\u0442",
    "\u0437\u0430\u043b\u0435\u0442",
]
DESTRUCTIVE_OUTRO_MARKERS = [
    "shatter",
    "shards",
    "pieces",
    "broken glass",
    "\u0440\u0430\u0437\u0431\u0438\u0442",
    "\u0440\u0430\u0437\u0431\u0438\u0432",
    "\u043e\u0441\u043a\u043e\u043b",
    "\u043a\u0443\u0441\u043a",
    "\u0441\u0442\u0435\u043a\u043b",
]
EXPLICIT_SHATTER_MARKERS = [
    "shatter",
    "shards",
    "broken glass",
    "glass",
    "\u0440\u0430\u0437\u0431\u0438\u0442",
    "\u0440\u0430\u0437\u0431\u0438\u0432",
    "\u043e\u0441\u043a\u043e\u043b",
    "\u0441\u0442\u0435\u043a\u043b",
]
GRAVITY_OUTRO_MARKERS = [
    "drop",
    "fall",
    "stone",
    "gravity",
    "accelerat",
    "\u043f\u0430\u0434",
    "\u043a\u0430\u043c\u043d",
    "\u0433\u0440\u0430\u0432\u0438\u0442",
    "\u0443\u0441\u043a\u043e\u0440",
]
OUTRO_STAGE_MARKERS = [
    "at the end",
    "in the end",
    "end",
    "last",
    "outro",
    "exit",
    "fade",
    "\u0432\u043a\u043e\u043d\u0446\u0435",
    "\u0432 \u043a\u043e\u043d\u0446\u0435",
    "\u043f\u043e\u0441\u043b\u0435\u0434\u043d",
    "\u0444\u0435\u0439\u0434",
    "\u0443\u0445\u043e\u0434",
    "\u0438\u0441\u0447\u0435\u0437",
]

WHOLE_FRAME_SCOPE_MARKERS = [
    "whole frame",
    "entire frame",
    "full frame",
    "all elements",
    "all layers",
    "every element",
    "everything",
    "frame",
    "\u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c",
    "\u0432\u0441\u044f \u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\u0438\u044f",
    "\u0432\u0441\u044e \u0440\u0430\u043c\u043a\u0443",
    "\u0432\u0441\u044f \u043a\u0430\u0440\u0442\u0438\u043d\u043a",
    "\u0432\u0435\u0441\u044c \u044d\u043a\u0440\u0430\u043d",
    "\u0432\u0441\u0435 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u044b",
    "\u0432\u0441\u0435 \u0441\u043b\u043e\u0438",
    "\u0432\u0435\u0441\u044c \u043a\u0430\u0434\u0440",
    "\u043a\u0430\u0434\u0440",
    "\u0444\u0440\u0435\u0439\u043c",
]
FADE_OUT_MARKERS = [
    "fade out",
    "fades out",
    "fade-out",
    "fadeout",
    "\u0444\u0435\u0439\u0434 \u0430\u0443\u0442",
    "\u0444\u044d\u0439\u0434 \u0430\u0443\u0442",
    "\u0444\u0435\u0439\u0434\u0430\u0443\u0442",
    "\u0444\u044d\u0439\u0434\u0430\u0443\u0442",
    "\u0443\u0445\u043e\u0434\u0438\u0442 \u0432 \u0444\u0435\u0439\u0434",
    "\u0443\u0445\u043e\u0434\u0438\u0442 \u0432 \u0444\u044d\u0439\u0434",
    "\u0438\u0441\u0447\u0435\u0437",
]

ADVANCED_CHOREO_MARKERS = [
    "venetian",
    "blinds",
    "parallax",
    "fade up",
    "fade-up",
    "position y",
    "tetris",
    "glitch",
    "composition",
    "photos",
    "photographs",
    "pictures",
    "buttons",
    "cta",
    "scatter",
    "\u0436\u0430\u043b\u044e\u0437",
    "\u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441",
    "\u0444\u043e\u0442\u043e",
    "\u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444",
    "\u043a\u0430\u0440\u0442\u0438\u043d\u043a",
    "\u0442\u0435\u043a\u0441\u0442",
    "\u0437\u0430\u0433\u043e\u043b\u043e\u0432",
    "\u043a\u043d\u043e\u043f\u043a",
    "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437",
    "\u0441\u043d\u0438\u0437\u0443 \u0432\u0432\u0435\u0440\u0445",
    "\u0442\u0435\u0442\u0440\u0438\u0441",
    "\u0433\u043b\u0438\u0442\u0447",
    "\u0440\u0430\u0441\u0441\u044b\u043f",
    "\u043e\u043f\u0430\u0434",
    "\u0444\u0438\u0437\u0438\u043a",
]

VENETIAN_MARKERS = ["venetian", "blinds", "\u0436\u0430\u043b\u044e\u0437"]
GLITCH_MARKERS = ["glitch", "\u0433\u043b\u0438\u0442\u0447"]
SIGNAL_SCAN_MARKERS = ["signal scan", "digital scan", "scan reveal", "modern glitch", "clean glitch", "\u0441\u043a\u0430\u043d", "\u0446\u0438\u0444\u0440\u043e\u0432", "\u0441\u043e\u0432\u0440\u0435\u043c\u0435\u043d\u043d"]
GLASS_SWEEP_MARKERS = ["glass sweep", "light sweep", "premium shine", "shimmer reveal", "\u0441\u0442\u0435\u043a\u043b", "\u0431\u043b\u0438\u043a", "\u0448\u0438\u043c\u043c\u0435\u0440"]
PIXEL_SNAP_MARKERS = ["pixel snap", "pixel reveal", "pixelated", "\u043f\u0438\u043a\u0441\u0435\u043b"]
HARSH_GLITCH_MARKERS = ["rgb split", "datamosh", "hard glitch", "harsh glitch", "\u0436\u0435\u0441\u0442\u043a\u0438\u0439 \u0433\u043b\u0438\u0442\u0447", "\u0440\u0435\u0437\u043a\u0438\u0439 \u0433\u043b\u0438\u0442\u0447"]
TETRIS_MARKERS = ["tetris", "\u0442\u0435\u0442\u0440\u0438\u0441"]
PARALLAX_PHOTO_MARKERS = ["parallax", "\u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441"]
PHOTO_MARKERS = ["photo", "photos", "photograph", "picture", "image", "\u0444\u043e\u0442\u043e", "\u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444", "\u043a\u0430\u0440\u0442\u0438\u043d\u043a"]
TEXT_MARKERS = ["text", "headline", "title", "lines", "\u0442\u0435\u043a\u0441\u0442", "\u0437\u0430\u0433\u043e\u043b\u043e\u0432", "\u0441\u0442\u0440\u043e\u043a"]
BUTTON_MARKERS = ["button", "buttons", "cta", "\u043a\u043d\u043e\u043f\u043a"]
SCATTER_MARKERS = ["scatter", "scattering", "falling pieces", "\u0440\u0430\u0441\u0441\u044b\u043f", "\u043e\u043f\u0430\u0434", "\u0447\u0430\u0441\u0442\u0438\u0446"]
TOP_DOWN_MARKERS = ["top down", "top-to-bottom", "top to bottom", "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437", "\u0441\u0432\u0435\u0440\u0445\u0443-\u0432\u043d\u0438\u0437"]
TEXT_FROM_BOTTOM_MARKERS = ["from below", "bottom to top", "slide up", "rise up", "\u0441\u043d\u0438\u0437\u0443", "\u0432\u044b\u043f\u043b\u044b\u0432", "\u043f\u043e\u0434\u043d\u0438\u043c"]
FULL_FRAME_UNIT_MARKERS = ["as one", "whole object", "entire object", "whole picture", "entire picture", "\u0446\u0435\u043b\u0438\u043a\u043e\u043c", "\u0446\u0435\u043b\u044c\u043d", "\u0432\u0441\u044f \u043a\u0430\u0440\u0442\u0438\u043d\u043a", "\u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c", "\u0432\u0441\u044f \u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446"]
CAMERA_MARKERS = [
    "camera",
    "handheld",
    "hand held",
    "camera shake",
    "push in",
    "pull back",
    "zoom in",
    "zoom out",
    "camera zoom",
    "pan",
    "\u043a\u0430\u043c\u0435\u0440",
    "\u0440\u0443\u0447\u043d\u0430\u044f \u043a\u0430\u043c\u0435\u0440\u0430",
    "\u043d\u0430\u0435\u0437\u0434",
    "\u043e\u0442\u044a\u0435\u0437\u0434",
    "\u043e\u0442\u044c\u0435\u0437\u0434",
    "\u043f\u0430\u043d\u043e\u0440\u0430\u043c",
    "\u0437\u0443\u043c",
]

TIME_NUMBER_PATTERN = r"\d+(?:[\.,]\d+)?|" + "|".join(re.escape(item) for item in NUMBER_WORDS)
TIME_NUMBER_CAPTURE_PATTERN = f"({TIME_NUMBER_PATTERN})" + r"\s*(?:[x\u0445]\s*)?"
TIME_UNITS_PATTERN = r"(?:seconds?|secs?|sec|s|\u0441\u0435\u043a(?:\u0443\u043d\u0434(?:\u0430|\u0443|\u044b|\u0435|\u043e\u0439)?|\.)?)"

INTRO_DURATION_PATTERNS = [
    r"(?:white\s+(?:screen|background)|background\s+only|\u0431\u0435\u043b\w*\s+(?:\u044d\u043a\u0440\u0430\u043d|\u0444\u043e\u043d)|\u0444\u043e\u043d\s+\u0431\u0435\u0437)[^,.;\n]{0,90}(?:for|over|in|during|\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438]|\u0434\u043b\u0438\u0442\w*)\s+<num>\s*<units>",
    r"(?:first|initial|opening|beginning|at\s+the\s+start|start(?:ing)?)[\s:,-]*(?:for\s+)?<num>\s*<units>",
    r"(?:\u043f\u0435\u0440\u0432\w*|\u0441\u043d\u0430\u0447\u0430\u043b\w*|\u0432\s+\u043d\u0430\u0447\u0430\u043b\w*)[\s:,-]*(?:\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438\u0435\s+)?<num>\s*<units>",
    r"<num>\s*<units>[\s\S]{0,90}(?:white\s+(?:screen|background)|background\s+only|\u0431\u0435\u043b\w*\s+(?:\u044d\u043a\u0440\u0430\u043d|\u0444\u043e\u043d)|\u0444\u043e\u043d\s+\u0431\u0435\u0437)",
]

BUILD_DURATION_PATTERNS = [
    r"(?:all\s+(?:elements|layers)|elements?|layers?|fly(?:\s+into|\s+in)?|stagger)[\s\S]{0,130}?(?:over(?:\s+the\s+course\s+of)?|within|during|for|in)\s+<num>\s*<units>",
    r"(?:then[\s,]*)?(?:over(?:\s+the\s+course\s+of)?|within|during|for|in)\s+<num>\s*<units>[\s\S]{0,130}?(?:all\s+(?:elements|layers)|elements?|layers?|fly|stagger|\u0432\u043b\u0435\u0442|\u0437\u0430\u043b\u0435\u0442|\u044d\u043b\u0435\u043c\u0435\u043d\u0442|\u0441\u043b\u043e\u0439)",
    r"(?:\u0432\u0441\w*\s+(?:\u044d\u043b\u0435\u043c\u0435\u043d\u0442\w*|\u0441\u043b\u043e\w*)|(?:\u044d\u043b\u0435\u043c\u0435\u043d\u0442\w*|\u0441\u043b\u043e\w*)[\s\S]{0,60}(?:\u0432\u043b\u0435\u0442|\u0437\u0430\u043b\u0435\u0442|\u0441\u043b\u0443\u0447\u0430\u0439\u043d))[\s\S]{0,170}(?:\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438])\s+<num>\s*<units>",
    r"(?:\u043f\u043e\u0442\u043e\u043c|then|after\s+that)[\s,]*(?:\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[еи]|\u0437\u0430|over|within|during|for|in)\s+<num>\s*<units>",
    r"(?:\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[еи]|\u0437\u0430)\s+<num>\s*<units>[\s\S]{0,130}(?:\u0432\u043b\u0435\u0442|\u0437\u0430\u043b\u0435\u0442|\u044d\u043b\u0435\u043c\u0435\u043d\u0442|\u0441\u043b\u043e\u0439)",
]

OUTRO_DURATION_PATTERNS = [
    r"(?:over|during|for|in)\s+(?:the\s+)?(?:last|final)\s+<num>\s*<units>",
    r"(?:last|final)\s+<num>\s*<units>",
    r"(?:at\s+the\s+end|in\s+the\s+end)[\s\S]{0,90}<num>\s*<units>",
    r"(?:fade\s*out|fadeout|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u0435\u0439\u0434\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\u0430\u0443\u0442|outro|exit|\u0432\u044b\u0445\u043e\u0434)[\s\S]{0,90}<num>\s*<units>",
    r"(?:\u043f\u043e\u0441\u043b\u0435\u0434\u043d\w*)\s+<num>\s*<units>",
    r"(?:\u0432\s+\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435)[\s\S]{0,90}<num>\s*<units>",
    r"<num>\s*<units>[\s\S]{0,90}(?:at\s+the\s+end|in\s+the\s+end|fade\s*out|fadeout|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u0435\u0439\u0434\u0430\u0443\u0442|\u0444\u044d\u0439\u0434\u0430\u0443\u0442|\u0432\s+\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|\u0432\u044b\u0445\u043e\u0434)",
]

ADVANCED_APPEARANCE_DURATION_PATTERNS = [
    r"(?:whole|entire|all)\s+(?:composition|scene|animation|animations)[\s\S]{0,180}(?:within|in|over|for|by)\s+<num>\s*<units>",
    r"(?:composition|scene|animation|animations)[\s\S]{0,180}(?:within|in|over|for|by)\s+<num>\s*<units>",
    r"(?:whole|entire|all|everything|composition|scene|animation|animations)[\s\S]{0,180}(?:done|finish(?:ed)?|complete(?:d)?|visible|appear(?:s|ed)?)[\s\S]{0,80}(?:by|within|in|over|for)\s+<num>\s*<units>",
    r"(?:\u0432\u0441\w*|\u0432\u0435\u0441\w*)[\s\S]{0,50}(?:\u043f\u043e\u044f\u0432\u043b\w*|\u0438\u043d\u0442\u0440\u043e|\u0430\u043d\u0438\u043c\u0430\u0446\w*)[\s\S]{0,120}(?:\u0443\u043a\u043b\u0430\u0434\u044b\u0432\w*|\u0437\u0430\u043a\u0430\u043d\u0447\u0438\u0432\w*|\u0437\u0430\u0432\u0435\u0440\u0448\w*|\u0434\u043e\u043b\u0436\w*)[\s\S]{0,80}(?:\u0437\u0430|\u0432|\u043a)\s+<num>\s*<units>",
    r"(?:\u0432\u0441\u044f|\u0432\u0435\u0441\u044c|\u0432\u0441\u0435)\s+(?:\u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\w*|\u0430\u043d\u0438\u043c\u0430\u0446\w*|\u0441\u0446\u0435\u043d\w*)[\s\S]{0,220}(?:\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[е\u0435\u0438])\s+<num>\s*<units>",
    r"(?:\u0437\u0430|within|in|over)\s+<num>\s*<units>[\s\S]{0,180}(?:\u0432\u0441\w*\s+\u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446|whole\s+composition|all\s+animations)",
]

VENETIAN_DURATION_PATTERNS = [
    r"(?:venetian|blinds|\u0436\u0430\u043b\u044e\u0437)[\s\S]{0,50}?<num>\s*<units>",
    r"<num>\s*<units>[\s\S]{0,96}(?:venetian|blinds|\u0436\u0430\u043b\u044e\u0437)",
    r"(?:venetian|blinds|\u0436\u0430\u043b\u044e\u0437)[\s\S]{0,180}?(?:\bduration\b|\blasts?\b|\bfor\b|\bover\b|\bwithin\b|\bin\b|\u0437\u0430\b|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[å\u0435\u0438]|\u0434\u043b\u0438\u0442\u0435\u043b\w*)[\s\S]{0,60}?<num>\s*<units>",
    r"(?:\bduration\b|\u0434\u043b\u0438\u0442\u0435\u043b\w*)[\s\S]{0,60}?<num>\s*<units>[\s\S]{0,140}(?:venetian|blinds|\u0436\u0430\u043b\u044e\u0437)",
]
CAMERA_DURATION_PATTERNS = [
    r"(?:camera|zoom|push\s*in|pull\s*back|pan|\u043a\u0430\u043c\u0435\u0440|\u0437\u0443\u043c|\u043d\u0430\u0435\u0437\u0434|\u043e\u0442[\u044a\u044c]\u0435\u0437\u0434|\u043f\u0430\u043d\u043e\u0440\u0430\u043c)[\s\S]{0,100}(?:for|over|during|in|\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438])\s+<num>\s*<units>",
    r"(?:for|over|during|in|\u0437\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438])\s+<num>\s*<units>[\s\S]{0,100}(?:camera|zoom|push\s*in|pull\s*back|pan|\u043a\u0430\u043c\u0435\u0440|\u0437\u0443\u043c|\u043d\u0430\u0435\u0437\u0434|\u043e\u0442[\u044a\u044c]\u0435\u0437\u0434|\u043f\u0430\u043d\u043e\u0440\u0430\u043c)",
]


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def _has_negated_motion_marker(text: str, markers: list[str]) -> bool:
    for marker in markers:
        escaped = re.escape(marker)
        if re.search(rf"\b(?:no|without)\s+(?:\w+\s+){{0,3}}{escaped}\b", text, flags=re.IGNORECASE):
            return True
        if re.search(rf"(?:\u0431\u0435\u0437|\u043d\u0435)\s+(?:\w+\s+){{0,3}}{escaped}", text, flags=re.IGNORECASE):
            return True
    return False


def _wants_fade_out_only(text: str) -> bool:
    return bool(
        re.search(r"fade\s*out\s+only|only[\s\S]{0,32}fade\s*out|clean\s+full-frame\s+fade\s*out", text, flags=re.IGNORECASE)
        or re.search(r"\u0442\u043e\u043b\u044c\u043a\u043e[\s\S]{0,32}\u0444[е\u0435]\u0439\u0434|\u043f\u0440\u043e\u0441\u0442\w*[\s\S]{0,32}\u0444[е\u0435]\u0439\u0434", text, flags=re.IGNORECASE)
    )


def _wants_gradient_fade_entry(text: str) -> bool:
    return bool(
        re.search(r"gradient|\u0433\u0440\u0430\u0434\u0438\u0435\u043d\u0442|\u0433\u0440\u0430\u0444\u0434\u0438\u0435\u043d\u0442", text)
    )


def _parse_numeric_token(raw: str) -> float | None:
    token = str(raw or "").strip().replace(",", ".").casefold()
    if not token:
        return None
    if token in NUMBER_WORDS:
        return NUMBER_WORDS[token]
    try:
        return float(token)
    except ValueError:
        return None


def _duration_pattern(keywords: list[str]) -> str:
    keyword_pattern = "|".join(re.escape(item) for item in keywords)
    number_pattern = r"\d+(?:[\.,]\d+)?|" + "|".join(re.escape(item) for item in NUMBER_WORDS)
    units_pattern = r"(?:seconds?|secs?|sec|s|\u0441\u0435\u043a(?:\u0443\u043d\u0434(?:\u044b|\u0430)?)?)"
    return rf"(?:{keyword_pattern})[\s:,-]*(?:for\s+|over\s+|within\s+|in\s+|during\s+|(?:\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438\u0435|(?:\u043d\u0430\s+\u043f\u0440\u043e\u0442\u044f\u0436\u0435\u043d\u0438\u0438)|(?:\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0445\s+))?)?({number_pattern})\s*(?:[x\u0445]\s*)?{units_pattern}"


def _find_duration_for_keywords(text: str, keywords: list[str], fallback: float) -> float:
    match = re.search(_duration_pattern(keywords), text, flags=re.IGNORECASE)
    if not match:
        return fallback
    value = _parse_numeric_token(match.group(1))
    return float(value if value is not None else fallback)


def _duration_from_patterns(text: str, patterns: list[str], fallback: float) -> float:
    value = _duration_from_patterns_optional(text, patterns)
    return fallback if value is None else value


def _duration_from_patterns_optional(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        compiled = pattern.replace("<num>", TIME_NUMBER_CAPTURE_PATTERN).replace("<units>", TIME_UNITS_PATTERN)
        match = re.search(compiled, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _parse_numeric_token(match.group(1))
        if value is not None:
            return float(value)
    return None


def _basic_time_mentions(text: str) -> list[tuple[float, int, int]]:
    mentions: list[tuple[float, int, int]] = []
    pattern = rf"{TIME_NUMBER_CAPTURE_PATTERN}\s*{TIME_UNITS_PATTERN}"
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        value = _parse_numeric_token(match.group(1))
        if value is not None:
            mentions.append((float(value), match.start(), match.end()))
    return mentions


def _basic_prompt_clauses(text: str) -> list[tuple[str, int]]:
    clean = re.sub(r"[ \t\r\f\v]+", " ", str(text or "").casefold().replace(",", ".").replace("\u0451", "\u0435")).strip()
    if not clean:
        return []
    split_re = re.compile(
        r"\n+|(?<!\d)[.;!?]+(?!\d)|"
        r"\b(?:then\s+add\s+this\s+motion\s+instruction|and\s+then|then|afterwards|finally)\b|"
        r"\b(?:потом|затем|после\s+этого)\b",
        flags=re.IGNORECASE,
    )
    clauses: list[tuple[str, int]] = []
    start = 0
    for match in split_re.finditer(clean):
        piece = clean[start : match.start()].strip(" ,;:.")
        if piece:
            clauses.append((piece, start))
        start = match.end()
    tail = clean[start:].strip(" ,;:.")
    if tail:
        clauses.append((tail, start))
    return clauses or [(clean, 0)]


def _duration_near_basic_intent(text: str, marker_pattern: str, fallback: float) -> float:
    candidates: list[tuple[float, float]] = []
    clauses = _basic_prompt_clauses(text)
    for clause, _offset in clauses:
        markers = list(re.finditer(marker_pattern, clause, flags=re.IGNORECASE))
        if not markers:
            continue
        mentions = _basic_time_mentions(clause)
        if not mentions:
            continue
        for marker in markers:
            marker_mid = (marker.start() + marker.end()) / 2.0
            for value, start, end in mentions:
                mention_mid = (start + end) / 2.0
                before_penalty = 14.0 if end < marker.start() else 0.0
                candidates.append((abs(mention_mid - marker_mid) + before_penalty, value))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return max(0.05, float(candidates[0][1]))
    mentions = _basic_time_mentions(text)
    if len(mentions) == 1 and re.search(marker_pattern, text, flags=re.IGNORECASE):
        return max(0.05, float(mentions[0][0]))
    return max(0.05, float(fallback))


def _number_from_prompt(text: str, pattern: str, fallback: float = 0.0) -> float:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return fallback
    value = _parse_numeric_token(match.group(1) or "")
    return float(value if value is not None else fallback)


def _has_exit_drop_intent(text: str) -> bool:
    exit_words = r"(?:исчез|исчезан|выход|уход|уходит|улет|пропад|убер|exit|outro|disappear|leave|leaves)"
    drop_words = r"(?:упал|упасть|пад|рух|вниз|камн|гравитац|ускор|drop|fall|down|bottom|gravity|stone|accelerat)"
    return bool(
        re.search(exit_words + r"[\s\S]{0,140}" + drop_words, text)
        or re.search(drop_words + r"[\s\S]{0,140}" + exit_words, text)
        or re.search(r"(?:drop|fall)\s+(?:out|down)|(?:упал|падает|падение)\s+вниз", text)
    )


def _has_exit_fade_intent(text: str) -> bool:
    return bool(re.search(r"fade\s*out|фейд\s*аут|исчез[\w\s]{0,50}(?:фейд|fade|прозрач)|пропад[\w\s]{0,50}(?:фейд|fade)", text))


def _has_outro_intent(text: str) -> bool:
    return bool(re.search(r"уход|исчез|исчезан|пропад|выход|out|exit|disappear|leave|fade\s*out", text))


def _wants_full_span_motion(text: str) -> bool:
    return bool(re.search(r"full duration|entire duration|whole clip|throughout|all the time|for the whole|всю длитель|вся длитель|на протяжении|все время|постоянно|до конца", text))


def is_frame_choreography_prompt(prompt: str) -> bool:
    text = prompt.casefold()
    has_staged_intro = _contains_any(text, INTRO_STAGE_MARKERS)
    has_background = _contains_any(text, BACKGROUND_ONLY_MARKERS)
    has_layer_entities = _contains_any(text, LAYER_ENTITY_MARKERS)
    has_layer_stagger = has_layer_entities and _contains_any(text, STAGGER_ENTRY_MARKERS)
    has_destructive_outro = _contains_any(text, DESTRUCTIVE_OUTRO_MARKERS)
    has_gravity_outro = _contains_any(text, GRAVITY_OUTRO_MARKERS) and _contains_any(text, OUTRO_STAGE_MARKERS)
    return (
        (has_staged_intro and has_background and has_layer_stagger)
        or (has_layer_stagger and (has_destructive_outro or has_gravity_outro))
        or (has_background and (has_destructive_outro or has_gravity_outro))
    )


def _is_advanced_choreography_prompt(prompt: str) -> bool:
    text = str(prompt or "").casefold()
    if not text:
        return False
    has_scope = (
        _contains_any(text, WHOLE_FRAME_SCOPE_MARKERS)
        or _contains_any(text, FULL_FRAME_UNIT_MARKERS)
        or _contains_any(text, ["composition", "\u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446"])
    )
    has_phase_language = bool(
        _contains_any(text, INTRO_STAGE_MARKERS)
        or _contains_any(text, OUTRO_STAGE_MARKERS)
        or "then" in text
        or "after" in text
        or "\u0437\u0430\u0442\u0435\u043c" in text
        or "\u043f\u043e\u0442\u043e\u043c" in text
        or "\u043f\u043e\u0441\u043b\u0435" in text
    )
    buckets = [
        _contains_any(text, VENETIAN_MARKERS),
        _contains_any(text, GLITCH_MARKERS),
        _contains_any(text, SIGNAL_SCAN_MARKERS),
        _contains_any(text, GLASS_SWEEP_MARKERS),
        _contains_any(text, PIXEL_SNAP_MARKERS),
        _contains_any(text, TETRIS_MARKERS),
        _contains_any(text, PARALLAX_PHOTO_MARKERS),
        _contains_any(text, PHOTO_MARKERS),
        _contains_any(text, TEXT_MARKERS)
        and (
            "fade up" in text
            or "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437" in text
            or "\u0441\u0442\u0440\u043e\u043a" in text
            or _contains_any(text, TEXT_FROM_BOTTOM_MARKERS)
        ),
        _contains_any(text, BUTTON_MARKERS),
        _contains_any(text, SCATTER_MARKERS) or (_contains_any(text, GRAVITY_OUTRO_MARKERS) and _contains_any(text, OUTRO_STAGE_MARKERS)),
    ]
    return has_scope and has_phase_language and sum(1 for item in buckets if item) >= 2


def should_use_frame_choreography_prompt(prompt: str) -> bool:
    text = str(prompt or "").casefold()
    if _contains_any(text, CAMERA_MARKERS):
        return True
    if _contains_any(text, [*VENETIAN_MARKERS, *SIGNAL_SCAN_MARKERS, *GLASS_SWEEP_MARKERS, *PIXEL_SNAP_MARKERS, *TETRIS_MARKERS]):
        return True
    if (
        (_contains_any(text, PARALLAX_PHOTO_MARKERS) and _contains_any(text, PHOTO_MARKERS))
        or (_contains_any(text, TEXT_MARKERS) and ("fade up" in text or "line" in text or _contains_any(text, TOP_DOWN_MARKERS)))
        or _contains_any(text, BUTTON_MARKERS)
    ):
        return True
    if is_frame_choreography_prompt(text):
        return True
    intro_duration = _duration_from_patterns_optional(text, INTRO_DURATION_PATTERNS)
    build_duration = _duration_from_patterns_optional(text, BUILD_DURATION_PATTERNS)
    outro_duration = _duration_from_patterns_optional(text, OUTRO_DURATION_PATTERNS)
    explicit_duration_count = sum(value is not None for value in [intro_duration, build_duration, outro_duration])
    has_background = _contains_any(text, BACKGROUND_ONLY_MARKERS)
    has_whole_scope = _contains_any(text, WHOLE_FRAME_SCOPE_MARKERS)
    has_layer_scope = _contains_any(text, LAYER_ENTITY_MARKERS)
    has_entry = _contains_any(text, STAGGER_ENTRY_MARKERS)
    has_destructive_outro = _contains_any(text, DESTRUCTIVE_OUTRO_MARKERS) or _contains_any(text, GRAVITY_OUTRO_MARKERS)
    has_outro_stage = _contains_any(text, OUTRO_STAGE_MARKERS)
    has_phase_language = _contains_any(text, INTRO_STAGE_MARKERS) or has_outro_stage or "then" in text or "\u043f\u043e\u0442\u043e\u043c" in text
    if has_whole_scope and explicit_duration_count >= 2:
        return True
    if has_background and has_layer_scope and (has_entry or has_outro_stage):
        return True
    if has_whole_scope and has_destructive_outro and (has_phase_language or explicit_duration_count >= 1):
        return True
    return False


def _stable_fraction(value: str, salt: int = 0) -> float:
    total = sum((index + 1 + salt) * ord(char) for index, char in enumerate(str(value or "")))
    return (total % 997) / 997.0


def _entry_range(frame_size: float, layer_start: float, layer_size: float, padding: float) -> tuple[float, float]:
    low = -layer_start + padding
    high = frame_size - (layer_start + layer_size) - padding
    if low > high and padding:
        low = -layer_start
        high = frame_size - (layer_start + layer_size)
    return low, high


def _entry_offset_inside_frame(
    *,
    horizontal: str,
    vertical: str,
    frame_width: float,
    frame_height: float,
    layer_x: float,
    layer_y: float,
    layer_w: float,
    layer_h: float,
    padding: float,
) -> tuple[float, float]:
    x_low, x_high = _entry_range(frame_width, layer_x, layer_w, padding)
    y_low, y_high = _entry_range(frame_height, layer_y, layer_h, padding)

    def choose(low: float, high: float, side: str) -> float:
        if low > high:
            return (low + high) / 2.0
        if side in {"left", "top"}:
            return low
        if side in {"right", "bottom"}:
            return high
        return max(low, min(high, 0.0))

    return choose(x_low, x_high, horizontal), choose(y_low, y_high, vertical)


def _limit_entry_offset(offset_x: float, offset_y: float, max_distance: float) -> tuple[float, float]:
    distance = math.hypot(offset_x, offset_y)
    if distance <= max_distance or distance <= 0.001:
        return offset_x, offset_y
    scale = max_distance / distance
    return offset_x * scale, offset_y * scale


def _frame_layer_area(layer: dict[str, Any]) -> float:
    try:
        return max(0.0, float(layer.get("width") or 0) * float(layer.get("height") or 0))
    except (TypeError, ValueError):
        return 0.0


def _is_background_layer(layer: dict[str, Any], frame_area: float) -> bool:
    name = str(layer.get("name") or "").casefold()
    fill = str(layer.get("fill") or "").casefold()
    area = _frame_layer_area(layer)
    return (
        layer.get("kind") == "shape"
        and area >= frame_area * 0.55
        and ("background" in name or "фон" in name or "255, 255, 255" in fill or "#fff" in fill or "white" in fill)
    )


def _overlap_area(a: dict[str, Any], b: dict[str, Any]) -> float:
    left = max(float(a.get("x", 0) or 0), float(b.get("x", 0) or 0))
    top = max(float(a.get("y", 0) or 0), float(b.get("y", 0) or 0))
    right = min(float(a.get("x", 0) or 0) + float(a.get("width", 0) or 0), float(b.get("x", 0) or 0) + float(b.get("width", 0) or 0))
    bottom = min(float(a.get("y", 0) or 0) + float(a.get("height", 0) or 0), float(b.get("y", 0) or 0) + float(b.get("height", 0) or 0))
    return max(0.0, right - left) * max(0.0, bottom - top)


def _visual_mask_ids(layers: list[dict[str, Any]]) -> set[str]:
    mask_ids: set[str] = set()
    for layer_index, layer in enumerate(layers):
        if layer.get("kind") != "image" or layer.get("visible") is False:
            continue
        explicit_mask_id = str(layer.get("visual_mask_id") or "").strip()
        if explicit_mask_id.casefold() in {"none", "null", "undefined"}:
            explicit_mask_id = ""
        if explicit_mask_id:
            mask_ids.add(explicit_mask_id)
            continue
        layer_area = max(1.0, _frame_layer_area(layer))
        candidates = []
        for index, item in enumerate(layers[:layer_index]):
            if item.get("kind") != "shape" or item.get("visible") is False:
                continue
            area = max(1.0, _frame_layer_area(item))
            overlap = _overlap_area(layer, item)
            coverage = overlap / min(layer_area, area)
            layer_coverage = overlap / layer_area
            valid_mask = coverage > 0.62 and layer_coverage > 0.12 and area <= layer_area * 1.18
            if valid_mask:
                candidates.append((layer_index - index, -coverage, area, str(item.get("id") or "")))
        if candidates:
            mask_id = sorted(candidates)[0][3]
            if mask_id:
                mask_ids.add(mask_id)
    return mask_ids


def _figma_frame_size(motion: Any, layers: list[dict[str, Any]], fallback_width: float, fallback_height: float) -> tuple[float, float]:
    node_id = str(getattr(motion, "figma_node_id", "") or "")
    for layer in layers:
        is_root = str(layer.get("id") or "") == node_id or (
            str(layer.get("node_type") or "").upper() == "FRAME"
            and abs(float(layer.get("x", 0) or 0)) < 0.001
            and abs(float(layer.get("y", 0) or 0)) < 0.001
        )
        if is_root:
            width = float(layer.get("width") or 0)
            height = float(layer.get("height") or 0)
            if width > 0 and height > 0:
                return width, height
    max_x = max((float(layer.get("x", 0) or 0) + float(layer.get("width", 0) or 0) for layer in layers), default=fallback_width)
    max_y = max((float(layer.get("y", 0) or 0) + float(layer.get("height", 0) or 0) for layer in layers), default=fallback_height)
    return max(1.0, max_x), max(1.0, max_y)


def _is_root_frame_layer(motion: Any, layer: dict[str, Any]) -> bool:
    node_id = str(getattr(motion, "figma_node_id", "") or "")
    return str(layer.get("id") or "") == node_id or (
        str(layer.get("node_type") or "").upper() == "FRAME"
        and abs(float(layer.get("x", 0) or 0)) < 0.001
        and abs(float(layer.get("y", 0) or 0)) < 0.001
    )


def _figma_frame_background_fill(motion: Any, layers: list[dict[str, Any]]) -> str:
    for layer in layers:
        if _is_root_frame_layer(motion, layer):
            fill = str(layer.get("fill") or "").strip()
            if fill:
                return fill
    for layer in layers:
        fill = str(layer.get("fill") or "").strip()
        if fill and layer.get("kind") == "shape":
            return fill
    return "rgba(255,255,255,1)"


def _layer_center_inside(layer: dict[str, Any], container: dict[str, Any], padding: float) -> bool:
    cx = float(layer.get("x", 0) or 0) + float(layer.get("width", 0) or 0) / 2
    cy = float(layer.get("y", 0) or 0) + float(layer.get("height", 0) or 0) / 2
    left = float(container.get("x", 0) or 0) - padding
    top = float(container.get("y", 0) or 0) - padding
    right = float(container.get("x", 0) or 0) + float(container.get("width", 0) or 0) + padding
    bottom = float(container.get("y", 0) or 0) + float(container.get("height", 0) or 0) + padding
    return left <= cx <= right and top <= cy <= bottom


def _ui_cluster_keys(layers: list[dict[str, Any]], excluded_ids: set[str]) -> dict[str, str]:
    candidates = []
    for layer in layers:
        layer_id = str(layer.get("id") or "")
        if not layer_id or layer_id in excluded_ids or layer.get("visible") is False:
            continue
        width = float(layer.get("width", 0) or 0)
        height = float(layer.get("height", 0) or 0)
        if layer.get("kind") in {"image", "shape"} and 42 <= width <= 360 and 12 <= height <= 90 and width >= height * 2.0:
            candidates.append(layer)
    keys = {str(layer.get("id") or ""): str(layer.get("id") or "") for layer in layers if str(layer.get("id") or "")}
    for container in candidates:
        container_id = str(container.get("id") or "")
        padding = max(12.0, float(container.get("height", 0) or 0) * 0.9)
        for layer in layers:
            layer_id = str(layer.get("id") or "")
            if not layer_id or layer_id in excluded_ids or layer.get("visible") is False or layer_id == container_id:
                continue
            if _layer_center_inside(layer, container, padding):
                keys[layer_id] = container_id
        keys[container_id] = container_id
    return keys


def _sanitize_choreo_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return safe or "cluster"


def _visual_clip_clusters(layers: list[dict[str, Any]], mask_ids: set[str]) -> dict[str, dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    mask_indexes = [
        index
        for index, layer in enumerate(layers)
        if str(layer.get("id") or "") in mask_ids or layer.get("mask_role") == "visual-mask"
    ]
    for order, mask_index in enumerate(mask_indexes):
        mask = layers[mask_index]
        mask_id = str(mask.get("id") or "")
        if not mask_id:
            continue
        next_mask_index = next((index for index in mask_indexes if index > mask_index), len(layers))
        children: list[str] = []
        for item in layers[mask_index + 1 : next_mask_index]:
            item_id = str(item.get("id") or "")
            if not item_id or item_id == mask_id or item.get("visible") is False:
                continue
            if item.get("kind") != "image":
                continue
            overlap = _overlap_area(item, mask)
            if overlap <= 0:
                continue
            item_center_x = float(item.get("x", 0) or 0) + float(item.get("width", 0) or 0) / 2
            item_center_y = float(item.get("y", 0) or 0) + float(item.get("height", 0) or 0) / 2
            mask_left = float(mask.get("x", 0) or 0)
            mask_top = float(mask.get("y", 0) or 0)
            mask_right = mask_left + float(mask.get("width", 0) or 0)
            mask_bottom = mask_top + float(mask.get("height", 0) or 0)
            if not (mask_left <= item_center_x <= mask_right and mask_top <= item_center_y <= mask_bottom):
                continue
            item_area = max(1.0, _frame_layer_area(item))
            mask_area = max(1.0, _frame_layer_area(mask))
            if overlap / min(item_area, mask_area) < 0.08:
                continue
            children.append(item_id)
        if len(children) < 2:
            continue
        clusters[mask_id] = {
            "id": f"__frame_choreo_cluster_{_sanitize_choreo_id(mask_id)}",
            "source_mask_id": mask_id,
            "name": str(mask.get("name") or f"Visual cluster {order + 1}"),
            "x": float(mask.get("x", 0) or 0),
            "y": float(mask.get("y", 0) or 0),
            "width": max(1.0, float(mask.get("width", 1) or 1)),
            "height": max(1.0, float(mask.get("height", 1) or 1)),
            "child_ids": children,
            "order": order,
        }
    return clusters


def _layer_union_rect_for_ids(layers: list[dict[str, Any]], layer_ids: list[str], padding: float = 0.0) -> dict[str, float]:
    lookup = {str(layer.get("id") or ""): layer for layer in layers}
    rect_layers = [
        lookup[layer_id]
        for layer_id in layer_ids
        if layer_id in lookup and lookup[layer_id].get("visible") is not False
    ]
    if not rect_layers:
        return {}
    left = min(float(layer.get("x", 0) or 0) for layer in rect_layers) - padding
    top = min(float(layer.get("y", 0) or 0) for layer in rect_layers) - padding
    right = max(
        float(layer.get("x", 0) or 0) + max(1.0, float(layer.get("width", 1) or 1))
        for layer in rect_layers
    ) + padding
    bottom = max(
        float(layer.get("y", 0) or 0) + max(1.0, float(layer.get("height", 1) or 1))
        for layer in rect_layers
    ) + padding
    return {
        "x": left,
        "y": top,
        "width": max(1.0, right - left),
        "height": max(1.0, bottom - top),
    }


def _cluster_reference_rects(layers: list[dict[str, Any]], cluster_keys: dict[str, str]) -> dict[str, dict[str, float]]:
    by_id = {str(layer.get("id") or ""): layer for layer in layers if str(layer.get("id") or "")}
    result: dict[str, dict[str, float]] = {}
    for layer_id, key in cluster_keys.items():
        layer = by_id.get(key)
        if not layer:
            continue
        result[layer_id] = {
            "width": max(1.0, float(layer.get("width", 1) or 1)),
            "height": max(1.0, float(layer.get("height", 1) or 1)),
        }
    return result


def _choreo_recipe(
    prompt: str,
    preset: str,
    keyframes: list[dict[str, Any]],
    tags: list[str],
    phase_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recipe = {
        "id": f"recipe-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}",
        "prompt": prompt,
        "preset": preset,
        "dsl_type": "custom-dsl",
        "time_mode": "absolute",
        "label": FRAME_CHOREO_PRESETS.get(preset, preset),
        "tags": tags,
        "intro": {"type": "custom", "direction": "center", "delay": 0, "duration": 0.1, "distance": 0, "ease": "smooth"},
        "hold": {"type": "none", "amount": 0, "speed": 1},
        "outro": None,
        "motion_dsl": {"version": 1, "keyframes": keyframes, "effects": []},
    }
    if phase_plan is not None:
        recipe["phase_plan"] = phase_plan
    return recipe


def _modern_intro_preset(text: str, advanced: bool, wants_white_intro: bool) -> str:
    if not advanced and not wants_white_intro:
        return "static-reveal"
    intro_text = re.split(
        r"\bthen\b|\bafter\b|\bat\s+the\s+end\b|\bin\s+the\s+end\b|\boutro\b|\bexit\b|"
        r"\u043f\u043e\u0442\u043e\u043c|\u0437\u0430\u0442\u0435\u043c|\u0432\s+\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|\u0432\u044b\u0445\u043e\u0434|\u043f\u043e\u0441\u043b\u0435\u0434\u043d",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    preset_text = intro_text if wants_white_intro else text
    if _contains_any(preset_text, VENETIAN_MARKERS):
        return "venetian-blinds-bg"
    if _contains_any(preset_text, GLASS_SWEEP_MARKERS):
        return "glass-light-sweep"
    if _contains_any(preset_text, PIXEL_SNAP_MARKERS):
        return "soft-pixel-snap"
    if _contains_any(preset_text, HARSH_GLITCH_MARKERS):
        return "glitch-bg-fade"
    if _contains_any(preset_text, GLITCH_MARKERS) or _contains_any(preset_text, SIGNAL_SCAN_MARKERS):
        return "signal-scan-reveal"
    return "white-bg-fade" if wants_white_intro else "static-reveal"


def _append_intro_template_effects(recipe: dict[str, Any], spec: dict[str, Any], intro_duration: float) -> None:
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else None
    if not isinstance(dsl, dict):
        return
    effects = dsl.setdefault("effects", [])
    preset = str(spec.get("intro_preset") or "")
    duration = round(min(max(0.2, float(intro_duration or 0.2)), 1.6), 3)
    if preset == "venetian-blinds-bg":
        effects.append(
            {
                "type": "venetian-blinds",
                "start": 0,
                "duration": float(intro_duration or duration),
                "blades": 12,
                "orientation": str(spec.get("venetian_orientation") or "vertical"),
            }
        )
    elif preset == "signal-scan-reveal":
        effects.append({"type": "signal-scan", "start": 0, "duration": duration, "strength": 0.34, "seed": 719})
    elif preset == "glass-light-sweep":
        effects.append({"type": "shimmer", "start": 0, "duration": duration, "strength": 0.22, "band": 0.26})
    elif preset == "soft-pixel-snap":
        effects.append({"type": "pixelate", "start": 0, "duration": duration, "max_pixel": 18})
    elif preset == "glitch-bg-fade":
        effects.append({"type": "glitch", "start": 0, "duration": duration, "amplitude": 0.018, "seed": 719})


def _parse_frame_choreography_spec(prompt: str, motion_duration: float) -> dict[str, float | bool | str]:
    text = prompt.casefold()
    intro_duration = _find_duration_for_keywords(
        text,
        ["first", "первые", "вначале", "сначала"],
        0.0,
    ) or _find_duration_for_keywords(text, BACKGROUND_ONLY_MARKERS, 2.0)
    build_duration = _find_duration_for_keywords(
        text,
        ["over the course of", "within", "during", "в течение", "на протяжении"],
        3.0,
    )
    outro_duration = _find_duration_for_keywords(
        text,
        ["last", "at the end", "in the end", "последних", "вконце", "в конце"],
        3.0,
    )
    intro_duration, build_duration, outro_duration = _fit_phase_durations(motion_duration, intro_duration, build_duration, outro_duration)
    wants_white_intro = _contains_any(text, BACKGROUND_ONLY_MARKERS)
    wants_random_stagger = _contains_any(text, LAYER_ENTITY_MARKERS) and _contains_any(text, STAGGER_ENTRY_MARKERS)
    wants_fade_out = _contains_any(text, FADE_OUT_MARKERS)
    wants_gravity_drop = _contains_any(text, GRAVITY_OUTRO_MARKERS)
    wants_shatter = _contains_any(text, DESTRUCTIVE_OUTRO_MARKERS)
    wants_fade_only_outro = wants_fade_out and not wants_gravity_drop and not wants_shatter
    return {
        "intro_duration": intro_duration,
        "build_duration": build_duration,
        "outro_duration": outro_duration,
        "wants_white_intro": wants_white_intro,
        "wants_random_stagger": wants_random_stagger,
        "wants_fade_out": wants_fade_out,
        "wants_gravity_drop": wants_gravity_drop,
        "wants_shatter": wants_shatter,
        "wants_fade_only_outro": wants_fade_only_outro,
        "build_preset": "random-fly-in-stagger" if wants_random_stagger else "static-reveal",
        "outro_preset": "full-frame-shatter" if wants_shatter else "gravity-drop-fade" if wants_gravity_drop else "full-frame-fade-out" if wants_fade_only_outro else "static-reveal",
    }


def describe_motion_units(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def recipe_actions(recipe: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(recipe, dict):
            return []
        actions = recipe.get("motion_actions")
        if isinstance(actions, list):
            return [action for action in actions if isinstance(action, dict)]
        return [recipe] if recipe else []

    units: list[dict[str, Any]] = []
    grouped_child_ids: set[str] = set()
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("visible") is False:
            continue
        layer_id = str(layer.get("id") or "").strip()
        if not layer_id or layer_id.startswith("__frame_choreo_") or layer.get("choreo_static_skip") or layer.get("mask_role"):
            continue
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if layer.get("render_cluster_source"):
            child_ids = [str(child_id) for child_id in list(layer.get("cluster_child_ids") or []) if child_id]
            grouped_child_ids.update(child_ids)
            units.append(
                {
                    "id": f"unit:{layer_id}",
                    "type": "visual-cluster",
                    "source_layer_id": layer_id,
                    "layer_ids": [layer_id, *child_ids],
                    "label": str(layer.get("name") or layer_id),
                    "preset": recipe.get("preset") if recipe else None,
                    "phase_plan": recipe.get("phase_plan") if recipe else None,
                }
            )
            continue
        if layer_id in grouped_child_ids or layer.get("cluster_parent_id"):
            continue
        actions = recipe_actions(recipe)
        if actions:
            if len(actions) == 1:
                action = actions[0]
                units.append(
                    {
                        "id": f"layer:{layer_id}",
                        "type": "layer",
                        "source_layer_id": layer_id,
                        "layer_ids": [layer_id],
                        "label": str(layer.get("name") or layer_id),
                        "preset": action.get("preset"),
                        "phase_plan": action.get("phase_plan"),
                        "action_id": action.get("id"),
                    }
                )
                continue
            for index, action in enumerate(actions, start=1):
                units.append(
                    {
                        "id": f"action:{layer_id}:{action.get('id') or index}",
                        "type": "motion-action",
                        "source_layer_id": layer_id,
                        "layer_ids": [layer_id],
                        "label": str(action.get("label") or action.get("preset") or f"Action {index}"),
                        "preset": action.get("preset"),
                        "phase_plan": action.get("phase_plan"),
                        "action_id": action.get("id"),
                    }
                )
    return units


def describe_motion_plan(layers: list[dict[str, Any]]) -> dict[str, Any] | None:
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        if phase_plan:
            return phase_plan
    return None


def _parse_frame_choreography_spec(prompt: str, motion_duration: float) -> dict[str, float | bool | str]:
    text = prompt.casefold()
    advanced = _is_advanced_choreography_prompt(text)
    venetian_duration = _duration_from_patterns_optional(text, VENETIAN_DURATION_PATTERNS)
    appearance_duration = _duration_from_patterns_optional(text, ADVANCED_APPEARANCE_DURATION_PATTERNS)
    intro_duration = _duration_from_patterns(text, INTRO_DURATION_PATTERNS, 0.0) or _find_duration_for_keywords(text, BACKGROUND_ONLY_MARKERS, 2.0)
    if advanced and venetian_duration is not None:
        intro_duration = venetian_duration
    elif advanced and _contains_any(text, VENETIAN_MARKERS) and intro_duration <= 0.1:
        intro_duration = 0.5
    build_duration = _duration_from_patterns(text, BUILD_DURATION_PATTERNS, 3.0)
    if advanced and appearance_duration is not None:
        build_duration = max(0.15, float(appearance_duration) - float(intro_duration or 0))
    outro_duration = _duration_from_patterns(text, OUTRO_DURATION_PATTERNS, 3.0)
    intro_duration = max(0.1, float(intro_duration or 0.1))
    build_duration = max(0.15, float(build_duration or 0.15))
    outro_duration = max(0.15, float(outro_duration or 0.15))
    minimum_duration = intro_duration + build_duration + outro_duration
    duration = max(float(motion_duration or 0), minimum_duration)
    wants_white_intro = _contains_any(text, BACKGROUND_ONLY_MARKERS)
    wants_top_down = _contains_any(text, TOP_DOWN_MARKERS)
    negates_fly_entry = _has_negated_motion_marker(text, FLY_ENTRY_MARKERS)
    wants_fly_entry = _contains_any(text, FLY_ENTRY_MARKERS) and not negates_fly_entry
    wants_gradient_fade_entry = _wants_gradient_fade_entry(text) and not wants_fly_entry
    wants_random_stagger = _contains_any(text, LAYER_ENTITY_MARKERS) and (
        _contains_any(text, STAGGER_ENTRY_MARKERS)
        or wants_top_down
        or wants_gradient_fade_entry
    )
    wants_fade_out = _contains_any(text, FADE_OUT_MARKERS)
    fade_out_only = _wants_fade_out_only(text)
    negates_gravity = _has_negated_motion_marker(text, ["fall", "falling", "drop", "dropping", "gravity", "\u043f\u0430\u0434", "\u0433\u0440\u0430\u0432\u0438\u0442"])
    negates_scatter = _has_negated_motion_marker(text, ["scatter", "scattering", "shatter", "pieces", "\u0440\u0430\u0441\u0441\u044b\u043f", "\u043e\u0441\u043a\u043e\u043b", "\u043a\u0443\u0441\u043a"])
    wants_gravity_drop = _contains_any(text, GRAVITY_OUTRO_MARKERS) and not negates_gravity and not fade_out_only
    wants_shatter = _contains_any(text, DESTRUCTIVE_OUTRO_MARKERS) and not negates_scatter and not fade_out_only
    wants_venetian_blinds = advanced and _contains_any(text, VENETIAN_MARKERS)
    wants_intro_glitch = advanced and _contains_any(text, GLITCH_MARKERS)
    has_modern_intro_marker = _contains_any(text, [*GLITCH_MARKERS, *SIGNAL_SCAN_MARKERS, *GLASS_SWEEP_MARKERS, *PIXEL_SNAP_MARKERS])
    intro_preset = _modern_intro_preset(text, advanced or has_modern_intro_marker, wants_white_intro)
    wants_intro_template = intro_preset not in {"static-reveal", "white-bg-fade", "venetian-blinds-bg"}
    venetian_orientation = "horizontal" if re.search(r"horizontal|horiz|\u0433\u043e\u0440\u0438\u0437", text) else "vertical"
    role_motion_context = advanced or (
        (_contains_any(text, WHOLE_FRAME_SCOPE_MARKERS) or _contains_any(text, LAYER_ENTITY_MARKERS))
        and (
            "then" in text
            or "\u043f\u043e\u0442\u043e\u043c" in text
            or "\u0437\u0430\u0442\u0435\u043c" in text
            or _contains_any(text, INTRO_STAGE_MARKERS)
        )
    )
    wants_photo_parallax = role_motion_context and (_contains_any(text, PARALLAX_PHOTO_MARKERS) or (_contains_any(text, PHOTO_MARKERS) and "depth" in text))
    wants_text_fade_up_lines = role_motion_context and (_contains_any(text, TEXT_MARKERS) and ("fade up" in text or "\u0441\u0442\u0440\u043e\u043a" in text or _contains_any(text, TOP_DOWN_MARKERS) or "typewriter" in text or "word by word" in text or "letter by letter" in text or "kinetic" in text))
    wants_text_slide_up_lines = role_motion_context and _contains_any(text, TEXT_MARKERS) and _contains_any(text, TEXT_FROM_BOTTOM_MARKERS)
    wants_button_y_rise = role_motion_context and (_contains_any(text, BUTTON_MARKERS) or "position y" in text)
    wants_order_top_down = wants_top_down
    wants_layer_scatter = _contains_any(text, SCATTER_MARKERS) and not negates_scatter and not fade_out_only
    wants_tetris_build = _contains_any(text, TETRIS_MARKERS)
    wants_scene_camera = _contains_any(text, CAMERA_MARKERS)
    wants_advanced_role_build = bool(
        wants_photo_parallax
        or wants_text_fade_up_lines
        or wants_text_slide_up_lines
        or wants_button_y_rise
        or wants_tetris_build
    )
    wants_full_frame_drop = (
        _contains_any(text, GRAVITY_OUTRO_MARKERS)
        and _contains_any(text, FULL_FRAME_UNIT_MARKERS)
        and not wants_shatter
        and not wants_layer_scatter
        and not negates_gravity
        and not fade_out_only
    )
    if wants_layer_scatter and not _contains_any(text, EXPLICIT_SHATTER_MARKERS):
        wants_shatter = False
    if wants_full_frame_drop:
        wants_gravity_drop = True
        wants_fade_out = True
    if wants_layer_scatter:
        wants_gravity_drop = True
        wants_fade_out = True
    wants_fade_only_outro = wants_fade_out and not wants_gravity_drop and not wants_shatter
    camera_duration = _duration_from_patterns_optional(text, CAMERA_DURATION_PATTERNS)
    camera_only = bool(
        wants_scene_camera
        and not advanced
        and not wants_white_intro
        and not wants_random_stagger
        and not wants_gravity_drop
        and not wants_shatter
        and not wants_fade_only_outro
    )
    if camera_only:
        duration = max(float(motion_duration or 0), float(camera_duration or motion_duration or 1.0), 1.0)
        minimum_duration = max(0.15, float(camera_duration or duration))
        intro_duration = 0.0
        build_duration = duration
        outro_duration = 0.0
    return {
        "intro_duration": intro_duration,
        "build_duration": build_duration,
        "outro_duration": outro_duration,
        "minimum_duration": minimum_duration,
        "duration": duration,
        "prompt": prompt,
        "advanced_choreography": advanced,
        "appearance_duration": float(appearance_duration or intro_duration + build_duration),
        "wants_white_intro": wants_white_intro,
        "wants_random_stagger": wants_random_stagger,
        "wants_fade_out": wants_fade_out,
        "wants_gravity_drop": wants_gravity_drop,
        "wants_shatter": wants_shatter,
        "wants_venetian_blinds": wants_venetian_blinds,
        "wants_intro_glitch": wants_intro_glitch,
        "wants_intro_template": wants_intro_template,
        "intro_preset": intro_preset,
        "venetian_orientation": venetian_orientation,
        "wants_photo_parallax": wants_photo_parallax,
        "wants_text_fade_up_lines": wants_text_fade_up_lines,
        "wants_text_slide_up_lines": wants_text_slide_up_lines,
        "wants_button_y_rise": wants_button_y_rise,
        "wants_order_top_down": wants_order_top_down,
        "wants_fly_entry": wants_fly_entry,
        "wants_gradient_fade_entry": wants_gradient_fade_entry,
        "wants_layer_scatter": wants_layer_scatter,
        "wants_tetris_build": wants_tetris_build,
        "wants_full_frame_drop": wants_full_frame_drop,
        "wants_fade_only_outro": wants_fade_only_outro,
        "wants_scene_camera": wants_scene_camera,
        "camera_only": camera_only,
        "camera_duration": float(camera_duration or 0),
        "build_preset": "tetris-build" if wants_tetris_build else "advanced-composition-build" if wants_advanced_role_build else "gradient-fade-stagger" if wants_gradient_fade_entry else "random-fly-in-stagger" if wants_random_stagger else "static-reveal",
        "outro_preset": "full-frame-shatter" if wants_shatter else "layer-scatter-fall" if wants_layer_scatter else "full-frame-drop" if wants_full_frame_drop else "gravity-drop-fade" if wants_gravity_drop else "full-frame-fade-out" if wants_fade_only_outro else "static-reveal",
    }


def _basic_frame_choreography_spec(prompt: str, motion_duration: float) -> dict[str, Any]:
    text = str(prompt or "").casefold().replace(",", ".").replace("\u0451", "\u0435")
    fade_in_marker = r"(?:fades?\s*in|fading\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432\w*|\u043f\u0440\u043e\u044f\u0432\w*|opacity\s*0\s*(?:to|->)\s*1)"
    fade_out_marker = r"(?:fades?\s*out|fading\s*out|fadeout|disappear|vanish|hide|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u0435\u0439\u0434\u0430\u0443\u0442|\u0438\u0441\u0447\u0435\u0437\w*|\u043f\u0440\u043e\u043f\u0430\u0434\w*|\u0443\u0431\u0435\u0440\w*|opacity\s*1\s*(?:to|->)\s*0)"
    slide_marker = r"(?:\bslides?\b|\bsliding\b|from\s+(?:the\s+)?(?:left|right|top|bottom)|\u0441\u043b\u0430\u0439\u0434|\u0437\u0430\u0435\u0437\u0434|\u0432\u044b\u0435\u0437\u0434|\u0441\u043b\u0435\u0432\u0430|\u0441\u043f\u0440\u0430\u0432\u0430|\u0441\u0432\u0435\u0440\u0445\u0443|\u0441\u043d\u0438\u0437\u0443)"
    scale_marker = r"(?:\b(?:zooms?|zooming|scales?|scaling|pop)\b|\u0437\u0443\u043c|\u043c\u0430\u0441\u0448\u0442\u0430\u0431|\u0443\u0432\u0435\u043b\u0438\u0447|\u043f\u0440\u0438\u0431\u043b\u0438\u0437|\u043f\u043e\u043f)"
    fly_marker = r"(?:\bfly\b|\bflies\b|\bflying\b|fly\s+into|fly\s+in|\u0432\u043b\u0435\u0442|\u0437\u0430\u043b\u0435\u0442)"
    drop_marker = r"(?:\u043f\u0430\u0434\w*|\u0443\u043f\u0430\u0434\w*|\u0440\u0443\u0445\w*|drop|fall|gravity)"
    white_marker = r"(?:\u0431\u0435\u043b\w*\s+\u0444\u043e\u043d|\u0431\u0435\u043b\w*\s+\u044d\u043a\u0440\u0430\u043d|white\s+(?:background|screen))"
    end_marker = r"(?:\u0432\s+\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|at\s+the\s+end|in\s+the\s+end|final|last|outro|exit)"

    wants_fade_in = bool(re.search(fade_in_marker, text))
    wants_fade_out = bool(re.search(fade_out_marker, text) or re.search(end_marker, text) and _contains_any(text, FADE_OUT_MARKERS))
    negates_fly = bool(
        re.search(
            r"(?:no|without)\s+(?:fly|flying|movement|motion)|do\s+not\s+(?:fly|move)|"
            r"\u0431\u0435\u0437\s+(?:\u0437\u0430\u043b\u0435\u0442|\u0432\u043b\u0435\u0442|\u0434\u0432\u0438\u0436)|\u043d\u0435\s+(?:\u0437\u0430\u043b\u0435\u0442|\u0432\u043b\u0435\u0442|\u0434\u0432\u0438\u0433)",
            text,
        )
    )
    negates_drop = bool(
        re.search(
            r"(?:no|without)\s+(?:fall|falling|drop|dropping)|do\s+not\s+(?:fall|drop)|"
            r"\u0431\u0435\u0437\s+(?:\u043f\u0430\u0434|\u043e\u0441\u043a\u043e\u043b|\u0440\u0430\u0437\u0431\u0438\u0432)|\u043d\u0435\s+(?:\u043f\u0430\u0434|\u0440\u0443\u0445)",
            text,
        )
    )
    wants_layer_scatter = bool(_contains_any(text, SCATTER_MARKERS) and not negates_drop)
    wants_drop_out = bool(
        re.search(
            rf"(?:\u0432\u0435\u0441\u044c\s+\u0444\u0440\u0435\u0439\u043c|\u0432\u0435\u0441\u044c\s+\u043a\u0430\u0434\u0440|whole\s+frame|frame)?[\s\S]{{0,80}}{drop_marker}[\s\S]{{0,80}}(?:\u0432\u043d\u0438\u0437|down|bottom)|(?:drop|fall)[\s\S]{{0,80}}(?:whole\s+frame|frame)|{drop_marker}",
            text,
        )
        and not re.search(r"(?:drop|fall)\s+in|\u0437\u0430\u043f\u0430\u0434\w*\s+\u0432\s+\u043a\u0430\u0434\u0440", text)
        and not negates_drop
    )
    if wants_layer_scatter:
        wants_drop_out = True
    wants_slide = bool(re.search(slide_marker, text))
    wants_scale = bool(re.search(scale_marker, text))
    wants_fly = bool(re.search(fly_marker, text) and not negates_fly)
    wants_top_down = _contains_any(text, TOP_DOWN_MARKERS) or "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437" in text
    wants_white_intro = _contains_any(text, BACKGROUND_ONLY_MARKERS) or "\u0431\u0435\u043b" in text
    intro_preset = _modern_intro_preset(text, True, wants_white_intro)
    wants_intro_template = intro_preset not in {"static-reveal", "white-bg-fade"}
    wants_tetris_build = _contains_any(text, TETRIS_MARKERS)
    wants_shatter = bool(
        re.search(
            r"shatter|shards|broken\s+glass|\u0440\u0430\u0437\u0431\u0438\u0442|\u0440\u0430\u0437\u0431\u0438\u0432|\u043e\u0441\u043a\u043e\u043b|\u043a\u0443\u0441\u043a",
            text,
        )
        and not negates_drop
    )
    wants_photo_parallax = _contains_any(text, PARALLAX_PHOTO_MARKERS) and _contains_any(text, PHOTO_MARKERS)
    wants_text_fade_up_lines = _contains_any(text, TEXT_MARKERS) and ("fade up" in text or "\u0441\u0442\u0440\u043e\u043a" in text or wants_top_down)
    wants_button_y_rise = _contains_any(text, BUTTON_MARKERS) or "position y" in text
    wants_advanced_role_build = bool(wants_photo_parallax or wants_text_fade_up_lines or wants_button_y_rise)
    wants_gradient = bool(_wants_gradient_fade_entry(text) or (wants_top_down and wants_fade_in and not wants_fly))
    wants_exit_motion = wants_fade_out or wants_drop_out or wants_shatter
    wants_scene_camera = _contains_any(text, CAMERA_MARKERS)
    camera_duration = _duration_from_patterns_optional(text, CAMERA_DURATION_PATTERNS)
    wants_full_frame_drop = bool(
        wants_drop_out
        and not wants_layer_scatter
        and not re.search(r"(?:not\s+as\s+(?:one|a\s+whole)|not\s+(?:one|whole)\s+frame|\u043d\u0435\s+\u0446\u0435\u043b\u0438\u043a\u043e\u043c)", text)
        and re.search(r"(?:whole\s+frame|entire\s+frame|full\s+frame|\u0432\u0435\u0441\u044c\s+\u0444\u0440\u0435\u0439\u043c|\u0432\u0435\u0441\u044c\s+\u043a\u0430\u0434\u0440)", text)
    )
    stagger_marker = r"(?:stagger|cascade|sequential|sequence|one\s+by\s+one|\u043f\u043e\s*\u043e\u0447\u0435\u0440\u0435\u0434|\u043f\u043e\u043e\u0447\u0435\u0440\u0435\u0434|\u043f\u043e\u044d\u0442\u0430\u043f|\u043f\u043e\u0441\u0442\u0435\u043f\u0435\u043d|\u043e\u0434\u0438\u043d\s+\u0437\u0430\s+\u0434\u0440\u0443\u0433)"
    explicit_intro_duration = _duration_from_patterns_optional(text, INTRO_DURATION_PATTERNS)
    explicit_build_duration = _duration_from_patterns_optional(text, BUILD_DURATION_PATTERNS)
    explicit_outro_duration = _duration_from_patterns_optional(text, OUTRO_DURATION_PATTERNS) if wants_exit_motion else None
    intro_duration = (
        explicit_intro_duration
        if explicit_intro_duration is not None
        else _duration_near_basic_intent(text, rf"{fade_in_marker}|{slide_marker}|{scale_marker}|{white_marker}", 1.0)
    )
    build_duration = (
        explicit_build_duration
        if explicit_build_duration is not None
        else _duration_near_basic_intent(text, r"(?:layers?|elements?|\u0441\u043b\u043e\w*|\u044d\u043b\u0435\u043c\u0435\u043d\w*|\u043f\u043e\s+\u043e\u0447\u0435\u0440\u0435\u0434|\u043a\u0430\u0441\u043a\u0430\u0434)", 3.0)
    )
    appearance_duration = _duration_from_patterns_optional(text, ADVANCED_APPEARANCE_DURATION_PATTERNS)
    if wants_advanced_role_build and appearance_duration is not None:
        build_duration = max(0.15, float(appearance_duration) - float(intro_duration or 0.0))
    outro_duration = (
        explicit_outro_duration
        if explicit_outro_duration is not None
        else _duration_near_basic_intent(text, rf"{fade_out_marker}|{drop_marker}|{end_marker}", 1.0)
        if wants_exit_motion
        else 0.0
    )
    wants_layer_stagger = bool(
        wants_white_intro
        or wants_intro_template
        or wants_tetris_build
        or wants_advanced_role_build
        or wants_gradient
        or wants_top_down
        or (_contains_any(text, LAYER_ENTITY_MARKERS) and re.search(rf"then|\u043f\u043e\u0442\u043e\u043c|\u0437\u0430\u0442\u0435\u043c|{stagger_marker}", text))
    )
    if wants_layer_stagger:
        if not wants_white_intro and not wants_intro_template:
            build_duration = max(build_duration, intro_duration if wants_fade_in else 0.05)
            intro_duration = 0.0
        minimum_duration = intro_duration + build_duration + (outro_duration if wants_exit_motion else 0.0)
    else:
        wants_enter_motion = wants_fade_in or wants_slide or wants_scale
        minimum_duration = max(intro_duration if wants_enter_motion else 0.0, outro_duration if wants_exit_motion else 0.0, 0.05)
        if wants_enter_motion and wants_exit_motion:
            minimum_duration = intro_duration + outro_duration
    camera_only = bool(
        wants_scene_camera
        and not wants_layer_stagger
        and not wants_fade_in
        and not wants_fade_out
        and not wants_drop_out
        and not wants_slide
        and not wants_scale
        and not wants_fly
    )
    if camera_only:
        intro_duration = 0.0
        outro_duration = 0.0
        build_duration = max(0.15, float(camera_duration or motion_duration or 1.0))
        minimum_duration = build_duration
    total_duration = max(float(motion_duration or 0.0), minimum_duration)
    return {
        "prompt": prompt,
        "mode": "layer-stagger" if wants_layer_stagger else "whole-frame",
        "duration": total_duration,
        "minimum_duration": minimum_duration,
        "intro_duration": intro_duration,
        "build_duration": build_duration,
        "outro_duration": outro_duration if wants_exit_motion else 0.0,
        "build_start": intro_duration,
        "build_end": intro_duration + build_duration,
        "outro_start": total_duration - (outro_duration if wants_exit_motion else 0.0),
        "wants_white_intro": wants_white_intro,
        "wants_fade_in": bool(wants_fade_in),
        "wants_fade_out": bool(wants_fade_out),
        "wants_drop_out": wants_drop_out,
        "wants_slide": bool(wants_slide),
        "wants_scale": bool(wants_scale),
        "wants_fly": bool(wants_fly),
        "wants_full_frame_drop": bool(wants_full_frame_drop),
        "wants_layer_scatter": bool(wants_layer_scatter),
        "wants_shatter": bool(wants_shatter),
        "wants_tetris_build": bool(wants_tetris_build),
        "wants_advanced_role_build": bool(wants_advanced_role_build),
        "wants_photo_parallax": bool(wants_photo_parallax),
        "wants_text_fade_up_lines": bool(wants_text_fade_up_lines),
        "wants_button_y_rise": bool(wants_button_y_rise),
        "intro_preset": intro_preset,
        "direction": "left" if re.search(r"from\s+(?:the\s+)?left|\bleft\b|\u0441\u043b\u0435\u0432\u0430|\u0438\u0437\s+\u043b\u0435\u0432", text) else "right" if re.search(r"from\s+(?:the\s+)?right|\bright\b|\u0441\u043f\u0440\u0430\u0432\u0430|\u0438\u0437\s+\u043f\u0440\u0430\u0432", text) else "top" if re.search(r"from\s+(?:the\s+)?top|\btop\b|\u0441\u0432\u0435\u0440\u0445\u0443|\u0438\u0437\s+\u0432\u0435\u0440\u0445", text) else "bottom" if re.search(r"from\s+(?:the\s+)?bottom|\bbottom\b|\u0441\u043d\u0438\u0437\u0443|\u0438\u0437\s+\u043d\u0438\u0437", text) else "left",
        "wants_top_down": bool(wants_top_down),
        "wants_gradient": bool(wants_gradient),
        "wants_scene_camera": bool(wants_scene_camera),
        "camera_only": bool(camera_only),
        "camera_duration": float(camera_duration or 0.0),
    }


def _basic_choreo_phase_plan(spec: dict[str, Any]) -> dict[str, Any]:
    duration = float(spec["duration"])
    intro_duration = float(spec["intro_duration"])
    build_start = float(spec["build_start"])
    build_duration = float(spec["build_duration"])
    outro_start = float(spec["outro_start"])
    outro_duration = float(spec["outro_duration"])
    if spec.get("camera_only"):
        camera_preset = _scene_camera_kind(str(spec.get("prompt") or ""))
        return {
            "scope": "whole-frame",
            "mode": "basic-frame-choreography",
            "duration": round(duration, 3),
            "minimum_duration": round(float(spec["minimum_duration"]), 3),
            "phases": [
                {
                    "id": "camera",
                    "preset": camera_preset,
                    "start": 0.0,
                    "duration": round(duration, 3),
                    "end": round(duration, 3),
                }
            ],
            "camera": _scene_camera_plan(str(spec.get("prompt") or ""), duration, 0.0, False),
            "acceptance": {"sample_times": [0.0, round(duration * 0.5, 3), round(max(0.0, duration - 0.05), 3)]},
        }
    if spec.get("mode") == "whole-frame":
        phases: list[dict[str, Any]] = []
        if spec.get("wants_slide"):
            enter_preset = "basic-frame-slide"
        elif spec.get("wants_scale"):
            enter_preset = "basic-frame-scale"
        elif spec.get("wants_fade_in"):
            enter_preset = "basic-frame-fade-in"
        else:
            enter_preset = "static-reveal"
        if spec.get("wants_fade_in") or spec.get("wants_slide") or spec.get("wants_scale"):
            phases.append({"id": "intro", "preset": enter_preset, "start": 0.0, "duration": round(intro_duration, 3), "end": round(intro_duration, 3)})
        if spec.get("wants_fade_out") or spec.get("wants_drop_out"):
            if spec.get("wants_drop_out"):
                outro_preset = "basic-frame-drop-out" if spec.get("wants_fade_out") else "basic-frame-drop"
            else:
                outro_preset = "basic-frame-fade-out"
            phases.append({"id": "outro", "preset": outro_preset, "start": round(outro_start, 3), "duration": round(outro_duration, 3), "end": round(duration, 3), "anchor": "end"})
        if not phases:
            phases.append({"id": "hold", "preset": "static-reveal", "start": 0.0, "duration": round(duration, 3), "end": round(duration, 3)})
        return {
            "scope": "whole-frame",
            "mode": "basic-frame-choreography",
            "duration": round(duration, 3),
            "minimum_duration": round(float(spec["minimum_duration"]), 3),
            "phases": phases,
            "acceptance": {"sample_times": sorted({0.0, round(intro_duration, 3), round(outro_start, 3), round(max(0.0, duration - 0.05), 3)})},
        }
    build_preset = (
        "advanced-composition-build"
        if spec.get("wants_advanced_role_build")
        else "tetris-build"
        if spec.get("wants_tetris_build")
        else "gradient-fade-stagger"
        if spec.get("wants_gradient")
        else "random-fly-in-stagger"
        if spec.get("wants_fly")
        else "basic-layer-fade"
    )
    build_order = "top-down-by-role" if spec.get("wants_top_down") else "stable-random" if spec.get("wants_fly") else "ordered"
    phases = [
        {"id": "intro", "preset": str(spec.get("intro_preset") or "white-bg-fade"), "start": 0.0, "duration": round(intro_duration, 3), "end": round(intro_duration, 3)},
        {
            "id": "build",
            "preset": build_preset,
            "start": round(build_start, 3),
            "duration": round(build_duration, 3),
            "end": round(build_start + build_duration, 3),
            "order": build_order,
        },
    ]
    if spec.get("wants_advanced_role_build"):
        phases[1]["subphases"] = [
            {
                "id": "photos",
                "preset": "parallax-photo" if spec.get("wants_photo_parallax") else "static-reveal",
                "start": round(build_start, 3),
                "duration": round(build_duration * 0.55, 3),
                "target": "image-layers",
            },
            {
                "id": "text",
                "preset": "fade-up-lines" if spec.get("wants_text_fade_up_lines") else "static-reveal",
                "start": round(build_start + build_duration * 0.18, 3),
                "duration": round(build_duration * 0.62, 3),
                "target": "text-layers",
                "order": "top-down",
            },
            {
                "id": "buttons",
                "preset": "button-y-rise" if spec.get("wants_button_y_rise") else "static-reveal",
                "start": round(build_start + build_duration * 0.42, 3),
                "duration": round(build_duration * 0.45, 3),
                "target": "button-clusters",
            },
        ]
    if spec.get("wants_fade_out") or spec.get("wants_drop_out") or spec.get("wants_shatter"):
        if spec.get("wants_drop_out"):
            outro_preset = (
                "layer-scatter-fall"
                if spec.get("wants_layer_scatter")
                else "full-frame-drop"
                if spec.get("wants_full_frame_drop")
                else "basic-frame-drop-out"
                if spec.get("wants_fade_out")
                else "basic-frame-drop"
            )
        elif spec.get("wants_shatter"):
            outro_preset = "full-frame-shatter"
        else:
            outro_preset = "full-frame-fade-out"
        phases.append({"id": "outro", "preset": outro_preset, "start": round(outro_start, 3), "duration": round(outro_duration, 3), "end": round(duration, 3), "anchor": "end"})
    return {
        "scope": "whole-frame",
        "mode": "basic-frame-choreography",
        "duration": round(duration, 3),
        "minimum_duration": round(float(spec["minimum_duration"]), 3),
        "phases": phases,
        "acceptance": {"sample_times": sorted({0.0, round(intro_duration, 3), round(build_start + build_duration, 3), round(max(0.0, duration - 0.05), 3)})},
    }


def _basic_static_keyframes(
    start: float,
    end: float,
    total: float,
    fade_out: bool,
    outro_start: float | None = None,
    drop_out: bool = False,
    drop_y: float = 0.0,
) -> list[dict[str, Any]]:
    outro_start = max(end, float(outro_start if outro_start is not None else total))
    final_y = float(drop_y) if drop_out else 0
    return [
        {"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": round(start, 4), "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": round(end, 4), "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
        {"time": round(outro_start, 4), "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": round(total, 4), "opacity": 0 if fade_out else 1, "x": 0, "y": final_y, "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
    ]


def _basic_whole_frame_keyframes(spec: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    duration = float(spec["duration"])
    enter_duration = max(0.05, float(spec["intro_duration"]))
    outro_duration = max(0.0, float(spec["outro_duration"]))
    has_exit_motion = bool(spec.get("wants_fade_out") or spec.get("wants_drop_out"))
    outro_start = max(0.0, duration - outro_duration) if has_exit_motion else duration
    base = {"x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0}
    direction = str(spec.get("direction") or "left")
    offset = {
        "left": {"x": -110, "y": 0},
        "right": {"x": 110, "y": 0},
        "top": {"x": 0, "y": -110},
        "bottom": {"x": 0, "y": 110},
    }.get(direction, {"x": -110, "y": 0})
    if spec.get("wants_drop_out"):
        preset = "basic-frame-drop-out" if spec.get("wants_fade_out") else "basic-frame-drop"
        frames: list[dict[str, Any]] = []
        if spec.get("wants_fade_in"):
            frames.extend(
                [
                    {"time": 0, "opacity": 0, **base},
                    {"time": round(enter_duration, 4), "opacity": 1, **base, "ease": "sine"},
                ]
            )
        else:
            frames.append({"time": 0, "opacity": 1, **base})
        last_time = float(frames[-1].get("time") or 0)
        if outro_start > last_time + 0.001:
            frames.append({"time": round(outro_start, 4), "opacity": 1, **base})
        fall_mid = min(duration, outro_start + max(0.05, outro_duration) * 0.45)
        if fall_mid > outro_start + 0.001 and fall_mid < duration - 0.001:
            frames.append(
                {
                    "time": round(fall_mid, 4),
                    "opacity": 0.8 if spec.get("wants_fade_out") else 1,
                    **base,
                    "y": 32,
                    "rotate": 1.5,
                    "ease": "power",
                }
            )
        frames.append(
            {
                "time": round(duration, 4),
                "opacity": 0 if spec.get("wants_fade_out") else 1,
                **base,
                "y": 125,
                "rotate": 4,
                "blur": 1.2,
                "ease": "sine",
            }
        )
        return preset, frames
    if spec.get("wants_slide"):
        preset = "basic-frame-slide"
        frames = [
            {"time": 0, "opacity": 0, **base, **offset},
            {"time": round(enter_duration, 4), "opacity": 1, **base, "ease": "sine"},
        ]
    elif spec.get("wants_scale"):
        preset = "basic-frame-scale"
        frames = [
            {"time": 0, "opacity": 0, **base, "scale": 0.94},
            {"time": round(enter_duration, 4), "opacity": 1, **base, "scale": 1, "ease": "sine"},
        ]
    elif spec.get("wants_fade_out") and not spec.get("wants_fade_in"):
        preset = "basic-frame-fade-out"
        frames = [
            {"time": 0, "opacity": 1, **base},
            {"time": round(outro_start, 4), "opacity": 1, **base},
            {"time": round(duration, 4), "opacity": 0, **base, "ease": "sine"},
        ]
        return preset, frames
    else:
        preset = "basic-frame-fade-in"
        frames = [
            {"time": 0, "opacity": 0, **base},
            {"time": round(enter_duration, 4), "opacity": 1, **base, "ease": "sine"},
        ]
    if spec.get("wants_fade_out"):
        frames.extend(
            [
                {"time": round(outro_start, 4), "opacity": 1, **base},
                {"time": round(duration, 4), "opacity": 0, **base, "ease": "sine"},
            ]
        )
    else:
        frames.append({"time": round(duration, 4), "opacity": 1, **base})
    return preset, frames


def _plan_basic_whole_frame_motion(prompt: str, motion: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
    width = max(1.0, float(getattr(motion, "width", 1) or 1))
    height = max(1.0, float(getattr(motion, "height", 1) or 1))
    layers = [
        dict(layer)
        for layer in list(getattr(motion, "figma_layers", []) or [])
        if not str(layer.get("id") or "").startswith("__frame_choreo_")
    ]
    frame_width, frame_height = _figma_frame_size(motion, layers, width, height)
    phase_plan = _basic_choreo_phase_plan(spec)
    if spec.get("camera_only"):
        preset = _scene_camera_kind(str(spec.get("prompt") or ""))
        camera_dsl = _scene_camera_dsl(str(spec.get("prompt") or ""), float(spec.get("duration") or 1.0), effect_duration=float(spec.get("camera_duration") or 0.0) or None)
        keyframes = [dict(frame) for frame in list(camera_dsl.get("keyframes") or []) if isinstance(frame, dict)]
    else:
        preset, keyframes = _basic_whole_frame_keyframes(spec)
    asset_path = str(getattr(motion, "asset_path", "") or "").strip()
    if not asset_path:
        result: list[dict[str, Any]] = []
        for layer in layers:
            if layer.get("visible") is False:
                result.append(layer)
                continue
            recipe = _choreo_recipe(prompt, preset, [dict(frame) for frame in keyframes], ["frame", "basic", preset], phase_plan)
            recipe["transform_reference"] = {"width": frame_width, "height": frame_height}
            layer["motion_recipe"] = recipe
            result.append(layer)
        return result

    motion_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(getattr(motion, "id", "") or "motion")).strip("-") or "motion"
    recipe_tags = ["frame", "scene-camera", "camera-controller"] if spec.get("camera_only") else ["frame", "basic", "whole-frame-composite", preset]
    recipe = _choreo_recipe(
        prompt,
        preset,
        [dict(frame) for frame in keyframes],
        recipe_tags,
        phase_plan,
    )
    recipe["transform_reference"] = {"width": frame_width, "height": frame_height}
    source_layer = {
        "id": f"__frame_choreo_whole_frame_{motion_id}",
        "name": "Whole Figma frame",
        "kind": "image",
        "node_type": "FRAME",
        "visible": True,
        "x": 0,
        "y": 0,
        "width": frame_width,
        "height": frame_height,
        "asset_path": asset_path,
        "opacity": 1,
        "motion_internal": True,
        "motion_recipe": recipe,
    }
    if spec.get("wants_fade_in"):
        source_layer["choreo_video_fade_in"] = max(0.05, float(spec.get("intro_duration") or 0))
    skipped_original_layers: list[dict[str, Any]] = []
    for layer in layers:
        layer.pop("motion_recipe", None)
        layer["whole_frame_static_skip"] = True
        skipped_original_layers.append(layer)
    return [*skipped_original_layers, source_layer]


def _plan_basic_frame_choreography(prompt: str, motion: Any) -> list[dict[str, Any]]:
    width = max(1.0, float(getattr(motion, "width", 1) or 1))
    height = max(1.0, float(getattr(motion, "height", 1) or 1))
    layers = [
        dict(layer)
        for layer in list(getattr(motion, "figma_layers", []) or [])
        if not str(layer.get("id") or "").startswith("__frame_choreo_")
    ]
    spec = _basic_frame_choreography_spec(prompt, max(1.0, float(getattr(motion, "duration", 6) or 6)))
    if spec.get("mode") == "whole-frame":
        return _plan_basic_whole_frame_motion(prompt, motion, spec)
    duration = float(spec["duration"])
    intro_duration = float(spec["intro_duration"])
    build_start = float(spec["build_start"])
    build_end = float(spec["build_end"])
    outro_start = float(spec["outro_start"])
    outro_duration = float(spec["outro_duration"])
    phase_plan = _basic_choreo_phase_plan(spec)
    frame_width, frame_height = _figma_frame_size(motion, layers, width, height)
    frame_area = max(1.0, frame_width * frame_height)
    drop_y = frame_height * 0.9 if spec.get("wants_drop_out") else 0.0
    build_preset = (
        "advanced-composition-build"
        if spec.get("wants_advanced_role_build")
        else "tetris-build"
        if spec.get("wants_tetris_build")
        else "gradient-fade-stagger"
        if spec.get("wants_gradient")
        else "random-fly-in-stagger"
        if spec.get("wants_fly")
        else "basic-layer-fade"
    )
    root_background_ids = {str(layer.get("id") or "") for layer in layers if _is_root_frame_layer(motion, layer)}
    background_ids = root_background_ids | {str(layer.get("id") or "") for layer in layers if _is_background_layer(layer, frame_area)}
    mask_layer_ids = _visual_mask_ids(layers) | {
        str(layer.get("id") or "")
        for layer in layers
        if str(layer.get("id") or "") and layer.get("mask_role") == "visual-mask"
    }
    cluster_keys = _ui_cluster_keys(layers, background_ids | mask_layer_ids)
    cluster_container_ids = {
        key
        for layer_id, key in cluster_keys.items()
        if layer_id != key
    }
    cluster_children: dict[str, list[str]] = {}
    for layer_id, key in cluster_keys.items():
        if layer_id == key:
            continue
        cluster_children.setdefault(key, []).append(layer_id)
    ui_cluster_child_ids: set[str] = set()
    ui_cluster_parent_by_child: dict[str, str] = {}
    ui_cluster_layers_by_container: dict[str, dict[str, Any]] = {}
    for container_id, child_ids in cluster_children.items():
        grouped_ids = [container_id, *child_ids]
        rect = _layer_union_rect_for_ids(layers, grouped_ids, padding=2.0)
        if not rect:
            continue
        cluster_id = f"__frame_choreo_ui_cluster_{_sanitize_choreo_id(container_id)}"
        ui_cluster_child_ids.update(grouped_ids)
        for child_id in grouped_ids:
            ui_cluster_parent_by_child[child_id] = cluster_id
        ui_cluster_layers_by_container[container_id] = {
            "id": cluster_id,
            "name": "UI badge cluster",
            "kind": "shape",
            "node_type": "RECTANGLE",
            "visible": True,
            "x": float(rect.get("x", 0) or 0),
            "y": float(rect.get("y", 0) or 0),
            "width": max(1.0, float(rect.get("width", 1) or 1)),
            "height": max(1.0, float(rect.get("height", 1) or 1)),
            "fill": "rgba(0,0,0,0)",
            "opacity": 1,
            "render_cluster_source": True,
            "source_crop_key_transparent": False,
            "motion_internal": True,
            "cluster_source_kind": "ui",
            "cluster_child_ids": grouped_ids,
        }
    planning_layers: list[dict[str, Any]] = []
    for layer in layers:
        planning_layers.append(layer)
        layer_id = str(layer.get("id") or "")
        ui_cluster = ui_cluster_layers_by_container.get(layer_id)
        if ui_cluster:
            planning_layers.append(dict(ui_cluster))
    motion_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(getattr(motion, "id", "") or "motion")).strip("-") or "motion"
    intro_preset = str(spec.get("intro_preset") or "white-bg-fade")
    white_background = {
        "id": f"__frame_choreo_white_bg_{motion_id}",
        "name": "Frame intro background",
        "kind": "shape",
        "node_type": "RECTANGLE",
        "visible": True,
        "x": 0,
        "y": 0,
        "width": frame_width,
        "height": frame_height,
        "fill": _figma_frame_background_fill(motion, layers),
        "opacity": 1,
        "motion_internal": True,
        "motion_recipe": _choreo_recipe(
            prompt,
            intro_preset,
            [
                {"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
                {"time": round(intro_duration, 4), "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
                {"time": round(outro_start, 4), "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
                {"time": round(duration, 4), "opacity": 0 if spec.get("wants_fade_out") else 1, "x": 0, "y": round(drop_y, 3), "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
            ],
            ["frame", "basic", "white-intro", intro_preset],
            phase_plan,
        ),
    }
    animated_layers = [
        layer
        for layer in planning_layers
        if str(layer.get("id") or "") not in background_ids
        and str(layer.get("id") or "") not in ui_cluster_child_ids
        and layer.get("visible") is not False
    ]
    order = sorted(
        [str(layer.get("id") or index) for index, layer in enumerate(animated_layers)],
        key=lambda layer_id: (
            float(next((item.get("y") or 0 for item in animated_layers if str(item.get("id") or "") == layer_id), 0)),
            float(next((item.get("x") or 0 for item in animated_layers if str(item.get("id") or "") == layer_id), 0)),
            layer_id,
        ),
    )
    rank_by_id = {layer_id: index for index, layer_id in enumerate(order)}
    count = max(1, len(order))
    result = [white_background]
    for index, layer in enumerate(planning_layers):
        layer_id = str(layer.get("id") or index)
        if layer_id in ui_cluster_child_ids:
            layer.pop("motion_recipe", None)
            layer["cluster_parent_id"] = ui_cluster_parent_by_child.get(layer_id)
            result.append(layer)
            continue
        if layer_id in background_ids:
            layer["motion_recipe"] = _choreo_recipe(
                prompt,
                "static-reveal",
                _basic_static_keyframes(
                    intro_duration,
                    build_end,
                    duration,
                    bool(spec.get("wants_fade_out")),
                    outro_start,
                    bool(spec.get("wants_drop_out")),
                    drop_y,
                ),
                ["frame", "basic", "background"],
                phase_plan,
            )
            result.append(layer)
            continue
        if layer.get("visible") is False:
            result.append(layer)
            continue
        rank = rank_by_id.get(layer_id, index)
        entry_duration = min(0.8, max(0.18, float(spec["build_duration"]) / max(1, min(count, 4))))
        delay_window = max(0.0, float(spec["build_duration"]) - entry_duration)
        delay = build_start + (rank / max(1, count - 1)) * delay_window if count > 1 else build_start
        enter_end = min(build_end, delay + entry_duration)
        fly_x = 0.0
        fly_y = 0.0
        if spec.get("wants_fly"):
            fly_x = -80.0 if rank % 2 == 0 else 80.0
            fly_y = -40.0 if rank % 3 == 0 else 44.0
        keyframes = [
            {"time": 0, "opacity": 0, "x": round(fly_x, 3), "y": round(fly_y, 3), "scale": 1, "rotate": 0, "blur": 0},
            {"time": round(delay, 4), "opacity": 0, "x": round(fly_x, 3), "y": round(fly_y, 3), "scale": 1, "rotate": 0, "blur": 0},
            {"time": round(enter_end, 4), "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
            {"time": round(outro_start, 4), "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
            {"time": round(duration, 4), "opacity": 0 if spec.get("wants_fade_out") else 1, "x": 0, "y": round(drop_y, 3), "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
        ]
        recipe = _choreo_recipe(prompt, build_preset, keyframes, ["frame", "basic", build_preset], phase_plan)
        if layer.get("cluster_source_kind") == "ui":
            recipe["transform_reference"] = {
                "width": max(1.0, float(layer.get("width") or 1)),
                "height": max(1.0, float(layer.get("height") or 1)),
            }
        if spec.get("wants_gradient"):
            recipe.setdefault("motion_dsl", {}).setdefault("effects", []).append(
                {"type": "wipe-reveal", "start": round(delay, 4), "duration": round(max(0.05, enter_end - delay), 4), "direction": "down", "softness": 0.18}
            )
        layer["motion_recipe"] = recipe
        result.append(layer)
    return result


def frame_choreography_required_duration(prompt: str, motion_duration: float = 0.0) -> float:
    spec = _basic_frame_choreography_spec(prompt, motion_duration)
    return float(spec["minimum_duration"])


def _phase_sample_times(duration: float, intro_end: float, build_start: float, build_end: float, outro_start: float) -> list[float]:
    candidates = [
        min(max(0.0, duration - 0.05), max(0.05, intro_end * 0.25)),
        min(max(0.0, duration - 0.05), build_start + 0.5),
        min(max(0.0, duration - 0.05), max(build_start, build_end - 0.5)),
        min(max(0.0, duration - 0.05), max(build_end, outro_start + 0.5)),
        min(max(0.0, duration - 0.05), max(outro_start, duration - 0.5)),
    ]
    result: list[float] = []
    for item in candidates:
        value = round(float(item), 3)
        if value not in result:
            result.append(value)
    return result


def _scene_camera_kind(text: str) -> str:
    if re.search(r"hand\s*held|handheld|camera\s*shake|\u0440\u0443\u0447\u043d\w*\s+\u043a\u0430\u043c\u0435\u0440", text):
        return "handheld"
    if re.search(r"pull\s*back|zoom\s*out|\u043e\u0442[\u044a\u044c]\u0435\u0437\u0434|\u043e\u0442\u0434\u0430\u043b", text):
        return "camera-pull"
    if re.search(r"\bpan\b|camera\s*pan|\u043f\u0430\u043d\u043e\u0440\u0430\u043c", text):
        return "camera-pan"
    return "camera-push"


def _scene_camera_direction(text: str) -> str:
    if re.search(r"\bleft\b|\u0432\u043b\u0435\u0432|\u0441\u043b\u0435\u0432", text):
        return "left"
    if re.search(r"\bright\b|\u0432\u043f\u0440\u0430\u0432|\u0441\u043f\u0440\u0430\u0432", text):
        return "right"
    if re.search(r"\bup\b|\u0432\u0432\u0435\u0440\u0445", text):
        return "up"
    if re.search(r"\bdown\b|\u0432\u043d\u0438\u0437", text):
        return "down"
    return "right"


def _scene_camera_dsl(text: str, duration: float, start: float = 0.0, effect_duration: float | None = None) -> dict[str, Any]:
    kind = _scene_camera_kind(text)
    camera_duration = max(0.15, min(float(duration), float(effect_duration or max(0.15, duration - start))))
    end = min(float(duration), float(start) + camera_duration)
    base = {"x": 0, "y": 0, "scale": 1, "scaleX": 1, "scaleY": 1, "rotate": 0, "blur": 0, "opacity": 1}
    frames: list[dict[str, Any]] = [{**base, "time": 0}]
    if start > 0.001:
        frames.append({**base, "time": round(start, 3)})
    if kind == "handheld":
        frames[-1].update({"scale": 1.045})
        frames.append({**base, "time": round(end, 3), "scale": 1.045, "ease": "linear"})
        return {
            "version": 1,
            "keyframes": frames,
            "effects": [{"type": "shake", "start": round(start, 3), "duration": round(camera_duration, 3), "amplitude": 1.6, "frequency": 6.5}],
        }
    if kind == "camera-pull":
        frames[-1].update({"scale": 1.12})
        frames.append({**base, "time": round(end, 3), "scale": 1, "ease": "sine"})
    elif kind in {"pan", "camera-pan"}:
        direction = _scene_camera_direction(text)
        axis_x = -7 if direction == "right" else 7 if direction == "left" else 0
        axis_y = -7 if direction == "down" else 7 if direction == "up" else 0
        frames.append({**base, "time": round(end, 3), "x": axis_x, "y": axis_y, "scale": 1.04, "ease": "sine"})
    else:
        frames.append({**base, "time": round(end, 3), "scale": 1.12, "ease": "sine"})
    return {"version": 1, "keyframes": frames, "effects": []}


def _scene_camera_plan(text: str, duration: float, build_end: float, advanced: bool) -> dict[str, Any] | None:
    if not _contains_any(text, CAMERA_MARKERS):
        return None
    explicit_duration = _duration_from_patterns_optional(text, CAMERA_DURATION_PATTERNS)
    start = build_end if advanced and re.search(r"after|then|\u043f\u043e\u0441\u043b\u0435|\u043f\u043e\u0442\u043e\u043c|\u0437\u0430\u0442\u0435\u043c", text) else 0.0
    if start >= duration - 0.15:
        start = 0.0
    dsl = _scene_camera_dsl(text, duration, start=start, effect_duration=explicit_duration)
    return {
        "id": _scene_camera_kind(text),
        "label": "Scene camera",
        "scope": "post-composite",
        "start": round(start, 3),
        "duration": round(float(explicit_duration or max(0.15, duration - start)), 3),
        "motion_dsl": dsl,
    }


def _frame_phase_plan(
    spec: dict[str, float | bool | str],
    duration: float,
    build_start: float,
    build_end: float,
    outro_start: float,
) -> dict[str, Any]:
    intro_duration = float(spec["intro_duration"])
    build_duration = max(0.0, build_end - build_start)
    outro_duration = max(0.0, duration - outro_start)
    camera = _scene_camera_plan(str(spec.get("prompt") or ""), duration, build_end, bool(spec.get("advanced_choreography")))
    if spec.get("camera_only"):
        phases = [
            {
                "id": "camera",
                "preset": str((camera or {}).get("id") or "scene-camera"),
                "start": 0.0,
                "duration": round(duration, 3),
                "visibility": "exact-source-frame",
            }
        ]
        result = {
            "version": 1,
            "scope": "whole-frame",
            "time_mode": "absolute",
            "duration": round(duration, 3),
            "minimum_duration": round(float(spec["minimum_duration"]), 3),
            "phases": phases,
            "acceptance": {"sample_times": [0.0, round(duration * 0.5, 3), round(max(0.0, duration - 0.05), 3)]},
        }
        if camera:
            result["camera"] = camera
        return result
    phases: list[dict[str, Any]] = [
        {
            "id": "intro",
            "preset": str(spec.get("intro_preset") or ("white-bg-fade" if spec["wants_white_intro"] else "static-reveal")),
            "start": 0.0,
            "duration": round(intro_duration, 3),
            "visibility": "background-only" if spec["wants_white_intro"] or spec.get("wants_venetian_blinds") or spec.get("wants_intro_template") else "frame",
        },
        {
            "id": "build",
            "preset": str(spec["build_preset"]),
            "start": round(build_start, 3),
            "duration": round(build_duration, 3),
            "visibility": "visible-content-layers",
            "order": "top-down-by-role" if spec.get("wants_order_top_down") else "stable-random" if spec["wants_random_stagger"] else "ordered",
        },
    ]
    if spec.get("advanced_choreography"):
        photo_start, photo_span, _photo_entry = ADVANCED_ROLE_PHASES["photo"]
        text_start, text_span, _text_entry = ADVANCED_ROLE_PHASES["text"]
        button_start, button_span, _button_entry = ADVANCED_ROLE_PHASES["button"]
        phases[1]["subphases"] = [
            {
                "id": "photos",
                "preset": "parallax-photo" if spec.get("wants_photo_parallax") else "tetris-build" if spec.get("wants_tetris_build") else "static-reveal",
                "start": round(build_start + build_duration * photo_start, 3),
                "duration": round(build_duration * photo_span, 3),
                "target": "image-layers",
            },
            {
                "id": "text",
                "preset": "text-slide-up-lines" if spec.get("wants_text_slide_up_lines") else "fade-up-lines" if spec.get("wants_text_fade_up_lines") else "static-reveal",
                "start": round(build_start + build_duration * text_start, 3),
                "duration": round(build_duration * text_span, 3),
                "target": "text-layers",
                "order": "top-down",
            },
            {
                "id": "buttons",
                "preset": "button-y-rise" if spec.get("wants_button_y_rise") else "tetris-build" if spec.get("wants_tetris_build") else "static-reveal",
                "start": round(build_start + build_duration * button_start, 3),
                "duration": round(build_duration * button_span, 3),
                "target": "button-clusters",
            },
        ]
        if spec.get("wants_tetris_build"):
            element_start, element_span, _element_entry = ADVANCED_ROLE_PHASES["element"]
            phases[1]["subphases"].append(
                {
                    "id": "remaining",
                    "preset": "tetris-build",
                    "start": round(build_start + build_duration * element_start, 3),
                    "duration": round(build_duration * element_span, 3),
                    "target": "remaining-elements",
                }
            )
    if outro_start - build_end > 0.05:
        phases.append(
            {
                "id": "hold",
                "preset": "static",
                "start": round(build_end, 3),
                "duration": round(outro_start - build_end, 3),
                "visibility": "exact-source-frame",
            }
        )
    phases.append(
        {
            "id": "outro",
            "preset": str(spec["outro_preset"]),
            "start": round(outro_start, 3),
            "duration": round(outro_duration, 3),
            "anchor": "end",
            "visibility": "full-frame",
        }
    )
    result = {
        "version": 1,
        "scope": "whole-frame",
        "time_mode": "absolute",
        "duration": round(duration, 3),
        "minimum_duration": round(float(spec["minimum_duration"]), 3),
        "phases": phases,
        "acceptance": {
            "sample_times": _phase_sample_times(duration, intro_duration, build_start, build_end, outro_start)
        },
    }
    if camera:
        result["camera"] = camera
    return result


def _static_reveal_keyframes(intro_end: float, outro_start: float, duration: float, fade_in: bool = False, fade_out: bool = False) -> list[dict[str, Any]]:
    return [
        {"time": 0, "opacity": 0 if fade_in else 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": intro_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "smooth"},
        {"time": outro_start, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": duration, "opacity": 0 if fade_out else 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "smooth"},
    ]


def _gravity_outro_keyframes(base_x: float, base_y: float, duration: float, outro_start: float, fade_out: bool, rotate: float) -> list[dict[str, Any]]:
    midpoint = min(duration, outro_start + (duration - outro_start) * 0.42)
    return [
        {"time": outro_start, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": midpoint, "opacity": 1 if not fade_out else 0.86, "x": base_x * 0.18, "y": max(18, base_y * 0.1), "scale": 1.0, "rotate": rotate * 0.2, "ease": "power"},
        {"time": duration, "opacity": 0 if fade_out else 1, "x": base_x, "y": base_y, "scale": 0.9, "rotate": rotate, "blur": 2, "ease": "gravity"},
    ]


def _fade_only_outro_keyframes(duration: float, outro_start: float, fade_out: bool) -> list[dict[str, Any]]:
    fade_end = duration
    return [
        {"time": 0, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": outro_start, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": fade_end, "opacity": 0 if fade_out else 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0.8 if fade_out else 0, "ease": "sine"},
        {"time": duration, "opacity": 0 if fade_out else 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0.8 if fade_out else 0, "ease": "sine"},
    ]


def _hide_for_unified_frame_outro_keyframes(duration: float, outro_start: float) -> list[dict[str, Any]]:
    hide_time = min(duration, outro_start + 0.03)
    return [
        {"time": outro_start, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
        {"time": hide_time, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
        {"time": duration, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
    ]


def _full_frame_drop_overlay_layer(
    prompt: str,
    motion_id: str,
    frame_width: float,
    frame_height: float,
    duration: float,
    outro_start: float,
    fade_out: bool,
    phase_plan: dict[str, Any],
) -> dict[str, Any]:
    outro_span = max(0.05, duration - outro_start)
    mid = min(duration, outro_start + outro_span * 0.46)
    recipe = _choreo_recipe(
        prompt,
        "full-frame-drop",
        [
            {"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
            {"time": max(0.0, outro_start - 0.02), "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
            {"time": outro_start, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
            {"time": mid, "opacity": 0.94 if fade_out else 1, "x": 0, "y": 14, "scale": 0.995, "rotate": 1.0, "blur": 0.1, "ease": "power"},
            {"time": duration, "opacity": 0 if fade_out else 1, "x": 0, "y": 136, "scale": 0.965, "rotate": 4.0, "blur": 1.2, "ease": "gravity"},
        ],
        ["frame", "full-frame-drop", "unified-outro", "exact-source-frame"],
        phase_plan,
    )
    recipe["transform_reference"] = {"width": frame_width, "height": frame_height}
    recipe["ignore_hold_window"] = True
    return {
        "id": f"__frame_choreo_full_frame_drop_{motion_id}",
        "name": "Full-frame drop overlay",
        "kind": "shape",
        "node_type": "RECTANGLE",
        "visible": True,
        "x": 0,
        "y": 0,
        "width": frame_width,
        "height": frame_height,
        "fill": "rgba(0,0,0,0)",
        "opacity": 1,
        "render_cluster_source": True,
        "source_crop_key_transparent": False,
        "motion_internal": True,
        "motion_recipe": recipe,
    }


def _frame_shatter_overlay_layers(
    prompt: str,
    frame_width: float,
    frame_height: float,
    duration: float,
    outro_start: float,
    fade_out: bool,
    phase_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    columns = 5
    rows = 4
    tile_w = frame_width / columns
    tile_h = frame_height / rows
    shards: list[dict[str, Any]] = []
    for row in range(rows):
        for col in range(columns):
            shard_id = f"__frame_choreo_shard_{row}_{col}"
            center_x = (col + 0.5) / columns - 0.5
            center_y = (row + 0.5) / rows - 0.5
            spread_x = center_x * (52 + _stable_fraction(shard_id, 11) * 34)
            spread_y = 16 + max(0.0, center_y + 0.2) * 54 + _stable_fraction(shard_id, 23) * 18
            rotate = -30 + _stable_fraction(shard_id, 37) * 60
            midpoint = min(duration, outro_start + (duration - outro_start) * (0.38 + _stable_fraction(shard_id, 41) * 0.12))
            recipe = _choreo_recipe(
                prompt,
                "full-frame-shatter",
                [
                    {"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
                    {"time": max(0.0, outro_start - 0.04), "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
                    {"time": outro_start + 0.04, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
                    {"time": midpoint, "opacity": 0.84 if fade_out else 1, "x": spread_x * 0.34, "y": spread_y * 0.42, "scale": 0.985, "rotate": rotate * 0.45, "blur": 0.2, "ease": "power"},
                    {"time": duration, "opacity": 0 if fade_out else 1, "x": spread_x, "y": spread_y, "scale": 0.94, "rotate": rotate, "blur": 1.4, "ease": "gravity"},
                ],
                ["frame", "shatter-overlay", "full-frame-shatter", "outro"],
                phase_plan,
            )
            recipe["transform_reference"] = {"width": frame_width, "height": frame_height}
            recipe["ignore_hold_window"] = True
            shards.append(
                {
                    "id": shard_id,
                    "name": f"Shatter shard {row + 1}-{col + 1}",
                    "kind": "shape",
                    "node_type": "RECTANGLE",
                    "visible": True,
                    "x": round(col * tile_w, 3),
                    "y": round(row * tile_h, 3),
                    "width": round(tile_w if col < columns - 1 else frame_width - col * tile_w, 3),
                    "height": round(tile_h if row < rows - 1 else frame_height - row * tile_h, 3),
                    "opacity": 1,
                    "render_cluster_source": True,
                    "choreo_static_skip": True,
                    "motion_recipe": recipe,
                }
            )
    return shards


def plan_frame_choreography(prompt: str, motion: Any) -> list[dict[str, Any]]:
    return _plan_basic_frame_choreography(prompt, motion)

    width = max(1.0, float(getattr(motion, "width", 1) or 1))
    height = max(1.0, float(getattr(motion, "height", 1) or 1))
    duration = max(1.0, float(getattr(motion, "duration", 6) or 6))
    spec = _parse_frame_choreography_spec(prompt, duration)
    duration = float(spec["duration"])
    intro_duration = float(spec["intro_duration"])
    build_duration = float(spec["build_duration"])
    outro_duration = float(spec["outro_duration"])
    build_start = intro_duration
    build_end = min(duration, build_start + build_duration)
    outro_start = max(build_end, duration - outro_duration)
    phase_plan = _frame_phase_plan(spec, duration, build_start, build_end, outro_start)
    intro_preset = str(spec.get("intro_preset") or ("white-bg-fade" if spec.get("wants_white_intro") else "static-reveal"))
    intro_reveal = bool(spec["wants_white_intro"] or spec.get("wants_venetian_blinds") or spec.get("wants_intro_template"))
    layers = [
        dict(layer)
        for layer in list(getattr(motion, "figma_layers", []) or [])
        if not str(layer.get("id") or "").startswith("__frame_choreo_")
    ]
    frame_width, frame_height = _figma_frame_size(motion, layers, width, height)
    if spec.get("camera_only"):
        motion_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(getattr(motion, "id", "") or "motion")).strip("-") or "motion"
        camera_controller = {
            "id": f"__frame_choreo_camera_{motion_id}",
            "name": "Scene camera controller",
            "kind": "shape",
            "node_type": "RECTANGLE",
            "visible": True,
            "x": 0,
            "y": 0,
            "width": frame_width,
            "height": frame_height,
            "fill": "rgba(0,0,0,0)",
            "opacity": 0,
            "motion_internal": True,
            "motion_recipe": _choreo_recipe(
                prompt,
                "scene-camera",
                [{"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1}],
                ["frame", "scene-camera", "camera-controller"],
                phase_plan,
            ),
        }
        return [*layers, camera_controller]
    frame_area = max(1.0, frame_width * frame_height)
    root_background_ids = {str(layer.get("id") or "") for layer in layers if _is_root_frame_layer(motion, layer)}
    background_ids = root_background_ids | {str(layer.get("id") or "") for layer in layers if _is_background_layer(layer, frame_area)}
    frame_background_fill = _figma_frame_background_fill(motion, layers)
    mask_ids = _visual_mask_ids(layers)
    mask_layer_ids = mask_ids | {
        str(layer.get("id") or "")
        for layer in layers
        if str(layer.get("id") or "") and layer.get("mask_role") == "visual-mask"
    }
    visual_clusters = _visual_clip_clusters(layers, mask_layer_ids)
    visual_cluster_child_ids = {
        child_id
        for cluster in visual_clusters.values()
        for child_id in list(cluster.get("child_ids") or [])
    }
    visual_cluster_parent_by_child = {
        child_id: str(cluster.get("id") or "")
        for cluster in visual_clusters.values()
        for child_id in list(cluster.get("child_ids") or [])
    }
    excluded_ids = background_ids | mask_layer_ids | visual_cluster_child_ids
    cluster_keys = _ui_cluster_keys(layers, excluded_ids)
    cluster_refs = _cluster_reference_rects(layers, cluster_keys)
    cluster_container_ids = {
        key
        for layer_id, key in cluster_keys.items()
        if layer_id != key
    }
    cluster_children: dict[str, list[str]] = {}
    for layer_id, key in cluster_keys.items():
        if layer_id == key:
            continue
        cluster_children.setdefault(key, []).append(layer_id)
    ui_cluster_layers_by_container: dict[str, dict[str, Any]] = {}
    ui_cluster_child_ids: set[str] = set()
    ui_cluster_parent_by_child: dict[str, str] = {}
    for container_id, child_ids in cluster_children.items():
        grouped_ids = [container_id, *child_ids]
        rect = _layer_union_rect_for_ids(layers, grouped_ids, padding=2.0)
        if not rect:
            continue
        cluster_id = f"__frame_choreo_ui_cluster_{_sanitize_choreo_id(container_id)}"
        ui_cluster_child_ids.update(grouped_ids)
        for child_id in grouped_ids:
            ui_cluster_parent_by_child[child_id] = cluster_id
        ui_cluster_layers_by_container[container_id] = {
            "id": cluster_id,
            "name": "UI badge cluster",
            "kind": "shape",
            "node_type": "RECTANGLE",
            "visible": True,
            "x": float(rect.get("x", 0) or 0),
            "y": float(rect.get("y", 0) or 0),
            "width": max(1.0, float(rect.get("width", 1) or 1)),
            "height": max(1.0, float(rect.get("height", 1) or 1)),
            "fill": "rgba(0,0,0,0)",
            "opacity": 1,
            "render_cluster_source": True,
            "source_crop_key_transparent": False,
            "motion_internal": True,
            "cluster_source_kind": "ui",
            "cluster_child_ids": grouped_ids,
        }
    planning_layers: list[dict[str, Any]] = []
    for layer in layers:
        layer_id = str(layer.get("id") or "")
        planning_layers.append(layer)
        cluster = visual_clusters.get(layer_id)
        if cluster:
            planning_layers.append(
                {
                    "id": str(cluster["id"]),
                    "name": str(cluster.get("name") or "Visual cluster"),
                    "kind": "shape",
                    "node_type": "RECTANGLE",
                    "visible": True,
                    "x": float(cluster.get("x", 0) or 0),
                    "y": float(cluster.get("y", 0) or 0),
                    "width": max(1.0, float(cluster.get("width", 1) or 1)),
                    "height": max(1.0, float(cluster.get("height", 1) or 1)),
                    "fill": "rgba(0,0,0,0)",
                    "opacity": 1,
                    "render_cluster_source": True,
                    "source_crop_key_transparent": False,
                    "motion_internal": True,
                    "cluster_child_ids": list(cluster.get("child_ids") or []),
                    "cluster_source_mask_id": str(cluster.get("source_mask_id") or ""),
                }
            )
        ui_cluster = ui_cluster_layers_by_container.get(layer_id)
        if ui_cluster:
            planning_layers.append(dict(ui_cluster))
    motion_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(getattr(motion, "id", "") or "motion")).strip("-") or "motion"
    white_background = {
        "id": f"__frame_choreo_white_bg_{motion_id}",
        "name": "Frame intro background",
        "kind": "shape",
        "node_type": "RECTANGLE",
        "visible": True,
        "x": 0,
        "y": 0,
        "width": frame_width,
        "height": frame_height,
        "fill": frame_background_fill,
        "opacity": 1,
        "choreo_video_fade_in": intro_duration,
        "motion_recipe": _choreo_recipe(
            prompt,
            intro_preset,
            [
                {"time": 0, "opacity": 0 if intro_reveal else 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
                {"time": intro_duration, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "ease": "smooth"},
                {"time": outro_start, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0},
                {"time": duration, "opacity": 0 if spec["wants_fade_out"] or spec.get("wants_full_frame_drop") else 1, "x": 0 if spec["wants_shatter"] or spec.get("wants_full_frame_drop") or spec.get("wants_fade_only_outro") else -18, "y": 0 if spec["wants_shatter"] or spec.get("wants_full_frame_drop") or spec.get("wants_fade_only_outro") else 260, "scale": 1 if spec["wants_shatter"] or spec.get("wants_full_frame_drop") or spec.get("wants_fade_only_outro") else 0.96, "rotate": 0 if spec["wants_shatter"] or spec.get("wants_full_frame_drop") or spec.get("wants_fade_only_outro") else -11, "blur": 0.6 if spec["wants_shatter"] or spec.get("wants_fade_only_outro") else 0 if spec.get("wants_full_frame_drop") else 2, "ease": "sine" if spec["wants_shatter"] or spec.get("wants_full_frame_drop") or spec.get("wants_fade_only_outro") else "gravity"},
            ],
            ["frame", "white-intro", "background-only", str(spec["outro_preset"])],
            phase_plan,
        ),
    }
    _append_intro_template_effects(white_background["motion_recipe"], spec, intro_duration)
    if spec.get("wants_full_frame_drop"):
        white_background["motion_recipe"]["motion_dsl"]["keyframes"] = [
            {"time": 0, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0},
            {"time": intro_duration, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "ease": "smooth"},
            *_hide_for_unified_frame_outro_keyframes(duration, outro_start),
        ]
    animated_layers = [
        layer
        for layer in planning_layers
        if str(layer.get("id") or "") not in background_ids
        and str(layer.get("id") or "") not in mask_layer_ids
        and str(layer.get("id") or "") not in visual_cluster_child_ids
        and str(layer.get("id") or "") not in ui_cluster_child_ids
        and layer.get("visible") is not False
    ]
    animation_keys = []
    for index, layer in enumerate(animated_layers):
        layer_id = str(layer.get("id") or index)
        key = cluster_keys.get(layer_id, layer_id)
        if key not in animation_keys:
            animation_keys.append(key)
    animated_count = max(1, len(animation_keys))
    key_rects: dict[str, tuple[float, float]] = {}
    for layer in animated_layers:
        layer_id = str(layer.get("id") or "")
        key = cluster_keys.get(layer_id, layer_id)
        if key not in key_rects:
            key_rects[key] = (
                float(layer.get("y") or 0),
                float(layer.get("x") or 0),
            )
    if spec.get("wants_order_top_down"):
        ranked_keys = sorted(animation_keys, key=lambda key: (*key_rects.get(key, (0.0, 0.0)), key))
    else:
        ranked_keys = sorted(animation_keys, key=lambda key: (_stable_fraction(key, 17), key))
    rank_by_key = {key: rank for rank, key in enumerate(ranked_keys)}
    advanced = bool(spec.get("advanced_choreography") or spec.get("wants_advanced_role_build"))

    def semantic_role(layer: dict[str, Any]) -> str:
        layer_id = str(layer.get("id") or "")
        name = str(layer.get("name") or "").casefold()
        fill = str(layer.get("fill") or "").casefold()
        width_value = max(1.0, float(layer.get("width") or 1))
        height_value = max(1.0, float(layer.get("height") or 1))
        key = cluster_keys.get(layer_id, layer_id)
        is_ui = (
            key != layer_id
            or layer_id in cluster_container_ids
            or layer.get("cluster_source_kind") == "ui"
            or (
                layer.get("kind") in {"shape", "image"}
                and width_value >= height_value * 1.8
                and 10 <= height_value <= max(120.0, frame_height * 0.14)
                and ("0,0,0" in fill or "0, 0, 0" in fill or "black" in fill or "#" in fill or "follow" in name or "button" in name)
            )
        )
        if is_ui:
            return "button"
        if layer.get("kind") == "image" or str(layer_id).startswith("__frame_choreo_cluster_"):
            return "photo"
        if layer.get("kind") == "text":
            return "text"
        return "element"

    role_keys: dict[str, list[str]] = {"photo": [], "text": [], "button": [], "element": []}
    if advanced:
        for layer in animated_layers:
            layer_id = str(layer.get("id") or "")
            key = cluster_keys.get(layer_id, layer_id)
            role = semantic_role(layer)
            if key not in role_keys.setdefault(role, []):
                role_keys[role].append(key)
        key_lookup = {str(layer.get("id") or ""): layer for layer in animated_layers}
        for role, keys in list(role_keys.items()):
            role_keys[role] = sorted(
                keys,
                key=lambda key: (
                    float((key_lookup.get(key) or {}).get("y") or 0),
                    float((key_lookup.get(key) or {}).get("x") or 0),
                    key,
                ),
            )
    role_rank_by_key = {key: index for keys in role_keys.values() for index, key in enumerate(keys)}
    role_count_by_key = {key: len(keys) for keys in role_keys.values() for key in keys}
    directions = [("left", "top"), ("right", "top"), ("left", "bottom"), ("right", "bottom"), ("center", "top"), ("center", "bottom")]
    result = [white_background]
    for index, layer in enumerate(planning_layers):
        layer_id = str(layer.get("id") or index)
        if not (
            str(layer_id).startswith("__frame_choreo_cluster_")
            or str(layer_id).startswith("__frame_choreo_ui_cluster_")
        ):
            layer.pop("render_cluster_source", None)
            layer.pop("cluster_child_ids", None)
            layer.pop("cluster_parent_id", None)
            layer.pop("source_crop_key_transparent", None)
            layer.pop("motion_internal", None)
            layer.pop("cluster_source_kind", None)
        if layer_id in visual_cluster_child_ids:
            layer.pop("motion_recipe", None)
            layer["cluster_parent_id"] = visual_cluster_parent_by_child.get(layer_id)
            result.append(layer)
            continue
        if layer_id in ui_cluster_child_ids:
            layer.pop("motion_recipe", None)
            layer["cluster_parent_id"] = ui_cluster_parent_by_child.get(layer_id)
            result.append(layer)
            continue
        if layer_id in mask_layer_ids:
            layer.pop("motion_recipe", None)
            layer["choreo_static_skip"] = True
            layer["mask_role"] = "visual-mask"
            result.append(layer)
            continue
        if layer_id in background_ids:
            if spec.get("wants_full_frame_drop"):
                base_keyframes = _static_reveal_keyframes(
                    intro_duration,
                    outro_start,
                    duration,
                    fade_in=intro_reveal,
                    fade_out=False,
                )
                base_keyframes = [*base_keyframes[:2], *_hide_for_unified_frame_outro_keyframes(duration, outro_start)]
            elif spec["wants_shatter"]:
                base_keyframes = _static_reveal_keyframes(
                    intro_duration,
                    outro_start,
                    duration,
                    fade_in=intro_reveal,
                    fade_out=bool(spec["wants_fade_out"]),
                )
            else:
                base_keyframes = _static_reveal_keyframes(
                    intro_duration,
                    outro_start,
                    duration,
                    fade_in=intro_reveal,
                    fade_out=bool(spec["wants_fade_out"]),
                )
                if spec["wants_gravity_drop"]:
                    base_keyframes[-1].update({"y": 240, "rotate": -6, "scale": 0.97, "ease": "gravity"})
            background_preset = intro_preset if intro_reveal else "static-reveal"
            layer["motion_recipe"] = _choreo_recipe(
                prompt,
                background_preset,
                base_keyframes,
                ["frame", "background", background_preset, str(spec["outro_preset"])],
                phase_plan,
            )
            _append_intro_template_effects(layer["motion_recipe"], spec, intro_duration)
            result.append(layer)
            continue
        animation_key = cluster_keys.get(layer_id, layer_id)
        is_cluster_member = animation_key != layer_id
        is_cluster_container = layer_id in cluster_container_ids
        is_ui_cluster = is_cluster_member or is_cluster_container or layer.get("cluster_source_kind") == "ui"
        rank = rank_by_key.get(animation_key, index)
        enter_span = max(0.18, build_end - build_start)
        base_enter = min(0.95, max(0.32, enter_span * 0.36))
        delay_limit = max(build_start, build_end - base_enter)
        delay = build_start if animated_count <= 1 else build_start + (rank / max(1, animated_count - 1)) * max(0.0, delay_limit - build_start)
        enter_end = min(build_end, delay + base_enter * (0.86 + _stable_fraction(animation_key, 31) * 0.28))
        direction = directions[int(_stable_fraction(animation_key, 43) * len(directions)) % len(directions)]
        ref_rect = cluster_refs.get(layer_id) if isinstance(cluster_refs.get(layer_id), dict) else {}
        ref_w_units = max(1.0, float(ref_rect.get("width") or layer.get("width") or 1))
        ref_h_units = max(1.0, float(ref_rect.get("height") or layer.get("height") or 1))
        layer_x = float(layer.get("x") or 0)
        layer_y = float(layer.get("y") or 0)
        layer_w = max(1.0, float(layer.get("width") or 1))
        layer_h = max(1.0, float(layer.get("height") or 1))
        horizontal, vertical = direction
        if layer.get("kind") in {"image", "text"} and not is_ui_cluster:
            layer_center_x = layer_x + layer_w / 2.0
            if layer_center_x >= frame_width * 0.55:
                horizontal = "right"
            elif layer_center_x <= frame_width * 0.45:
                horizontal = "left"
        if layer.get("kind") == "text" and not is_ui_cluster:
            vertical = "center"
        if layer.get("kind") == "image" and not is_ui_cluster:
            layer_center_y = layer_y + layer_h / 2.0
            if layer_center_y >= frame_height * 0.55:
                vertical = "bottom"
            elif layer_center_y <= frame_height * 0.45:
                vertical = "top"
        entry_padding = max(8.0, min(frame_width, frame_height) * 0.012)
        offset_x_px, offset_y_px = _entry_offset_inside_frame(
            horizontal=horizontal,
            vertical=vertical,
            frame_width=frame_width,
            frame_height=frame_height,
            layer_x=layer_x,
            layer_y=layer_y,
            layer_w=layer_w,
            layer_h=layer_h,
            padding=entry_padding,
        )
        max_entry_distance = max(80.0, min(max(frame_width, frame_height) * 0.24, max(layer_w, layer_h) * 0.9))
        if is_ui_cluster:
            max_entry_distance = max(64.0, min(max_entry_distance, 180.0))
        offset_x_px, offset_y_px = _limit_entry_offset(offset_x_px, offset_y_px, max_entry_distance)
        direction_x = (offset_x_px / ref_w_units) * 100.0
        direction_y = (offset_y_px / ref_h_units) * 100.0
        rotate_in = 0
        shard_x = -105 + _stable_fraction(animation_key, 71) * 210
        shard_y = 210 + _stable_fraction(animation_key, 83) * 220
        shard_rotate = -44 + _stable_fraction(animation_key, 97) * 88
        role = semantic_role(layer)
        layer_preset = str(spec["build_preset"])
        order_tag = "random-order" if spec["wants_random_stagger"] else "ordered"
        if advanced:
            build_span = max(0.15, build_end - build_start)
            role_rank = role_rank_by_key.get(animation_key, rank)
            role_count = max(1, role_count_by_key.get(animation_key, animated_count))

            def phase_delay(start_fraction: float, span_fraction: float, entry_fraction: float) -> tuple[float, float]:
                phase_start = build_start + build_span * start_fraction
                phase_end = min(build_end, build_start + build_span * (start_fraction + span_fraction))
                entry_duration = max(0.12, min(phase_end - phase_start, build_span * entry_fraction))
                delay_window = max(0.0, phase_end - phase_start - entry_duration)
                if role_count <= 1:
                    start_time = phase_start
                else:
                    start_time = phase_start + (role_rank / max(1, role_count - 1)) * delay_window
                return start_time, min(build_end, start_time + entry_duration)

            if role == "photo":
                delay, enter_end = phase_delay(*ADVANCED_ROLE_PHASES["photo"])
                if spec.get("wants_tetris_build") and not spec.get("wants_photo_parallax"):
                    layer_preset = "tetris-build"
                    snap = delay + max(0.08, (enter_end - delay) * 0.72)
                    keyframes = [
                        {"time": 0, "opacity": 0, "x": 0, "y": -126, "scale": 1, "rotate": 0, "blur": 0},
                        {"time": delay, "opacity": 0, "x": 0, "y": -126, "scale": 1, "rotate": 0, "blur": 0},
                        {"time": snap, "opacity": 1, "x": 0, "y": 18, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
                        {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "power"},
                    ]
                else:
                    layer_preset = "parallax-photo" if spec.get("wants_photo_parallax") else "static-reveal"
                    px = (-7.0 if layer_x + layer_w / 2 < frame_width / 2 else 7.0) + (_stable_fraction(animation_key, 109) - 0.5) * 4.0
                    py = (-4.0 if layer_y + layer_h / 2 < frame_height / 2 else 4.0) + (_stable_fraction(animation_key, 113) - 0.5) * 3.0
                    keyframes = [
                        {"time": 0, "opacity": 0, "x": px, "y": py, "scale": 1.045, "rotate": 0, "blur": 0.4},
                        {"time": delay, "opacity": 0, "x": px, "y": py, "scale": 1.045, "rotate": 0, "blur": 0.4},
                        {"time": (delay + enter_end) / 2, "opacity": 0.82, "x": px * 0.42, "y": py * 0.42, "scale": 1.018, "rotate": 0, "blur": 0.15, "ease": "power"},
                        {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "expo"},
                    ]
            elif role == "text":
                delay, enter_end = phase_delay(*ADVANCED_ROLE_PHASES["text"])
                layer_preset = "text-slide-up-lines" if spec.get("wants_text_slide_up_lines") else "fade-up-lines" if spec.get("wants_text_fade_up_lines") else "static-reveal"
                start_y = 52 if spec.get("wants_text_slide_up_lines") else 18
                keyframes = [
                    {"time": 0, "opacity": 0, "x": 0, "y": start_y, "scale": 1, "rotate": 0, "blur": 0.8},
                    {"time": delay, "opacity": 0, "x": 0, "y": start_y, "scale": 1, "rotate": 0, "blur": 0.8},
                    {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "expo"},
                ]
            elif role == "button":
                delay, enter_end = phase_delay(*ADVANCED_ROLE_PHASES["button"])
                if spec.get("wants_tetris_build") and not spec.get("wants_button_y_rise"):
                    layer_preset = "tetris-build"
                    snap = delay + max(0.08, (enter_end - delay) * 0.72)
                    keyframes = [
                        {"time": 0, "opacity": 0, "x": 0, "y": -120, "scale": 1, "rotate": 0, "blur": 0},
                        {"time": delay, "opacity": 0, "x": 0, "y": -120, "scale": 1, "rotate": 0, "blur": 0},
                        {"time": snap, "opacity": 1, "x": 0, "y": 14, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
                        {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "power"},
                    ]
                else:
                    layer_preset = "button-y-rise" if spec.get("wants_button_y_rise") else "static-reveal"
                    keyframes = [
                        {"time": 0, "opacity": 0, "x": 0, "y": 58, "scale": 0.985, "rotate": 0, "blur": 0.25},
                        {"time": delay, "opacity": 0, "x": 0, "y": 58, "scale": 0.985, "rotate": 0, "blur": 0.25},
                        {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "expo"},
                    ]
            else:
                delay, enter_end = phase_delay(*ADVANCED_ROLE_PHASES["element"])
                if spec.get("wants_tetris_build"):
                    layer_preset = "tetris-build"
                    snap = delay + max(0.08, (enter_end - delay) * (0.66 + _stable_fraction(animation_key, 127) * 0.16))
                    column_offset = (-10 + _stable_fraction(animation_key, 131) * 20)
                    keyframes = [
                        {"time": 0, "opacity": 0, "x": column_offset, "y": -135, "scale": 1, "rotate": 0, "blur": 0},
                        {"time": delay, "opacity": 0, "x": column_offset, "y": -135, "scale": 1, "rotate": 0, "blur": 0},
                        {"time": snap, "opacity": 1, "x": column_offset * 0.25, "y": 20, "scale": 1, "rotate": 0, "blur": 0, "ease": "linear"},
                        {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "power"},
                    ]
                else:
                    layer_preset = "static-reveal"
                    keyframes = [
                        {"time": 0, "opacity": 0, "x": 0, "y": 10, "scale": 1, "rotate": 0, "blur": 0.35},
                        {"time": delay, "opacity": 0, "x": 0, "y": 10, "scale": 1, "rotate": 0, "blur": 0.35},
                        {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "expo"},
                    ]
            order_tag = f"{role}-phase"
        else:
            if spec.get("wants_gradient_fade_entry"):
                mid = delay + (enter_end - delay) * 0.55
                keyframes = [
                    {"time": 0, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0.3},
                    {"time": delay, "opacity": 0, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0.3},
                    {"time": mid, "opacity": 0.64, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0.12, "ease": "sine"},
                    {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "sine"},
                ]
            else:
                keyframes = [
                    {"time": 0, "opacity": 0, "x": direction_x, "y": direction_y, "scale": 1, "rotate": rotate_in, "blur": 0},
                    {"time": delay, "opacity": 0, "x": direction_x, "y": direction_y, "scale": 1, "rotate": rotate_in, "blur": 0},
                    {"time": enter_end, "opacity": 1, "x": 0, "y": 0, "scale": 1, "rotate": 0, "blur": 0, "ease": "expo"},
                ]
        if spec.get("wants_full_frame_drop"):
            keyframes.extend(_hide_for_unified_frame_outro_keyframes(duration, outro_start))
        elif spec["wants_shatter"] or spec.get("wants_fade_only_outro"):
            keyframes.extend(_fade_only_outro_keyframes(duration, outro_start, bool(spec["wants_fade_out"]))[1:])
        else:
            keyframes.extend(_gravity_outro_keyframes(shard_x, shard_y, duration, outro_start, bool(spec["wants_fade_out"]), shard_rotate))
        recipe = _choreo_recipe(
            prompt,
            layer_preset,
            keyframes,
            [
                "frame",
                layer_preset,
                "contained-entry",
                order_tag,
                str(spec["outro_preset"]),
            ],
            phase_plan,
        )
        if spec.get("wants_gradient_fade_entry") or spec.get("wants_order_top_down"):
            effects = recipe.setdefault("motion_dsl", {}).setdefault("effects", [])
            effects.append(
                {
                    "type": "wipe-reveal",
                    "start": round(delay, 4),
                    "duration": round(max(0.12, enter_end - delay), 4),
                    "direction": "down",
                    "softness": 0.22,
                }
            )
        if is_cluster_member and layer_id in cluster_refs:
            recipe["transform_reference"] = cluster_refs[layer_id]
        layer["motion_recipe"] = recipe
        result.append(layer)
    if spec.get("wants_full_frame_drop"):
        result.append(
            _full_frame_drop_overlay_layer(
                prompt,
                motion_id,
                frame_width,
                frame_height,
                duration,
                outro_start,
                bool(spec["wants_fade_out"]),
                phase_plan,
            )
        )
    if spec["wants_shatter"]:
        result.extend(
            _frame_shatter_overlay_layers(
                prompt,
                frame_width,
                frame_height,
                duration,
                outro_start,
                bool(spec["wants_fade_out"]),
                phase_plan,
            )
        )
    return result


def _fallback_recipe(prompt: str, layer: dict[str, Any]) -> dict[str, Any]:
    text = prompt.casefold()
    full_span = _wants_full_span_motion(text)
    kind = str(layer.get("kind") or "layer")
    registry_effect = primary_supported_effect(prompt, scope="selected-layer", target=kind)
    registry_preset = registry_effect.preset if registry_effect and registry_effect.category != "exit" else ""
    registry_effect_id = str(registry_effect.id if registry_effect else "")
    direction = "center"
    if re.search(r"слева|left", text):
        direction = "left"
    elif re.search(r"справа|right", text):
        direction = "right"
    elif re.search(r"сверху|top|верх", text):
        direction = "top"
    elif re.search(r"снизу|bottom|низ", text):
        direction = "bottom"
    if re.search(r"from\s+below|bottom\s+to\s+top|position\s*y|rise\s+up", text) or registry_effect_id == "button-y-rise":
        direction = "bottom"

    wants_drop_raw = bool(re.search(r"drop|fall|gravity|physics|bounce|пада|паден|сверху|физичес|гравитац|ускорени|подскок|подпрыг|отскок", text))
    if registry_preset == "pop-in" and re.search(r"pop|spring|elastic|пружин|упруг", text):
        wants_drop_raw = False
    wants_drop = wants_drop_raw or registry_preset == "drop-bounce"
    wants_custom = bool(re.search(r"крут|вращ|поворот|rotate|spin|дрож|тряск|shake|jitter|wiggle|wobble|покач|маятник|болта|сложно|complex|траектор|path|спирал|spiral|zoom|camera|push\s*in|pull\s*back|pan", text)) or registry_preset == "custom-dsl"
    wants_fade = bool(re.search(r"fade\s*in|фейд|прояв|появ|прозрач", text)) or registry_preset == "fade-in"
    initially_hidden = bool(re.search(r"изначально|не\s*было|небыло|нету|невидим", text))
    wants_wipe = bool(re.search(r"wipe|reveal|mask|маск|штор|свайп", text)) or registry_preset == "wipe-reveal"
    wants_pulse = bool(re.search(r"pulse|пульс|glow|свеч|сиян|мига|акцент", text)) or registry_preset == "pulse-glow"
    wants_blur = bool(re.search(r"blur|блюр|размыт|дым|туман", text)) or registry_preset == "blur-fade"
    wants_parallax_depth = registry_effect_id in {"parallax-photo", "depth-card-in"}
    wants_button_y_rise = registry_effect_id == "button-y-rise"
    wants_float = bool(re.search(r"float|парени|плава|дых|drift|левит", text)) or (registry_preset == "premium-float" and not wants_parallax_depth)
    is_dynamic = bool(re.search(r"динами|быстро|энерг|резк|удар|pop|поп|bounce|ускорени", text)) or registry_preset == "pop-in"
    is_premium = bool(re.search(r"premium|преми|дорого|apple|cinematic|кино|мягк|soft|плавн", text))

    delay = _number_from_prompt(
        text,
        r"(?:через|after)\s+(\d+(?:[\.,]\d+)?|одну|один|одна|две|два|три|четыре|пять)\s*(?:сек|second|sec|s)",
    )
    explicit_duration = _number_from_prompt(
        text,
        r"(?:в\s*течени[еи]|за|duration|for)\s+(\d+(?:[\.,]\d+)?|одну|один|одна|две|два|три|четыре|пять)\s*(?:х\s*)?(?:сек|second|sec|s)",
    )
    duration = explicit_duration or (0.42 if is_dynamic else 0.72 if is_premium else 0.58)

    if wants_drop:
        preset = "drop-bounce"
    elif wants_custom:
        preset = "custom-dsl"
    elif wants_wipe:
        preset = "wipe-reveal"
    elif wants_pulse:
        preset = "pulse-glow"
    elif wants_blur:
        preset = "blur-fade"
    elif wants_button_y_rise:
        preset = "soft-slide"
    elif wants_parallax_depth:
        preset = "premium-float"
    elif wants_fade or initially_hidden:
        preset = "fade-in"
    elif wants_float:
        preset = "premium-float"
    elif is_dynamic:
        preset = "pop-in"
    elif registry_preset in RECIPE_LABELS:
        preset = registry_preset
    else:
        preset = "soft-slide"

    intro_type = "fade" if preset == "fade-in" else "drop" if preset == "drop-bounce" else "custom" if preset == "custom-dsl" else "pop" if preset == "pop-in" else "wipe" if preset == "wipe-reveal" else "slide"
    distance = 0 if intro_type in {"fade", "custom"} else 180 if intro_type == "drop" else 115 if is_dynamic else 78
    recipe = {
        "id": f"recipe-{int(time.time() * 1000):x}",
        "prompt": prompt,
        "preset": preset,
        "time_mode": "full-span" if full_span else "intro-outro",
        "label": RECIPE_LABELS.get(preset, preset),
        "tags": [
            kind,
            preset,
            "premium" if is_premium else "clean",
            "dynamic" if is_dynamic else "soft",
            f"effect:{registry_effect.id}" if registry_effect else "effect:default",
        ],
        "intro": {
            "type": intro_type,
            "direction": "center" if intro_type in {"fade", "custom"} else "top" if intro_type == "drop" else direction,
            "delay": delay,
            "duration": duration if explicit_duration else 0.95 if intro_type == "drop" else duration,
            "distance": distance,
            "ease": "smooth" if intro_type == "fade" else "gravity-bounce" if intro_type == "drop" else "power" if is_dynamic else "expo",
        },
        "hold": {
            "type": "pulse" if wants_pulse else "float" if wants_float or (is_premium and not wants_fade and not wants_drop) else "none",
            "amount": 0.055 if wants_pulse else 0.035 if wants_float else 0,
            "speed": 2.2 if is_dynamic else 1.15,
        },
        "outro": {"type": "blur-fade" if wants_blur else "fade", "duration": 0.38, "ease": "sine"}
        if re.search(r"уход|исчез|out|exit|fade out|пропад", text)
        else None,
    }
    if _has_exit_drop_intent(text):
        if re.search(r"fade\s*in|фейд\s*ин|появ|прояв", text):
            recipe["preset"] = "fade-in"
            recipe["label"] = RECIPE_LABELS["fade-in"]
            recipe["intro"] = {
                "type": "fade",
                "direction": "center",
                "delay": delay,
                "duration": duration if explicit_duration else 0.58,
                "distance": 0,
                "ease": "smooth",
            }
        recipe["outro"] = {"type": "drop", "direction": "bottom", "distance": 180, "duration": 0.7, "ease": "gravity"}
        recipe["tags"] = [kind, str(recipe.get("preset") or "custom"), "exit-drop", "dynamic"]
    elif _has_exit_fade_intent(text):
        recipe["outro"] = {"type": "fade", "duration": 0.38, "ease": "sine"}
    recipe["motion_dsl"] = _motion_dsl_from_recipe(recipe, prompt)
    return recipe


def _motion_dsl_from_recipe(recipe: dict[str, Any], prompt: str) -> dict[str, Any]:
    intro = dict(recipe.get("intro") or {})
    hold = dict(recipe.get("hold") or {})
    text = prompt.casefold()
    tags = {str(tag).casefold() for tag in recipe.get("tags", []) if isinstance(tag, str)}

    def has_effect(effect_id: str) -> bool:
        return f"effect:{effect_id}".casefold() in tags

    delay = max(0.0, float(intro.get("delay") or 0))
    duration = max(0.05, float(intro.get("duration") or 0.6))
    end = delay + duration
    distance = max(0.0, float(intro.get("distance") or 0))
    direction = str(intro.get("direction") or "center")
    reveal_direction = {"left": "right", "right": "left", "top": "down", "bottom": "up"}.get(direction, direction)
    dx = -distance if direction == "left" else distance if direction == "right" else 0
    dy = -distance if direction == "top" else distance if direction == "bottom" else 0
    initial_hidden = bool(re.search(r"изначально|не\s*было|небыло|нету|невидим|absent|hidden", text))
    wants_rotate = bool(re.search(r"крут|вращ|поворот|rotate|spin", text))
    wants_shake = has_effect("handheld") or bool(re.search(r"дрож|тряск|shake|jitter|hand\s*held|handheld", text))
    wants_pulse = bool(re.search(r"пульс|pulse|heartbeat", text))
    wants_wiggle = bool(re.search(r"wiggle|wobble|покач|маятник|болта", text))
    wants_blur = bool(re.search(r"blur|блюр|размыт", text))
    wants_appear = bool(re.search(r"появ|прояв|fade\s*in|appear", text))
    wants_parallax_depth = has_effect("parallax-photo") or has_effect("depth-card-in")
    wants_button_y_rise = has_effect("button-y-rise")
    wants_kinetic_type = has_effect("kinetic-type")
    wants_flip_card = has_effect("flip-card")
    wants_cascade = has_effect("cascade")
    wants_spiral = bool(re.search(r"спирал|spiral", text))
    wants_typewriter = has_effect("type-on") or has_effect("character-stagger") or bool(re.search(r"typewriter|type\s*on|typing|character\s*stagger|letter\s*by\s*letter", text))
    wants_line_reveal = has_effect("fade-up-lines") or has_effect("word-stagger") or bool(re.search(r"fade\s*up\s*lines|line\s*by\s*line|lines\s*fade|text\s*fade\s*up|word\s*by\s*word", text))
    wants_iris = has_effect("iris-reveal") or has_effect("radial-wipe") or bool(re.search(r"iris|circle\s*reveal|radial|clock\s*wipe", text))
    wants_luma = has_effect("luma-wipe") or bool(re.search(r"luma|luminance", text))
    wants_liquid = has_effect("liquid-wipe") or bool(re.search(r"liquid\s*wipe|fluid\s*reveal|water\s*wipe", text))
    wants_particle = has_effect("particle-dissolve") or bool(re.search(r"particle\s*dissolve|particles|dust|sand|dissolve", text))
    wants_smoke = has_effect("smoke-dissolve") or bool(re.search(r"smoke|mist|fog\s*dissolve", text))
    wants_paper = has_effect("paper-tear") or bool(re.search(r"paper\s*tear|torn\s*paper", text))
    wants_pixelate = has_effect("pixelate") or bool(re.search(r"pixelate|pixels", text))
    wants_glitch = has_effect("glitch") or bool(re.search(r"glitch|rgb\s*split|digital\s*glitch", text))
    wants_film_burn = has_effect("film-burn") or bool(re.search(r"film\s*burn|light\s*leak", text))
    wants_shimmer = has_effect("shimmer") or bool(re.search(r"shimmer|shine\s*sweep|light\s*sweep", text))
    wants_camera_push = has_effect("camera-push") or bool(re.search(r"camera\s*push|push\s*in|zoom\s*in", text))
    wants_camera_pull = has_effect("camera-pull") or bool(re.search(r"camera\s*pull|pull\s*back|zoom\s*out", text))
    wants_pan = has_effect("pan") or bool(re.search(r"camera\s*pan|\bpan\b", text))
    wants_wipe_effect = str(intro.get("type") or "") == "wipe" or wants_iris or wants_luma or wants_liquid or wants_paper

    base = {"x": 0, "y": 0, "scale": 1, "scaleX": 1, "scaleY": 1, "rotate": 0, "skewX": 0, "skewY": 0, "opacity": 1, "blur": 0, "brightness": 1}
    keyframes: list[dict[str, Any]] = [{**base, "time": 0, "opacity": 0 if initial_hidden or wants_appear else 1}]
    if delay > 0:
        keyframes.append({"time": delay, "opacity": 0 if initial_hidden else 1})

    intro_type = str(intro.get("type") or "")
    if wants_kinetic_type:
        keyframes[-1].update({"y": 8, "scale": 0.985, "opacity": 0})
        keyframes.extend(
            [
                {"time": delay + duration * 0.5, "y": -2, "scale": 1.01, "opacity": 1, "ease": "power"},
                {"time": end, "y": 0, "scale": 1, "opacity": 1, "ease": "smooth"},
            ]
        )
    elif wants_flip_card:
        keyframes[-1].update({"scaleX": 0.04, "scaleY": 0.98, "opacity": 0, "blur": 0.25})
        keyframes.extend(
            [
                {"time": delay + duration * 0.48, "scaleX": 1.08, "scaleY": 1.0, "opacity": 1, "blur": 0.15, "ease": "power"},
                {"time": end, "scaleX": 1, "scaleY": 1, "opacity": 1, "blur": 0, "ease": "smooth"},
            ]
        )
    elif wants_cascade:
        keyframes[-1].update({"y": -16 if "top" in text else 16, "opacity": 0})
        keyframes.extend(
            [
                {"time": delay + duration * 0.68, "y": -2 if "top" in text else 2, "opacity": 1, "ease": "power"},
                {"time": end, "y": 0, "opacity": 1, "ease": "smooth"},
            ]
        )
    elif wants_parallax_depth:
        parallax_x = -9 if "left" not in text else 9
        start_scale = 1.06 if has_effect("depth-card-in") else 1.04
        keyframes[-1].update({"x": parallax_x, "y": 5, "scale": start_scale, "opacity": 0, "blur": 0.2})
        keyframes.extend(
            [
                {"time": delay + duration * 0.46, "x": parallax_x * 0.35, "y": 2, "scale": 1.015, "opacity": 0.82, "ease": "sine"},
                {"time": end, "x": 0, "y": 0, "scale": 1, "opacity": 1, "blur": 0, "ease": "smooth"},
            ]
        )
    elif wants_button_y_rise:
        keyframes[-1].update({"x": 0, "y": distance or 48, "opacity": 0})
        keyframes.extend(
            [
                {"time": delay + duration * 0.72, "x": 0, "y": -2, "opacity": 1, "ease": "power"},
                {"time": end, "x": 0, "y": 0, "opacity": 1, "ease": "smooth"},
            ]
        )
    elif wants_spiral:
        keyframes[-1].update({"x": -70, "y": -45, "scale": 0.68, "rotate": -90, "opacity": 0 if wants_appear or initial_hidden else 1})
        keyframes.extend(
            [
                {"time": delay + duration * 0.38, "x": 45, "y": -35, "scale": 0.86, "rotate": 120, "opacity": 0.75, "ease": "sine"},
                {"time": delay + duration * 0.72, "x": -18, "y": 18, "scale": 1.03, "rotate": 285, "opacity": 1, "ease": "power"},
                {"time": end, "x": 0, "y": 0, "scale": 1, "rotate": 360, "opacity": 1, "ease": "smooth"},
            ]
        )
    elif intro_type == "fade":
        keyframes.append({"time": end, "opacity": 1, "ease": "smooth"})
    elif intro_type == "wipe" or wants_wipe_effect or wants_typewriter or wants_line_reveal:
        keyframes.append({"time": end, "opacity": 1, "ease": "smooth"})
    elif intro_type == "drop":
        keyframes[-1].update({"y": -distance, "opacity": 0 if initial_hidden else 1})
        keyframes.extend(
            [
                {"time": delay + duration * 0.72, "opacity": 1, "y": 8, "ease": "gravity"},
                {"time": delay + duration * 0.86, "y": -3, "ease": "sine"},
                {"time": end, "y": 0, "ease": "smooth"},
            ]
        )
    elif intro_type == "pop":
        keyframes[-1].update({"scale": 0.82, "opacity": 0 if initial_hidden else 1})
        keyframes.extend(
            [
                {"time": delay + duration * 0.7, "opacity": 1, "scale": 1.06, "ease": "power"},
                {"time": end, "scale": 1, "ease": "smooth"},
            ]
        )
    else:
        keyframes[-1].update({"x": dx, "y": dy, "opacity": 0 if initial_hidden else 1})
        keyframes.append({"time": end, "x": 0, "y": 0, "opacity": 1, "ease": str(intro.get("ease") or "expo")})

    if wants_rotate:
        keyframes[-1]["rotate"] = 360 if "против" not in text and "counter" not in text else -360
        if len(keyframes) > 1:
            keyframes[1]["rotate"] = 0
    if wants_blur:
        keyframes[0]["blur"] = 10
        keyframes[-1]["blur"] = 0
    if wants_camera_push:
        keyframes[0].setdefault("scale", 1)
        keyframes.append({"time": end, "scale": 1.12, "opacity": 1, "ease": "sine"})
    elif wants_camera_pull:
        keyframes[0]["scale"] = 1.12
        keyframes.append({"time": end, "scale": 1, "opacity": 1, "ease": "sine"})
    if wants_pan:
        pan_distance = 9 if "right" in text else -9 if "left" in text else 7
        keyframes[0]["x"] = -pan_distance
        keyframes.append({"time": end, "x": pan_distance, "opacity": 1, "ease": "sine"})

    effects: list[dict[str, Any]] = []
    if wants_shake:
        effects.append({"type": "shake", "start": delay, "duration": max(duration, 0.6), "amplitude": 4, "frequency": 18})
    if wants_pulse or hold.get("type") == "pulse":
        effects.append({"type": "pulse", "start": end, "duration": max(0.1, float(hold.get("duration") or 999)), "amplitude": float(hold.get("amount") or 0.045), "frequency": float(hold.get("speed") or 1.5)})
    if hold.get("type") == "float":
        effects.append({"type": "float", "start": end, "duration": 999, "amplitude": float(hold.get("amount") or 0.03) * 100, "frequency": float(hold.get("speed") or 1.15)})
    if wants_wiggle:
        effects.append({"type": "wiggle", "start": delay, "duration": max(duration, 1.0), "amplitude": 5, "frequency": 6})
    if wants_wipe_effect and not (wants_iris or wants_liquid):
        effects.append({"type": "wipe-reveal", "start": delay, "duration": duration, "direction": reveal_direction})
    if wants_iris:
        effects.append({"type": "iris-reveal", "start": delay, "duration": duration, "centerX": 0.5, "centerY": 0.5, "softness": 0.035})
    if wants_luma:
        effects.append({"type": "luma-wipe", "start": delay, "duration": duration, "direction": "out" if re.search(r"out|exit|disappear|fade\s*out", text) else "in"})
    if wants_liquid:
        effects.append({"type": "liquid-wipe", "start": delay, "duration": duration, "direction": reveal_direction if reveal_direction != "center" else "right", "amplitude": 0.055, "frequency": 2.1})
    if wants_kinetic_type:
        effects.append({"type": "line-reveal", "start": delay, "duration": duration, "lines": 5})
    if wants_typewriter:
        effects.append({"type": "typewriter", "start": delay, "duration": duration, "steps": 42, "direction": "right"})
    if wants_line_reveal:
        effects.append({"type": "line-reveal", "start": delay, "duration": duration, "lines": 5})
    if wants_particle:
        particle_direction = "out" if re.search(r"out|exit|disappear|dissolve\s*out|fade\s*out", text) else "in"
        effects.append({"type": "particle-dissolve", "start": delay, "duration": duration, "direction": particle_direction, "cells": 30, "seed": 173})
    if wants_smoke:
        smoke_direction = "out" if re.search(r"out|exit|disappear|dissolve\s*out|fade\s*out", text) else "in"
        effects.append({"type": "smoke-dissolve", "start": delay, "duration": duration, "direction": smoke_direction, "seed": 311})
    if wants_paper:
        paper_direction = "out" if re.search(r"out|exit|disappear|tear\s*out|rip\s*out", text) else (reveal_direction if reveal_direction != "center" else "right")
        effects.append({"type": "paper-tear", "start": delay, "duration": duration, "direction": paper_direction, "amplitude": 0.06, "seed": 421})
    if wants_pixelate:
        effects.append({"type": "pixelate", "start": delay, "duration": duration, "direction": "out" if re.search(r"out|exit|disappear|fade\s*out", text) else "in"})
    if wants_glitch:
        effects.append({"type": "glitch", "start": delay, "duration": max(duration, 0.45), "amplitude": 0.055, "seed": 509})
    if wants_film_burn:
        effects.append({"type": "film-burn", "start": delay, "duration": duration, "strength": 0.5})
    if wants_shimmer:
        effects.append({"type": "shimmer", "start": end, "duration": max(0.55, min(1.4, duration)), "strength": 0.24})

    return {"version": 1, "keyframes": keyframes, "effects": effects}


def _normalize_motion_dsl(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return fallback
    keyframes_in = value.get("keyframes")
    if not isinstance(keyframes_in, list) or not keyframes_in:
        return fallback
    allowed_props = {"time", "x", "y", "scale", "scaleX", "scaleY", "rotate", "skewX", "skewY", "opacity", "blur", "brightness", "ease"}
    keyframes: list[dict[str, Any]] = []
    for raw_frame in keyframes_in[:24]:
        if not isinstance(raw_frame, dict):
            continue
        frame: dict[str, Any] = {}
        for key, val in raw_frame.items():
            if key not in allowed_props:
                continue
            if key == "ease":
                frame[key] = str(val or "smooth")
            else:
                try:
                    frame[key] = float(val)
                except (TypeError, ValueError):
                    continue
        if "time" in frame:
            frame["time"] = max(0.0, min(60.0, float(frame["time"])))
            keyframes.append(frame)
    if not keyframes:
        return fallback
    keyframes = sorted(keyframes, key=lambda item: float(item.get("time") or 0))

    effects_in = value.get("effects")
    effects: list[dict[str, Any]] = []
    if isinstance(effects_in, list):
        for raw_effect in effects_in[:12]:
            if not isinstance(raw_effect, dict):
                continue
            effect_type = str(raw_effect.get("type") or "").casefold()
            if effect_type not in {
                "shake",
                "pulse",
                "float",
                "wiggle",
                "glow",
                "wipe-reveal",
                "venetian-blinds",
                "iris-reveal",
                "radial-wipe",
                "luma-wipe",
                "liquid-wipe",
                "typewriter",
                "type-on",
                "line-reveal",
                "fade-up-lines",
                "particle-dissolve",
                "smoke-dissolve",
                "paper-tear",
                "pixelate",
                "glitch",
                "film-burn",
                "shimmer",
            }:
                continue
            effect = {"type": effect_type}
            for key in ("start", "duration", "amplitude", "frequency"):
                try:
                    effect[key] = float(raw_effect.get(key, 0 if key == "start" else 1))
                except (TypeError, ValueError):
                    effect[key] = 0 if key == "start" else 1
            effect["start"] = max(0.0, min(60.0, effect["start"]))
            effect["duration"] = max(0.05, min(60.0, effect["duration"]))
            effect["amplitude"] = max(0.0, min(100.0, effect["amplitude"]))
            effect["frequency"] = max(0.05, min(60.0, effect["frequency"]))
            for key in ("direction", "orientation", "mode"):
                if key in raw_effect:
                    effect[key] = str(raw_effect.get(key) or "")
            for key in ("blades", "steps", "characters", "lines", "cells", "seed"):
                if key in raw_effect:
                    try:
                        effect[key] = max(0.0, min(240.0, float(raw_effect.get(key) or 0)))
                    except (TypeError, ValueError):
                        continue
            for key in ("centerX", "centerY", "cx", "cy", "softness", "band", "strength"):
                if key in raw_effect:
                    try:
                        effect[key] = max(0.0, min(1.0, float(raw_effect.get(key) or 0)))
                    except (TypeError, ValueError):
                        continue
            effects.append(effect)
    return {"version": 1, "keyframes": keyframes, "effects": effects}


def _normalize_recipe(recipe: dict[str, Any], prompt: str, layer: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_recipe(prompt, layer)
    preset = str(recipe.get("preset") or fallback["preset"])
    if fallback["preset"] in {"fade-in", "drop-bounce"}:
        preset = fallback["preset"]
    elif isinstance(recipe.get("motion_dsl"), dict) and recipe.get("motion_dsl", {}).get("keyframes"):
        preset = preset if preset in RECIPE_LABELS else "custom-dsl"
    if preset not in RECIPE_LABELS:
        preset = fallback["preset"]
    intro = dict(recipe.get("intro") or {})
    if preset == "fade-in":
        intro["type"] = "fade"
        intro["distance"] = 0
        intro["direction"] = "center"
        intro["delay"] = fallback["intro"]["delay"]
        intro["duration"] = fallback["intro"]["duration"]
        intro["ease"] = "smooth"
    if preset == "drop-bounce":
        intro["type"] = "drop"
        intro["direction"] = "top"
        intro["delay"] = fallback["intro"]["delay"]
        intro["duration"] = fallback["intro"]["duration"]
        intro["distance"] = fallback["intro"]["distance"]
        intro["ease"] = "gravity-bounce"
    recipe_outro = recipe.get("outro") if isinstance(recipe.get("outro"), dict) or recipe.get("outro") is None else fallback["outro"]
    if isinstance(fallback.get("outro"), dict) and fallback["outro"].get("type") == "drop":
        recipe_outro = fallback["outro"]
    dsl_source = fallback.get("motion_dsl") if fallback["preset"] in {"fade-in", "drop-bounce"} else recipe.get("motion_dsl")
    normalized = {
        "id": f"recipe-{int(time.time() * 1000):x}",
        "prompt": prompt,
        "preset": preset,
        "time_mode": "full-span" if _wants_full_span_motion(prompt.casefold()) else str(recipe.get("time_mode") or fallback.get("time_mode") or "intro-outro"),
        "label": str(fallback["label"] if fallback["preset"] == preset else recipe.get("label") or RECIPE_LABELS[preset]),
        "tags": fallback["tags"] if fallback["preset"] == preset else recipe.get("tags") if isinstance(recipe.get("tags"), list) else fallback["tags"],
        "intro": {
            "type": str(intro.get("type") or fallback["intro"]["type"]),
            "direction": str(intro.get("direction") or fallback["intro"]["direction"]),
            "delay": max(0.0, float(intro.get("delay", fallback["intro"]["delay"]) or 0)),
            "duration": max(0.05, float(intro.get("duration", fallback["intro"]["duration"]) or 0.5)),
            "distance": max(0.0, float(intro.get("distance", fallback["intro"]["distance"]) or 0)),
            "ease": str(intro.get("ease") or fallback["intro"]["ease"]),
        },
        "hold": recipe.get("hold") if isinstance(recipe.get("hold"), dict) else fallback["hold"],
        "outro": recipe_outro,
        "motion_dsl": _normalize_motion_dsl(dsl_source, fallback.get("motion_dsl") or _motion_dsl_from_recipe(fallback, prompt)),
    }
    return normalized


def plan_layer_motion(prompt: str, layer: dict[str, Any], sibling_layers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    fallback = _fallback_recipe(prompt, layer)
    return fallback
    payload = {
        "prompt": prompt,
        "target_layer": {
            "id": layer.get("id"),
            "name": layer.get("name"),
            "kind": layer.get("kind"),
            "node_type": layer.get("node_type"),
            "x": layer.get("x"),
            "y": layer.get("y"),
            "width": layer.get("width"),
            "height": layer.get("height"),
        },
        "nearby_layers": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "x": item.get("x"),
                "y": item.get("y"),
                "width": item.get("width"),
                "height": item.get("height"),
            }
            for item in (sibling_layers or [])[:24]
        ],
        "fallback_if_unclear": fallback,
    }
    try:
        response = chat_json(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), timeout=8)
        return _normalize_recipe(response, prompt, layer)
    except (OllamaError, ValueError, TypeError):
        return fallback
