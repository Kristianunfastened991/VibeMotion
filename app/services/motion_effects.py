from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


SUPPORTED_LAYER_PRESETS = {
    "fade-in",
    "soft-slide",
    "pop-in",
    "drop-bounce",
    "wipe-reveal",
    "premium-float",
    "pulse-glow",
    "blur-fade",
    "custom-dsl",
}

SUPPORTED_FRAME_PRESETS = {
    "white-bg-fade",
    "glitch-bg-fade",
    "signal-scan-reveal",
    "glass-light-sweep",
    "soft-pixel-snap",
    "venetian-blinds-bg",
    "random-fly-in-stagger",
    "advanced-composition-build",
    "parallax-photo",
    "fade-up-lines",
    "text-slide-up-lines",
    "button-y-rise",
    "tetris-build",
    "gravity-drop-fade",
    "full-frame-drop",
    "layer-scatter-fall",
    "full-frame-shatter",
    "full-frame-fade-out",
    "static-reveal",
    "scene-camera",
}


@dataclass(frozen=True)
class MotionEffectSpec:
    id: str
    label: str
    category: str
    scopes: tuple[str, ...]
    targets: tuple[str, ...]
    aliases: tuple[str, ...]
    preset: str
    status: str
    quality: str
    qa_checks: tuple[str, ...]
    fallback_chain: tuple[str, ...]
    params: tuple[str, ...] = ()
    notes: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _e(
    effect_id: str,
    label: str,
    category: str,
    scopes: tuple[str, ...],
    targets: tuple[str, ...],
    aliases: tuple[str, ...],
    preset: str,
    status: str,
    quality: str,
    qa_checks: tuple[str, ...],
    fallback_chain: tuple[str, ...] | None = None,
    params: tuple[str, ...] = (),
    notes: str = "",
) -> MotionEffectSpec:
    return MotionEffectSpec(
        id=effect_id,
        label=label,
        category=category,
        scopes=scopes,
        targets=targets,
        aliases=aliases,
        preset=preset,
        status=status,
        quality=quality,
        qa_checks=qa_checks,
        fallback_chain=fallback_chain or (preset,),
        params=params,
        notes=notes,
    )


EFFECTS: tuple[MotionEffectSpec, ...] = (
    _e("fade-in", "Fade in", "entrance", ("selected-layer", "whole-frame"), ("any",), ("fade in", "fade-in", "appear", "opacity in", "\u0444\u0435\u0439\u0434 \u0438\u043d", "\u043f\u043e\u044f\u0432", "\u043f\u0440\u043e\u044f\u0432"), "fade-in", "native", "stable", ("opacity_timing", "no_position_drift"), params=("duration", "delay", "easing")),
    _e("fade-out", "Fade out", "exit", ("selected-layer", "whole-frame"), ("any",), ("fade out", "fade-out", "disappear", "opacity out", "\u0444\u0435\u0439\u0434 \u0430\u0443\u0442", "\u0438\u0441\u0447\u0435\u0437", "\u0443\u0445\u043e\u0434\u0438\u0442"), "blur-fade", "native", "stable", ("opacity_timing", "no_position_drift"), fallback_chain=("full-frame-fade-out", "blur-fade")),
    _e("soft-slide-in", "Soft slide in", "entrance", ("selected-layer", "whole-frame"), ("any",), ("soft slide", "slide in", "move in", "\u043f\u043b\u0430\u0432\u043d\u043e \u0432\u044a\u0435\u0437\u0436", "\u0441\u043b\u0430\u0439\u0434"), "soft-slide", "native", "stable", ("position_timing", "settled_pixel_match"), params=("direction", "distance", "duration")),
    _e("slide-up", "Slide up", "entrance", ("selected-layer", "whole-frame"), ("any",), ("slide up", "rise up", "from below", "bottom to top", "\u0441\u043d\u0438\u0437\u0443 \u0432\u0432\u0435\u0440\u0445", "\u043f\u043e\u0434\u043d\u0438\u043c\u0430"), "soft-slide", "native", "stable", ("position_timing", "settled_pixel_match"), fallback_chain=("button-y-rise", "soft-slide"), params=("distance", "duration")),
    _e("slide-down", "Slide down", "entrance", ("selected-layer", "whole-frame"), ("any",), ("slide down", "from top", "top to bottom", "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437", "\u043e\u043f\u0443\u0441\u043a"), "soft-slide", "native", "stable", ("position_timing", "settled_pixel_match"), params=("distance", "duration")),
    _e("slide-left", "Slide left", "entrance", ("selected-layer",), ("any",), ("slide left", "from right", "\u0441\u043f\u0440\u0430\u0432\u0430", "\u0432\u043b\u0435\u0432\u043e"), "soft-slide", "native", "stable", ("position_timing", "settled_pixel_match"), params=("distance", "duration")),
    _e("slide-right", "Slide right", "entrance", ("selected-layer",), ("any",), ("slide right", "from left", "\u0441\u043b\u0435\u0432\u0430", "\u0432\u043f\u0440\u0430\u0432\u043e"), "soft-slide", "native", "stable", ("position_timing", "settled_pixel_match"), params=("distance", "duration")),
    _e("pop-in", "Pop in", "entrance", ("selected-layer", "whole-frame"), ("shape", "image", "button", "badge", "any"), ("pop", "pop in", "scale pop", "\u043f\u043e\u043f", "\u043f\u0440\u044b\u0436\u043e\u043a"), "pop-in", "native", "stable", ("scale_overshoot", "settled_pixel_match"), params=("overshoot", "duration")),
    _e("elastic-pop", "Elastic pop", "entrance", ("selected-layer",), ("shape", "button", "badge", "image"), ("pop spring bounce", "spring bounce", "elastic", "spring pop", "bouncy pop", "\u0443\u043f\u0440\u0443\u0433", "\u043f\u0440\u0443\u0436\u0438\u043d"), "pop-in", "native", "stable", ("scale_overshoot", "no_layout_shift"), fallback_chain=("pop-in",)),
    _e("drop-bounce", "Drop bounce", "entrance", ("selected-layer",), ("any",), ("drop in", "fall in", "bounce in", "gravity in", "\u043f\u0430\u0434\u0430\u0435\u0442", "\u043f\u0430\u0434\u0435\u043d\u0438\u0435", "\u0433\u0440\u0430\u0432\u0438\u0442"), "drop-bounce", "native", "stable", ("gravity_curve", "settled_pixel_match"), params=("height", "bounce", "duration")),
    _e("wipe-reveal", "Wipe reveal", "reveal", ("selected-layer", "whole-frame"), ("any",), ("wipe", "wipe reveal", "linear reveal", "\u0441\u0432\u0430\u0439\u043f", "\u0448\u0442\u043e\u0440\u043a", "\u043c\u0430\u0441\u043a"), "wipe-reveal", "native", "stable", ("mask_bounds", "no_extra_pixels"), params=("direction", "softness", "duration")),
    _e("mask-reveal", "Mask reveal", "reveal", ("selected-layer", "whole-frame"), ("image", "shape", "frame"), ("mask reveal", "clip reveal", "masked reveal", "\u043c\u0430\u0441\u043a\u0430", "\u043e\u0431\u0440\u0435\u0437\u043a\u0430"), "wipe-reveal", "native", "stable", ("mask_bounds", "no_extra_pixels"), fallback_chain=("wipe-reveal",)),
    _e("blur-fade", "Blur fade", "entrance", ("selected-layer", "whole-frame"), ("any",), ("blur fade", "blur in", "soft blur", "\u0431\u043b\u044e\u0440", "\u0440\u0430\u0437\u043c\u044b\u0442"), "blur-fade", "native", "stable", ("blur_timing", "settled_sharpness"), params=("blur", "duration")),
    _e("premium-float", "Premium float", "accent", ("selected-layer", "whole-frame"), ("image", "card", "shape", "any"), ("float", "floating", "drift", "premium float", "\u043f\u043b\u0430\u0432\u0430", "\u043f\u0430\u0440\u0438\u0442"), "premium-float", "native", "stable", ("bounded_motion", "no_pixel_bleed"), params=("amplitude", "speed")),
    _e("pulse-glow", "Pulse glow", "accent", ("selected-layer",), ("button", "badge", "shape", "text", "any"), ("pulse", "glow", "pulse glow", "breathe glow", "\u043f\u0443\u043b\u044c\u0441", "\u0441\u0432\u0435\u0447", "\u0441\u0438\u044f\u043d"), "pulse-glow", "native", "stable", ("bounded_opacity", "no_geometry_drift"), params=("amplitude", "frequency")),
    _e("shake", "Shake", "accent", ("selected-layer",), ("any",), ("shake", "jitter", "tremble", "\u0442\u0440\u044f\u0441", "\u0434\u0440\u043e\u0436"), "custom-dsl", "native", "stable", ("bounded_motion", "settled_pixel_match"), params=("amplitude", "frequency", "duration")),
    _e("wiggle", "Wiggle", "accent", ("selected-layer",), ("any",), ("wiggle", "wobble", "\u043f\u043e\u043a\u0430\u0447", "\u0432\u043e\u0431\u043b"), "custom-dsl", "native", "stable", ("bounded_motion", "settled_pixel_match"), params=("amplitude", "frequency", "duration")),
    _e("rotate-in", "Rotate in", "entrance", ("selected-layer",), ("shape", "image", "badge", "any"), ("rotate in", "spin in", "\u0432\u0440\u0430\u0449", "\u043a\u0440\u0443\u0442", "\u043f\u043e\u0432\u043e\u0440\u043e\u0442"), "custom-dsl", "native", "stable", ("rotation_timing", "settled_pixel_match"), params=("degrees", "duration")),
    _e("spiral-in", "Spiral in", "entrance", ("selected-layer",), ("image", "shape", "badge", "any"), ("spiral", "spiral in", "\u0441\u043f\u0438\u0440\u0430\u043b"), "custom-dsl", "native", "stable", ("path_bounds", "settled_pixel_match"), params=("radius", "turns", "duration")),
    _e("type-on", "Type on", "text", ("selected-layer", "whole-frame"), ("text",), ("type on", "typewriter", "typing", "\u043f\u0435\u0447\u0430\u0442", "\u0442\u0430\u0439\u043f"), "fade-in", "native", "stable", ("text_fidelity", "timing"), fallback_chain=("fade-up-lines", "fade-in"), notes="Rendered as a raster alpha reveal, so text layout is never recalculated."),
    _e("fade-up-lines", "Fade up lines", "text", ("whole-frame", "selected-layer"), ("text",), ("fade up lines", "line by line", "lines fade up", "text fade up", "\u0441\u0442\u0440\u043e\u043a\u0438", "\u0441\u0442\u0440\u043e\u043a\u0438 \u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437"), "fade-in", "native", "stable", ("line_order", "text_fidelity"), fallback_chain=("fade-up-lines", "fade-in")),
    _e("text-slide-up-lines", "Text slide up lines", "text", ("whole-frame", "selected-layer"), ("text",), ("text slide up", "text rises from below", "lines from bottom", "\u0442\u0435\u043a\u0441\u0442 \u0441\u043d\u0438\u0437\u0443", "\u0432\u044b\u043f\u043b\u044b\u0432\u0430\u0435\u0442 \u0441\u043d\u0438\u0437\u0443", "\u0441\u043d\u0438\u0437\u0443 \u0444\u0440\u0435\u0439\u043c\u0430"), "text-slide-up-lines", "native", "stable", ("line_order", "text_fidelity"), fallback_chain=("text-slide-up-lines", "fade-up-lines", "fade-in")),
    _e("word-stagger", "Word stagger", "text", ("selected-layer", "whole-frame"), ("text",), ("word stagger", "word by word", "\u043f\u043e \u0441\u043b\u043e\u0432\u0430\u043c"), "fade-in", "native", "stable", ("text_fidelity", "timing"), fallback_chain=("fade-up-lines", "fade-in")),
    _e("character-stagger", "Character stagger", "text", ("selected-layer", "whole-frame"), ("text",), ("character stagger", "letter by letter", "\u043f\u043e \u0431\u0443\u043a\u0432\u0430\u043c"), "fade-in", "native", "stable", ("text_fidelity", "timing"), fallback_chain=("type-on", "fade-up-lines", "fade-in")),
    _e("kinetic-type", "Kinetic type", "text", ("selected-layer", "whole-frame"), ("text",), ("kinetic type", "kinetic typography", "\u043a\u0438\u043d\u0435\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0442\u0435\u043a\u0441\u0442"), "custom-dsl", "native", "stable", ("text_fidelity", "bounded_motion"), fallback_chain=("fade-up-lines", "custom-dsl")),
    _e("button-y-rise", "Button Y rise", "entrance", ("whole-frame", "selected-layer"), ("button", "badge", "shape"), ("button rise", "cta rise", "position y", "y rise", "\u043a\u043d\u043e\u043f\u043a\u0430 \u0441\u043d\u0438\u0437\u0443", "\u043a\u043d\u043e\u043f\u043a\u0430 \u043f\u043e y"), "soft-slide", "native", "stable", ("position_timing", "button_cluster_fidelity"), fallback_chain=("button-y-rise", "soft-slide")),
    _e("parallax-photo", "Parallax photo reveal", "image", ("whole-frame", "selected-layer"), ("image", "photo"), ("parallax", "depth parallax", "photo parallax", "\u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441"), "premium-float", "native", "stable", ("crop_fidelity", "settled_pixel_match"), fallback_chain=("parallax-photo", "premium-float")),
    _e("depth-card-in", "Depth card in", "image", ("selected-layer", "whole-frame"), ("image", "card"), ("depth card", "depth plane", "z depth", "3d card"), "premium-float", "native", "stable", ("crop_fidelity", "bounded_scale"), fallback_chain=("parallax-photo", "premium-float")),
    _e("flip-card", "Flip card", "entrance", ("selected-layer",), ("card", "image", "shape"), ("flip", "flip card", "card flip", "\u0444\u043b\u0438\u043f", "\u043f\u0435\u0440\u0435\u0432\u043e\u0440\u043e\u0442"), "custom-dsl", "native", "stable", ("settled_pixel_match", "no_backface_artifact"), fallback_chain=("pop-in", "custom-dsl")),
    _e("staggered-fly-in", "Staggered fly in", "composition", ("whole-frame",), ("frame", "group"), ("staggered fly in", "fly into frame", "random order", "stagger", "\u0432\u043b\u0435\u0442", "\u0441\u043b\u0443\u0447\u0430\u0439\u043d"), "random-fly-in-stagger", "native", "stable", ("layer_order", "settled_pixel_match"), params=("duration", "order", "distance")),
    _e("cascade", "Cascade", "composition", ("whole-frame", "selected-layer"), ("group", "text", "frame"), ("cascade", "waterfall", "top down sequence", "\u043a\u0430\u0441\u043a\u0430\u0434", "\u043a\u0430\u0441\u043a\u0430\u0434 \u0441\u0432\u0435\u0440\u0445\u0443"), "custom-dsl", "native", "stable", ("order_timing", "settled_pixel_match"), fallback_chain=("advanced-composition-build", "random-fly-in-stagger", "custom-dsl")),
    _e("tetris-build", "Tetris build", "composition", ("whole-frame",), ("frame", "group"), ("tetris", "tetris blocks", "block stack", "\u0442\u0435\u0442\u0440\u0438\u0441", "\u043a\u0430\u043a \u0432 \u0438\u0433\u0440\u0435 \u0442\u0435\u0442\u0440\u0438\u0441"), "tetris-build", "native", "stable", ("order_timing", "settled_pixel_match"), fallback_chain=("tetris-build", "advanced-composition-build")),
    _e("venetian-blinds", "Venetian blinds", "reveal", ("whole-frame",), ("background", "frame"), ("venetian", "venetian blinds", "blinds", "\u0436\u0430\u043b\u044e\u0437", "\u0432\u0435\u0440\u0442\u0438\u043a\u0430\u043b\u044c\u043d\u044b\u0435 \u043f\u043e\u043b\u043e\u0441\u044b"), "venetian-blinds-bg", "native", "stable", ("stripe_signal", "timing", "background_only"), params=("blades", "orientation", "duration")),
    _e("horizontal-blinds", "Horizontal blinds", "reveal", ("whole-frame",), ("background", "frame"), ("horizontal blinds", "horizontal venetian", "\u0433\u043e\u0440\u0438\u0437\u043e\u043d\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u0436\u0430\u043b\u044e\u0437\u0438"), "venetian-blinds-bg", "native", "stable", ("stripe_signal", "timing"), fallback_chain=("venetian-blinds-bg",)),
    _e("iris-reveal", "Iris reveal", "reveal", ("whole-frame", "selected-layer"), ("image", "frame", "shape"), ("iris", "circle reveal", "radial reveal", "\u043a\u0440\u0443\u0433\u043e\u0432\u043e\u0435 \u043f\u043e\u044f\u0432"), "wipe-reveal", "native", "stable", ("mask_bounds", "settled_pixel_match"), fallback_chain=("wipe-reveal",)),
    _e("radial-wipe", "Radial wipe", "reveal", ("whole-frame", "selected-layer"), ("frame", "image", "shape"), ("radial wipe", "clock wipe", "\u0440\u0430\u0434\u0438\u0430\u043b", "\u0447\u0430\u0441\u043e\u0432\u0430\u044f \u0441\u0442\u0440\u0435\u043b\u043a\u0430"), "wipe-reveal", "native", "stable", ("mask_bounds", "settled_pixel_match"), fallback_chain=("iris-reveal", "wipe-reveal")),
    _e("luma-wipe", "Luma wipe", "transition", ("whole-frame", "selected-layer"), ("frame", "image", "shape", "any"), ("luma wipe", "luminance wipe", "\u043b\u044e\u043c\u0430"), "wipe-reveal", "native", "stable", ("no_color_shift", "settled_pixel_match"), fallback_chain=("wipe-reveal",)),
    _e("liquid-wipe", "Liquid wipe", "transition", ("whole-frame", "selected-layer"), ("frame", "image", "shape", "any"), ("liquid wipe", "water wipe", "fluid reveal", "\u0436\u0438\u0434\u043a", "\u0432\u043e\u0434\u0430"), "wipe-reveal", "native", "stable", ("mask_bounds", "no_pixel_bleed"), fallback_chain=("wipe-reveal",)),
    _e("signal-scan-reveal", "Signal scan reveal", "transition", ("whole-frame",), ("background", "frame"), ("signal scan", "digital scan", "clean glitch", "modern glitch", "scan reveal", "\u0430\u043a\u043a\u0443\u0440\u0430\u0442\u043d\u044b\u0439 \u0433\u043b\u0438\u0442\u0447", "\u0446\u0438\u0444\u0440\u043e\u0432\u043e\u0439 \u0441\u043a\u0430\u043d", "\u0441\u043e\u0432\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0439 \u0433\u043b\u0438\u0442\u0447"), "signal-scan-reveal", "native", "stable", ("no_long_artifacts", "settled_pixel_match"), fallback_chain=("signal-scan-reveal", "glitch-bg-fade")),
    _e("glass-light-sweep", "Glass light sweep", "transition", ("whole-frame",), ("background", "frame"), ("glass sweep", "light sweep", "premium shine", "shimmer reveal", "\u0441\u0442\u0435\u043a\u043b\u044f\u043d\u043d\u044b\u0439 \u0431\u043b\u0438\u043a", "\u0441\u0432\u0435\u0442\u043e\u0432\u043e\u0439 \u0441\u0432\u0438\u043f", "\u0431\u043b\u0438\u043a"), "glass-light-sweep", "native", "stable", ("bounded_brightness", "settled_pixel_match"), fallback_chain=("glass-light-sweep", "white-bg-fade")),
    _e("soft-pixel-snap", "Soft pixel snap", "transition", ("whole-frame",), ("background", "frame"), ("pixel snap", "pixel reveal", "soft pixels", "pixelated reveal", "\u043f\u0438\u043a\u0441\u0435\u043b\u044c\u043d\u043e\u0435 \u043f\u043e\u044f\u0432\u043b\u0435\u043d\u0438\u0435", "\u043f\u0438\u043a\u0441\u0435\u043b\u0438", "\u043f\u0438\u043a\u0441\u0435\u043b\u044c"), "soft-pixel-snap", "native", "stable", ("settled_sharpness", "timing"), fallback_chain=("soft-pixel-snap", "white-bg-fade")),
    _e("glitch", "Glitch", "transition", ("selected-layer", "whole-frame"), ("any",), ("glitch", "rgb split", "digital glitch", "\u0433\u043b\u0438\u0442\u0447"), "custom-dsl", "native", "risky", ("no_long_artifacts", "settled_pixel_match"), fallback_chain=("signal-scan-reveal", "shake", "custom-dsl"), notes="RGB split is kept only for explicit harsh glitch requests; whole-frame glitch defaults to Signal scan reveal."),
    _e("pixelate", "Pixelate", "transition", ("selected-layer", "whole-frame"), ("any",), ("pixelate", "pixels", "\u043f\u0438\u043a\u0441\u0435\u043b"), "blur-fade", "native", "stable", ("settled_sharpness", "timing"), fallback_chain=("blur-fade",)),
    _e("film-burn", "Film burn", "transition", ("whole-frame", "selected-layer"), ("frame", "image", "any"), ("film burn", "light leak", "\u0437\u0430\u0441\u0432\u0435\u0442", "\u043f\u043b\u0435\u043d\u043a"), "blur-fade", "native", "stable", ("no_unwanted_color_shift", "timing"), fallback_chain=("blur-fade", "fade-in")),
    _e("shimmer", "Shimmer", "accent", ("selected-layer",), ("button", "badge", "text", "shape"), ("shimmer", "shine sweep", "\u0448\u0438\u043c\u043c\u0435\u0440", "\u043b\u0435\u0433\u043a\u043e\u0435 \u0441\u0438\u044f\u043d\u0438\u0435"), "pulse-glow", "native", "stable", ("bounded_brightness", "no_layout_shift"), fallback_chain=("pulse-glow",)),
    _e("underline-draw", "Underline draw", "text", ("selected-layer", "whole-frame"), ("text", "shape"), ("underline draw", "draw underline", "\u043f\u043e\u0434\u0447\u0435\u0440\u043a\u043d\u0443\u0442\u044c", "\u043b\u0438\u043d\u0438\u044f"), "wipe-reveal", "native", "stable", ("line_bounds", "timing"), fallback_chain=("wipe-reveal",)),
    _e("arrow-draw", "Arrow draw", "shape", ("selected-layer", "whole-frame"), ("shape",), ("arrow draw", "draw arrow", "\u0441\u0442\u0440\u0435\u043b\u043a"), "wipe-reveal", "native", "stable", ("shape_bounds", "timing"), fallback_chain=("wipe-reveal",)),
    _e("camera-push", "Camera push", "camera", ("whole-frame",), ("frame",), ("camera push", "push in", "zoom in", "camera zoom", "\u043d\u0430\u0435\u0437\u0434", "\u0437\u0443\u043c"), "advanced-composition-build", "native", "stable", ("frame_bounds", "settled_pixel_match"), fallback_chain=("advanced-composition-build",), notes="Rendered after the Figma composition is assembled, as a unified scene camera."),
    _e("camera-pull", "Camera pull", "camera", ("whole-frame",), ("frame",), ("camera pull", "pull back", "zoom out", "\u043e\u0442\u044a\u0435\u0437\u0434", "\u043e\u0442\u0434\u0430\u043b"), "advanced-composition-build", "native", "stable", ("frame_bounds", "settled_pixel_match"), fallback_chain=("advanced-composition-build",), notes="Rendered after the Figma composition is assembled, as a unified scene camera."),
    _e("pan", "Pan", "camera", ("whole-frame",), ("frame",), ("pan", "camera pan", "\u043f\u0430\u043d\u043e\u0440\u0430\u043c"), "advanced-composition-build", "native", "stable", ("frame_bounds", "no_black_edges"), fallback_chain=("advanced-composition-build",), notes="Uses camera overscan to avoid black edges."),
    _e("handheld", "Handheld", "camera", ("whole-frame", "selected-layer"), ("frame", "image"), ("handheld", "hand held", "camera shake", "\u0440\u0443\u0447\u043d\u0430\u044f \u043a\u0430\u043c\u0435\u0440\u0430"), "custom-dsl", "native", "risky", ("bounded_motion", "no_black_edges"), fallback_chain=("scene-camera", "shake", "custom-dsl")),
    _e("gravity-drop-fade", "Gravity drop fade", "exit", ("selected-layer", "whole-frame"), ("any",), ("gravity drop", "fall down", "drop down", "fall like stone", "\u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437", "\u043a\u0430\u043a \u043a\u0430\u043c\u0435\u043d\u044c"), "drop-bounce", "native", "stable", ("gravity_curve", "outro_timing"), fallback_chain=("gravity-drop-fade", "drop-bounce")),
    _e("full-frame-drop", "Full-frame drop", "exit", ("whole-frame",), ("frame",), ("whole frame falls", "entire frame falls", "picture falls as one", "\u0432\u0441\u044f \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0430 \u043f\u0430\u0434\u0430\u0435\u0442", "\u043f\u0430\u0434\u0430\u0435\u0442 \u0446\u0435\u043b\u0438\u043a\u043e\u043c", "\u0446\u0435\u043b\u0438\u043a\u043e\u043c \u0432\u043d\u0438\u0437"), "full-frame-drop", "native", "stable", ("unified_frame_motion", "outro_timing"), fallback_chain=("full-frame-drop", "gravity-drop-fade")),
    _e("layer-scatter-fall", "Layer scatter fall", "exit", ("whole-frame",), ("frame", "group"), ("scatter", "scatter fall", "falling pieces", "\u0440\u0430\u0441\u0441\u044b\u043f", "\u043e\u043f\u0430\u0434", "\u0447\u0430\u0441\u0442\u0438"), "layer-scatter-fall", "native", "stable", ("separate_layers", "gravity_curve", "outro_timing"), params=("spread", "gravity", "duration")),
    _e("glass-shatter", "Glass shatter", "exit", ("whole-frame",), ("frame",), ("shatter", "broken glass", "glass shards", "\u0441\u0442\u0435\u043a\u043b", "\u043e\u0441\u043a\u043e\u043b", "\u0440\u0430\u0437\u0431\u0438\u0432"), "full-frame-shatter", "native", "stable", ("shard_motion", "outro_timing", "fade_completion"), params=("shards", "spread", "duration")),
    _e("particle-dissolve", "Particle dissolve", "exit", ("whole-frame",), ("any",), ("particles", "particle dissolve", "dust", "sand", "\u043f\u044b\u043b", "\u043f\u0435\u0441\u043e\u043a", "\u0447\u0430\u0441\u0442\u0438\u0446"), "blur-fade", "native", "stable", ("outro_timing", "no_residual_artifacts"), fallback_chain=("layer-scatter-fall", "blur-fade"), notes="Selected-layer particle dissolve is disabled until a real particle compositor exists."),
    _e("smoke-dissolve", "Smoke dissolve", "exit", ("whole-frame",), ("any",), ("smoke", "mist", "fog dissolve", "\u0434\u044b\u043c", "\u0442\u0443\u043c\u0430\u043d"), "blur-fade", "native", "stable", ("outro_timing", "no_residual_artifacts"), fallback_chain=("particle-dissolve", "blur-fade"), notes="Selected-layer smoke dissolve is disabled until a real smoke compositor exists."),
    _e("paper-tear", "Paper tear", "transition", (), ("frame", "image", "any"), ("paper tear", "torn paper", "\u0431\u0443\u043c\u0430\u0433\u0430", "\u0440\u0430\u0437\u0440\u044b\u0432"), "full-frame-shatter", "native", "stable", ("outro_timing", "no_masks_visible"), fallback_chain=("full-frame-shatter",), notes="Disabled from prompt suggestions until a reliable paper-tear compositor exists."),
)

EFFECTS_BY_ID = {effect.id: effect for effect in EFFECTS}


def registry_stats() -> dict[str, int]:
    result = {"total": len(EFFECTS), "native": 0, "fallback": 0, "stable": 0, "risky": 0}
    for effect in EFFECTS:
        result[effect.status] = result.get(effect.status, 0) + 1
        result[effect.quality] = result.get(effect.quality, 0) + 1
    return result


def all_effects() -> tuple[MotionEffectSpec, ...]:
    return EFFECTS


def effect_prompt_reference(limit: int = 48) -> str:
    lines = []
    for effect in EFFECTS[:limit]:
        alias_preview = ", ".join(effect.aliases[:4])
        lines.append(f"- {effect.id}: aliases [{alias_preview}], use preset {effect.preset}, status {effect.status}")
    return "\n".join(lines)


def _text_variants(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold().replace(",", ".")).strip()


def _alias_score(text: str, alias: str) -> int:
    alias_text = _text_variants(alias)
    if not alias_text:
        return 0
    if alias_text in text:
        return 100 + min(60, len(alias_text))
    words = [word for word in re.split(r"\W+", alias_text) if len(word) > 2]
    if words and all(word in text for word in words):
        return 50 + len(words) * 5
    return 0


def resolve_effects(prompt: str, scope: str | None = None, target: str | None = None, limit: int = 8) -> list[MotionEffectSpec]:
    text = _text_variants(prompt)
    scored: list[tuple[int, MotionEffectSpec]] = []
    for effect in EFFECTS:
        if scope and scope not in effect.scopes:
            continue
        if target and "any" not in effect.targets and target not in effect.targets:
            continue
        score = max((_alias_score(text, alias) for alias in effect.aliases), default=0)
        effect_name = effect.id.replace("-", " ")
        if effect.id in text or effect_name in text:
            score += 45
        if effect.id == "handheld" and re.search(r"hand\s*held|handheld|\u0440\u0443\u0447\u043d\w*\s+\u043a\u0430\u043c\u0435\u0440", text):
            score += 80
        if effect.id == "button-y-rise" and re.search(
            r"(button|cta|\u043a\u043d\u043e\u043f\w*)[\s\S]{0,80}(position\s*y|from\s+below|bottom\s+to\s+top|rise|rises|\u0441\u043d\u0438\u0437\u0443\s+\u0432\u0432\u0435\u0440\u0445)",
            text,
        ):
            score += 90
        if score:
            if effect.status == "native":
                score += 12
            if effect.quality == "stable":
                score += 8
            scored.append((score, effect))
    scored.sort(key=lambda item: (item[0], item[1].status == "native", item[1].quality == "stable", item[1].id), reverse=True)
    return [effect for _, effect in scored[:limit]]


def primary_supported_effect(prompt: str, scope: str = "selected-layer", target: str | None = None) -> MotionEffectSpec | None:
    for effect in resolve_effects(prompt, scope=scope, target=target, limit=12):
        if scope == "whole-frame" and effect.preset in SUPPORTED_FRAME_PRESETS:
            return effect
        if scope == "selected-layer" and effect.preset in SUPPORTED_LAYER_PRESETS:
            return effect
        for fallback in effect.fallback_chain:
            if scope == "whole-frame" and fallback in SUPPORTED_FRAME_PRESETS:
                return EFFECTS_BY_ID.get(fallback, effect)
            if scope == "selected-layer" and fallback in SUPPORTED_LAYER_PRESETS:
                return effect
    return None


EFFECT_PROMPT_REFERENCE = effect_prompt_reference()
