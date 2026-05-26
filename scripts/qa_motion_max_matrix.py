from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.figma_plugin import motion_from_plugin_asset  # noqa: E402
from app.services.layer_motion import _fallback_recipe, describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt  # noqa: E402
from app.services.motion import render_motion_video_asset  # noqa: E402
from app.services.motion_effects import all_effects, primary_supported_effect, resolve_effects  # noqa: E402
from app.services.motion_intent import attach_frame_motion_contract, build_layer_motion_recipe_from_prompt, motion_recipe_actions  # noqa: E402
from scripts.qa_motion_20prompts_wave import PROMPTS as WAVE_PROMPTS, camera_layer_issues, order_issues  # noqa: E402
from scripts.qa_motion_5prompts_10frames import (  # noqa: E402
    assert_phase_contract,
    assert_text_integrity,
    extract_frame,
    font,
    phase_map,
    source_texts,
    strict_gradient_issues,
    text_transform_issues,
)


TARGET_KIND_BY_REGISTRY = {
    "any": "image",
    "badge": "shape",
    "button": "shape",
    "card": "image",
    "frame": "image",
    "group": "image",
}

EFFECT_PROMPT_OVERRIDES = {
    "fade-in": "Fade in this layer for 1 second. Preserve the layer design exactly.",
    "fade-out": "Fade out this layer for 1 second. Preserve the layer design exactly before it exits.",
    "drop-bounce": "Drop in this layer with gravity bounce for 1 second.",
    "gravity-drop-fade": "At the end this layer falls down like a stone and fades out over 1 second.",
    "type-on": "Typewriter reveal this text for 1 second without changing the words.",
    "fade-up-lines": "Reveal this text with fade up lines from top to bottom for 1 second without changing the words.",
    "text-slide-up-lines": "Make this text rise from below line by line for 1 second without changing the words.",
    "kinetic-type": "Use kinetic typography on this text for 1 second, keep the words exactly the same.",
    "button-y-rise": "Button rises from below on position Y with light fade in for 1 second.",
    "parallax-photo": "Photo appears through depth parallax for 1 second and settles exactly.",
    "depth-card-in": "Depth card in for 1 second and settle exactly.",
    "flip-card": "Flip card in from the center for 1 second.",
    "staggered-fly-in": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds in random order. Final 0.5 seconds full-frame fade out.",
    "cascade": "First 0.5 seconds white background only. Then all layers cascade from top to bottom over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "tetris-build": "First 0.5 seconds white background only. Then all layers build like tetris blocks over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "venetian-blinds": "First 1 second the white background appears with venetian blinds. Then all layers fade in over 1 second. Final 0.5 seconds full-frame fade out.",
    "horizontal-blinds": "First 1 second the white background appears with horizontal venetian blinds. Then all layers fade in over 1 second. Final 0.5 seconds full-frame fade out.",
    "signal-scan-reveal": "First 1 second the white background appears with a clean signal scan reveal. Then all layers fade in over 1 second. Final 0.5 seconds full-frame fade out.",
    "glass-light-sweep": "First 1 second the white background appears with a glass light sweep. Then all layers fade in over 1 second. Final 0.5 seconds full-frame fade out.",
    "soft-pixel-snap": "First 1 second the white background appears with pixel snap. Then all layers fade in over 1 second. Final 0.5 seconds full-frame fade out.",
    "camera-push": "Add a slow camera push in over 3 seconds. Keep all layers exactly as designed, do not animate separate layers.",
    "camera-pull": "Add a slow whole-frame camera pull back over 3 seconds. Do not animate layers separately, preserve all text exactly.",
    "pan": "Add a slow whole-frame camera pan to the right over 3 seconds. Keep the layer design unchanged and do not animate individual layers.",
    "handheld": "Add a subtle whole-frame handheld camera motion for 3 seconds. Keep the layer design unchanged and do not animate individual layers.",
    "full-frame-drop": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the entire frame drops down as one whole object with gravity and fades out.",
    "layer-scatter-fall": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second layers scatter away individually and fall down with physics while fading out.",
    "glass-shatter": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the full frame shatters like broken glass and fades out.",
    "particle-dissolve": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the full frame dissolves into particles and fades out.",
    "smoke-dissolve": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the full frame dissolves into smoke and fades out.",
    "paper-tear": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the full frame tears like paper and fades out.",
}

SELECTED_PROMPT_OVERRIDES = {
    "cascade": "Cascade this text from top to bottom for 1 second. Preserve every word exactly.",
    "handheld": "Add subtle handheld shake to this layer for 1 second, then settle exactly.",
    "particle-dissolve": "Dissolve this layer into particles over 1 second.",
    "smoke-dissolve": "Dissolve this layer into smoke over 1 second.",
}

WHOLE_PROMPT_OVERRIDES = {
    "fade-in": "First 1 second white background only. Then all layers fade in over 1 second with no movement. Final 0.5 seconds full-frame fade out.",
    "fade-out": "First 0.5 seconds white background only. Then all layers fly into place over 1 second. Final 1 second full-frame fade out.",
    "soft-slide-in": "First 0.5 seconds white background only. Then all layers softly slide into place over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "slide-up": "First 0.5 seconds white background only. Then all layers slide up from below over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "slide-down": "First 0.5 seconds white background only. Then all layers slide down from above over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "pop-in": "First 0.5 seconds white background only. Then all layers pop into place over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "wipe-reveal": "First 0.5 seconds white background only. Then all layers appear with a mask wipe reveal over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "mask-reveal": "First 0.5 seconds white background only. Then all layers appear with a mask reveal over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "blur-fade": "First 0.5 seconds white background only. Then all layers appear with a blur fade over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "premium-float": "First 0.5 seconds white background only. Then all layers float softly into place over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "type-on": "First 0.5 seconds white background only. Then all text layers use typewriter reveal over 1.5 seconds, preserve all words exactly. Final 0.5 seconds full-frame fade out.",
    "fade-up-lines": "First 0.5 seconds white background only. Then text uses fade up lines from top to bottom over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "text-slide-up-lines": "First 0.5 seconds white background only. Then text lines rise from below over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "word-stagger": "First 0.5 seconds white background only. Then text appears word by word over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "character-stagger": "First 0.5 seconds white background only. Then text appears letter by letter over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "kinetic-type": "First 0.5 seconds white background only. Then text uses kinetic typography over 1.5 seconds, preserve all words exactly. Final 0.5 seconds full-frame fade out.",
    "button-y-rise": "First 0.5 seconds white background only. Then buttons rise on position Y over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "parallax-photo": "First 0.5 seconds white background only. Then photos appear through depth parallax over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "depth-card-in": "First 0.5 seconds white background only. Then cards appear with depth parallax over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "iris-reveal": "First 0.5 seconds white background only. Then all layers appear with iris reveal over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "radial-wipe": "First 0.5 seconds white background only. Then all layers appear with radial wipe over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "luma-wipe": "First 0.5 seconds white background only. Then all layers appear with luma wipe over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "liquid-wipe": "First 0.5 seconds white background only. Then all layers appear with liquid wipe over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "pixelate": "First 0.5 seconds white background only. Then all layers appear with pixelate reveal over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "film-burn": "First 0.5 seconds white background only. Then all layers appear with a film burn light leak over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "underline-draw": "First 0.5 seconds white background only. Then underline shapes draw from left to right over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "arrow-draw": "First 0.5 seconds white background only. Then arrow shapes draw from left to right over 1.5 seconds. Final 0.5 seconds full-frame fade out.",
    "gravity-drop-fade": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second layers fall down with gravity and fade out.",
    "glitch": "First 1 second the white background appears with a clean signal scan reveal. Then all layers fade in over 1 second. Final 0.5 seconds full-frame fade out.",
    "smoke-dissolve": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the full frame dissolves into smoke and fades out.",
}

NEGATIVE_PROMPTS = [
    {
        "id": "neg-no-fly-ru",
        "title": "Russian no fly means no movement",
        "prompt": "Вначале только белый фон 1 секунда. Потом все слои появляются сверху вниз градиентным фейд ин за 2 секунды, без залета и без движения. В конце весь фрейм фейдаут 1 секунда.",
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("gradient-fade-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
        "strict_gradient": True,
        "samples": [0.2, 1.2, 2.4, 6.8, 7.7],
    },
    {
        "id": "neg-no-drop-shatter",
        "title": "No falling and no shatter stay fade-only",
        "prompt": "First 1 second white background only. Then every layer appears top-to-bottom with a gradient fade over 2 seconds, no flying. Final 1 second: fade out the whole frame, no falling, no shatter, no shards.",
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("gradient-fade-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
        "strict_gradient": True,
        "samples": [0.2, 1.2, 2.4, 6.8, 7.7],
    },
    {
        "id": "neg-scatter-not-full-drop",
        "title": "Scatter/fall individually is not full-frame drop",
        "prompt": "First 0.5 seconds white background. Then all layers fly into place over 1.5 seconds. At the end the layers scatter individually and fall with physics over 1 second, not as one full frame.",
        "expected_phases": {"intro": ("white-bg-fade", 0.5), "build": ("random-fly-in-stagger", 1.5), "outro": ("layer-scatter-fall", 1.0)},
        "samples": [0.2, 0.8, 1.8, 7.1, 7.8],
    },
    {
        "id": "neg-camera-no-layer",
        "title": "Camera prompt must not animate layers",
        "prompt": "Add a slow whole-frame camera pan to the right over 3 seconds. Keep text and all layers unchanged; do not animate any separate layer.",
        "expected_phases": {"camera": ("camera-pan", 8.0)},
        "camera_only": True,
        "samples": [0.0, 1.5, 3.0, 5.5, 7.5],
    },
]


def valid_text_assets() -> list[str]:
    records = json.loads((ROOT / "app/static/assets/figma-plugin/assets.json").read_text(encoding="utf-8"))
    items = list(records.values()) if isinstance(records, dict) else list(records)
    assets: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "")
        if not asset_id:
            continue
        if not (ROOT / "app/static/assets/figma-plugin" / str(item.get("asset_file") or f"{asset_id}.png")).exists():
            continue
        if source_texts(list(item.get("figma_layers") or [])):
            assets.append(asset_id)
    return sorted(assets)


def pick_layer(layers: list[dict[str, Any]], wanted_kind: str) -> dict[str, Any]:
    normalized = TARGET_KIND_BY_REGISTRY.get(wanted_kind, wanted_kind)
    candidates = [
        layer
        for layer in layers
        if isinstance(layer, dict)
        and layer.get("visible") is not False
        and not str(layer.get("id") or "").startswith("__")
        and str(layer.get("node_type") or "").upper() != "FRAME"
        and not layer.get("motion_internal")
        and not layer.get("cluster_parent_id")
    ]
    for kind in (normalized, "image", "shape", "text"):
        for layer in candidates:
            if str(layer.get("kind") or "") != kind:
                continue
            if kind in {"image", "shape"} and not layer.get("asset_path"):
                continue
            return dict(layer)
    raise RuntimeError(f"no pickable {wanted_kind} layer")


def target_kind_for_effect(effect: Any) -> str:
    for target in effect.targets:
        if target in {"text", "image", "shape"}:
            return str(target)
    return TARGET_KIND_BY_REGISTRY.get(str(effect.targets[0] if effect.targets else "any"), "image")


def selected_prompt(effect: Any) -> str:
    if effect.id in SELECTED_PROMPT_OVERRIDES:
        return SELECTED_PROMPT_OVERRIDES[effect.id]
    if effect.id in EFFECT_PROMPT_OVERRIDES:
        return EFFECT_PROMPT_OVERRIDES[effect.id]
    alias = str(effect.aliases[0] if effect.aliases else effect.id)
    return f"{alias} this layer for 1 second. Preserve the layer design exactly after the animation."


def whole_prompt(effect: Any) -> str:
    if effect.id in WHOLE_PROMPT_OVERRIDES:
        return WHOLE_PROMPT_OVERRIDES[effect.id]
    if effect.id in EFFECT_PROMPT_OVERRIDES:
        return EFFECT_PROMPT_OVERRIDES[effect.id]
    alias = str(effect.aliases[0] if effect.aliases else effect.id)
    if effect.category == "camera":
        return f"Add a subtle whole-frame {alias} for 3 seconds. Keep all layers exactly as designed, do not animate separate layers."
    if effect.category == "exit":
        return f"First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 1 second the whole frame uses {alias} and fades out."
    return f"First 0.5 seconds white background only. Then all layers use {alias} over 1.5 seconds. Final 0.5 seconds full-frame fade out."


def recipe_effect_ids(recipe: dict[str, Any]) -> set[str]:
    result = {str(recipe.get("preset") or "")}
    result.update({str(tag).split("effect:", 1)[1] for tag in list(recipe.get("tags") or []) if str(tag).startswith("effect:")})
    for action in motion_recipe_actions(recipe):
        if action.get("preset"):
            result.add(str(action.get("preset")))
        dsl = action.get("motion_dsl") if isinstance(action.get("motion_dsl"), dict) else {}
        for effect in list(dsl.get("effects") or []):
            if isinstance(effect, dict) and effect.get("type"):
                result.add(str(effect.get("type")))
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    for effect in list(dsl.get("effects") or []):
        if isinstance(effect, dict) and effect.get("type"):
            result.add(str(effect.get("type")))
    return result


def recipe_keyframe_issues(recipe: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    actions = motion_recipe_actions(recipe)
    if not actions:
        actions = [recipe]
    for action in actions:
        dsl = action.get("motion_dsl") if isinstance(action.get("motion_dsl"), dict) else {}
        keyframes = [frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        if not keyframes:
            issues.append("missing motion_dsl.keyframes")
            continue
        times: list[float] = []
        for frame in keyframes:
            try:
                times.append(float(frame.get("time") or 0))
            except (TypeError, ValueError):
                issues.append(f"non-numeric time in {action.get('preset')}")
            for key in ("x", "y", "scale", "scaleX", "scaleY", "rotate", "opacity", "blur", "brightness"):
                if key not in frame:
                    continue
                try:
                    value = float(frame.get(key) or 0)
                except (TypeError, ValueError):
                    issues.append(f"non-numeric {key} in {action.get('preset')}")
                    continue
                if key in {"scale", "scaleX", "scaleY"} and not (0.0 <= value <= 4.0):
                    issues.append(f"out-of-range {key}={value:g}")
                if key == "opacity" and not (-0.01 <= value <= 1.01):
                    issues.append(f"out-of-range opacity={value:g}")
                if key == "blur" and not (0.0 <= value <= 80.0):
                    issues.append(f"out-of-range blur={value:g}")
        if times != sorted(times):
            issues.append(f"non-monotonic keyframes in {action.get('preset')}")
        if any(value < -0.001 for value in times):
            issues.append(f"negative keyframe time in {action.get('preset')}")
    return issues


def plan_effect_issues(effect: Any) -> list[str]:
    issues: list[str] = []
    for scope in effect.scopes:
        for alias in effect.aliases[:5] or (effect.id,):
            target = target_kind_for_effect(effect)
            prompt = str(alias)
            if scope == "selected-layer":
                resolved = primary_supported_effect(prompt, scope="selected-layer", target=target)
                candidates = [item.id for item in resolve_effects(prompt, scope="selected-layer", target=target, limit=8)]
                probe_recipe = _fallback_recipe(selected_prompt(effect), {"id": "qa", "name": "QA", "kind": target, "visible": True, "width": 320, "height": 180, "asset_path": "qa.png"})
                represented = recipe_effect_ids(probe_recipe)
                accepted = {effect.id, *effect.fallback_chain, str(effect.preset)}
                if effect.id not in candidates and not any(fallback in candidates for fallback in effect.fallback_chain) and not (represented & accepted):
                    issues.append(f"alias `{alias}` does not resolve for selected/{target}; candidates={candidates}")
                if resolved is None and not (represented & accepted):
                    issues.append(f"alias `{alias}` has no selected-layer supported fallback")
            elif scope == "whole-frame":
                routed = should_use_frame_choreography_prompt(whole_prompt(effect))
                if not routed:
                    issues.append(f"whole-frame prompt for `{alias}` does not route to choreography")
    return issues


def phase_effect_ids(plan: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for phase in list(plan.get("phases") or []):
        if not isinstance(phase, dict):
            continue
        preset = str(phase.get("preset") or "")
        phase_id = str(phase.get("id") or "")
        if preset:
            result.add(preset)
        if phase_id:
            result.add(phase_id)
        for subphase in list(phase.get("subphases") or []):
            if isinstance(subphase, dict):
                if subphase.get("preset"):
                    result.add(str(subphase.get("preset")))
                if subphase.get("id"):
                    result.add(str(subphase.get("id")))
    return result


def run_cmd(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def render_samples(video: Path, case_id: str, samples: list[float], out_dir: Path) -> list[str]:
    frame_dir = out_dir / case_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[str] = []
    for seconds in samples:
        frame = frame_dir / f"{case_id}_{str(seconds).replace('.', 'p')}s.png"
        if extract_frame(video, float(seconds), frame):
            frames.append(str(frame))
    return frames


def render_case(case: dict[str, Any], motion: Any, assets_dir: Path, out_dir: Path, fps: int) -> tuple[str, list[str], dict[str, Any]]:
    video = render_motion_video_asset(motion, assets_dir, fps=fps)
    if video is None or not video.exists():
        raise RuntimeError("motion video was not rendered")
    copied = out_dir / f"{case['id']}.mp4"
    shutil.copy2(video, copied)
    report_path = video.with_suffix(".visual-self-check.json")
    visual_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    frames = render_samples(copied, case["id"], list(case.get("samples") or [0.0, 1.0, 2.0]), out_dir)
    return str(copied), frames, visual_report


def visual_issues(visual_report: dict[str, Any], allow_no_hold: bool = False) -> list[str]:
    issues: list[str] = []
    checks = {str(item.get("id")): item for item in list(visual_report.get("checks") or []) if isinstance(item, dict)}
    rendered = checks.get("rendered_frames") or {}
    if rendered.get("status") not in {"pass", None}:
        issues.append(f"rendered_frames {rendered.get('status')}")
    hold = checks.get("exact_hold_pixel_match") or {}
    if hold and hold.get("status") != "pass" and not allow_no_hold:
        issues.append(f"exact_hold_pixel_match {hold.get('status')}")
    metrics = visual_report.get("metrics") if isinstance(visual_report.get("metrics"), dict) else {}
    diff = metrics.get("exact_hold_mean_abs_diff")
    if diff is not None and float(diff) > 1.5:
        issues.append(f"hold diff too high {float(diff):.3f}")
    audit = visual_report.get("execution_audit") if isinstance(visual_report.get("execution_audit"), dict) else {}
    if audit.get("status") == "fail":
        issues.append(f"execution_audit fail: {audit.get('issues')}")
    return issues


def run_selected_case(effect: Any, asset_id: str, out_dir: Path, fps: int, index: int) -> dict[str, Any]:
    target_kind = target_kind_for_effect(effect)
    case_id = f"sel-{index:03d}-{effect.id}-{asset_id}".replace("/", "-")
    work_dir = out_dir / "work" / case_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_motion = motion_from_plugin_asset(work_dir, asset_id, start=0, duration=4.0)
    layers = [dict(layer) for layer in list(source_motion.figma_layers or [])]
    target = pick_layer(layers, target_kind)
    prompt = selected_prompt(effect)
    issues: list[str] = []
    recipe = build_layer_motion_recipe_from_prompt(prompt, "replace", target, layers, timeline_duration=4.0)
    target["motion_recipe"] = recipe
    planned_layers = [target if str(layer.get("id") or "") == str(target.get("id") or "") else layer for layer in layers]
    issues.extend(recipe_keyframe_issues(recipe))
    qa = recipe.get("visual_qa") if isinstance(recipe.get("visual_qa"), dict) else {}
    if qa.get("status") == "fail":
        issues.append(f"recipe visual_qa fail: {qa.get('errors')}")
    seen_effects = recipe_effect_ids(recipe)
    accepted = {effect.id, *effect.fallback_chain, str(effect.preset)}
    if not (seen_effects & accepted):
        issues.append(f"effect not represented; seen={sorted(seen_effects)} accepted={sorted(accepted)}")
    before_text = source_texts(list(source_motion.figma_layers or []))
    after_text = source_texts(planned_layers)
    changed = [key for key, value in before_text.items() if after_text.get(key) != value]
    if changed:
        issues.append(f"text metadata changed: {changed[:8]}")
    duration = max(4.0, max((float((action.get("phase_plan") or {}).get("minimum_duration") or 0) for action in motion_recipe_actions(recipe)), default=0.0) + 0.75)
    motion = source_motion.model_copy(update={"id": case_id, "figma_layers": planned_layers, "duration": duration})
    video, frames, visual_report = render_case({"id": case_id, "samples": [0.0, 0.5, 1.1, min(duration - 0.1, 2.5)]}, motion, work_dir / "assets", out_dir, fps)
    issues.extend(visual_issues(visual_report, allow_no_hold=effect.category in {"exit", "accent", "camera"}))
    return {
        "id": case_id,
        "scope": "selected-layer",
        "effect": effect.id,
        "quality": effect.quality,
        "asset_id": asset_id,
        "target_kind": target_kind,
        "prompt": prompt,
        "preset": recipe.get("preset"),
        "seen_effects": sorted(seen_effects),
        "video": video,
        "frames": frames,
        "issues": issues,
        "status": "pass" if not issues else "fail",
    }


def run_whole_case(effect: Any, asset_id: str, out_dir: Path, fps: int, index: int) -> dict[str, Any]:
    case_id = f"whole-{index:03d}-{effect.id}-{asset_id}".replace("/", "-")
    work_dir = out_dir / "work" / case_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_motion = motion_from_plugin_asset(work_dir, asset_id, start=0, duration=8.0)
    prompt = whole_prompt(effect)
    issues: list[str] = []
    if not should_use_frame_choreography_prompt(prompt):
        issues.append("prompt did not route to whole-frame choreography")
    planned_layers = plan_frame_choreography(prompt, source_motion)
    plan = describe_motion_plan(planned_layers) or {}
    planned_layers = attach_frame_motion_contract(prompt, str(source_motion.id), planned_layers, plan)
    plan = describe_motion_plan(planned_layers) or plan
    seen_effects = phase_effect_ids(plan)
    for layer in planned_layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if recipe:
            seen_effects.update(recipe_effect_ids(recipe))
    accepted = {effect.id, *effect.fallback_chain, str(effect.preset)}
    if effect.id == "fade-in":
        accepted.update({"white-bg-fade", "gradient-fade-stagger", "static-reveal"})
    if effect.id == "fade-out":
        accepted.add("full-frame-fade-out")
    if effect.id in {"soft-slide-in", "slide-up", "slide-down", "wipe-reveal", "mask-reveal", "blur-fade", "premium-float", "pop-in"}:
        accepted.update({"random-fly-in-stagger", "gradient-fade-stagger", "advanced-composition-build", "static-reveal"})
    if effect.id in {"type-on", "word-stagger", "character-stagger", "kinetic-type", "fade-up-lines", "text-slide-up-lines"}:
        accepted.update({"fade-up-lines", "text-slide-up-lines", "advanced-composition-build", "gradient-fade-stagger"})
    if effect.id in {"iris-reveal", "radial-wipe", "luma-wipe", "liquid-wipe", "pixelate", "film-burn", "underline-draw", "arrow-draw"}:
        accepted.update({"gradient-fade-stagger", "random-fly-in-stagger", "static-reveal"})
    if effect.id in {"particle-dissolve", "smoke-dissolve"}:
        accepted.update({"layer-scatter-fall", "full-frame-fade-out"})
    if effect.id in {"button-y-rise", "parallax-photo", "depth-card-in"}:
        accepted.add("advanced-composition-build")
    if effect.id == "pan":
        accepted.add("camera-pan")
    if effect.id == "glass-shatter":
        accepted.add("full-frame-shatter")
    if effect.id == "fade-out":
        accepted.add("full-frame-fade-out")
    if not (seen_effects & accepted):
        issues.append(f"effect not represented in phase plan; seen={sorted(seen_effects)} accepted={sorted(accepted)}")
    for layer in planned_layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if recipe:
            issues.extend([f"{layer.get('id')}: {issue}" for issue in recipe_keyframe_issues(recipe)])
    before_text = source_texts(list(source_motion.figma_layers or []))
    after_text = source_texts(planned_layers)
    changed = [key for key, value in before_text.items() if after_text.get(key) != value]
    if changed:
        issues.append(f"text metadata changed: {changed[:8]}")
    duration = max(float(source_motion.duration or 0), float(plan.get("minimum_duration") or 0), 4.0)
    motion = source_motion.model_copy(update={"id": case_id, "figma_layers": planned_layers, "motion_plan": plan, "duration": duration})
    samples = [0.0, min(duration - 0.1, 0.8), min(duration - 0.1, 2.2), min(duration - 0.1, duration * 0.82)]
    video, frames, visual_report = render_case({"id": case_id, "samples": samples}, motion, work_dir / "assets", out_dir, fps)
    issues.extend(visual_issues(visual_report, allow_no_hold=effect.category in {"camera", "accent"}))
    return {
        "id": case_id,
        "scope": "whole-frame",
        "effect": effect.id,
        "quality": effect.quality,
        "asset_id": asset_id,
        "prompt": prompt,
        "seen_effects": sorted(seen_effects),
        "summary": ", ".join(f"{phase.get('id')}:{phase.get('preset')} {phase.get('duration')}s" for phase in list(plan.get("phases") or [])),
        "video": video,
        "frames": frames,
        "issues": issues,
        "status": "pass" if not issues else "fail",
    }


def run_contract_case(prompt_spec: dict[str, Any], asset_id: str, out_dir: Path, fps: int, index: int) -> dict[str, Any]:
    case_id = f"contract-{index:03d}-{prompt_spec['id']}-{asset_id}"
    work_dir = out_dir / "work" / case_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_motion = motion_from_plugin_asset(work_dir, asset_id, start=0, duration=8.0)
    issues: list[str] = []
    prompt = str(prompt_spec["prompt"])
    if not should_use_frame_choreography_prompt(prompt):
        issues.append("prompt did not route to whole-frame choreography")
    planned_layers = plan_frame_choreography(prompt, source_motion)
    plan = describe_motion_plan(planned_layers) or {}
    planned_layers = attach_frame_motion_contract(prompt, str(source_motion.id), planned_layers, plan)
    plan = describe_motion_plan(planned_layers) or plan
    issues.extend(assert_phase_contract(prompt_spec, plan))
    issues.extend(order_issues(prompt_spec, plan))
    issues.extend(text_transform_issues(prompt_spec, planned_layers))
    if prompt_spec.get("strict_gradient"):
        issues.extend(strict_gradient_issues(planned_layers, plan))
    if prompt_spec.get("camera_only"):
        issues.extend(camera_layer_issues(planned_layers))
    duration = max(float(source_motion.duration or 0), float(plan.get("minimum_duration") or 0), 4.0)
    motion = source_motion.model_copy(update={"id": case_id, "figma_layers": planned_layers, "motion_plan": plan, "duration": duration})
    video, frames, visual_report = render_case({"id": case_id, "samples": list(prompt_spec.get("samples") or [0.0, 1.0, 2.0])}, motion, work_dir / "assets", out_dir, fps)
    if prompt_spec.get("camera_only"):
        before_text = source_texts(list(source_motion.figma_layers or []))
        after_text = source_texts(planned_layers)
        changed = [key for key, value in before_text.items() if after_text.get(key) != value]
        if changed:
            issues.append(f"text metadata changed: {changed[:8]}")
    else:
        issues.extend(assert_text_integrity(source_motion, planned_layers, visual_report, None))
    issues.extend(visual_issues(visual_report, allow_no_hold=bool(prompt_spec.get("camera_only"))))
    return {
        "id": case_id,
        "scope": "contract",
        "effect": prompt_spec["id"],
        "asset_id": asset_id,
        "prompt": prompt,
        "summary": ", ".join(f"{phase.get('id')}:{phase.get('preset')} {phase.get('duration')}s" for phase in list(plan.get("phases") or [])),
        "video": video,
        "frames": frames,
        "issues": issues,
        "status": "pass" if not issues else "fail",
    }


def build_contact_sheet(cases: list[dict[str, Any]], out_dir: Path, limit: int = 140) -> Path:
    shown = cases[:limit]
    label_w = 470
    thumb_w = 190
    thumb_h = 107
    row_h = 145
    cols = 4
    sheet = Image.new("RGB", (label_w + cols * thumb_w + 34, 56 + len(shown) * row_h), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    draw.text((18, 16), "VibeMotion max motion matrix QA", fill=(18, 18, 18), font=font(22, True))
    small = font(10)
    label = font(13, True)
    y = 56
    for case in shown:
        color = (28, 128, 68) if case["status"] == "pass" else (174, 54, 34)
        draw.rounded_rectangle((10, y + 6, sheet.width - 10, y + row_h - 8), radius=8, fill=(255, 255, 255), outline=(218, 214, 204))
        draw.text((22, y + 18), f"{case['id']} {case['status'].upper()}", fill=color, font=label)
        draw.text((22, y + 38), f"{case.get('scope')} / {case.get('effect')} / {case.get('asset_id')}", fill=(20, 20, 20), font=small)
        message = "; ".join(case.get("issues") or []) or case.get("summary") or f"preset={case.get('preset')} effects={case.get('seen_effects')}"
        draw.text((22, y + 60), message[:116], fill=(150, 45, 30) if case.get("issues") else (72, 72, 72), font=small)
        for index, frame_path in enumerate(case.get("frames", [])[:cols]):
            x = label_w + 12 + index * thumb_w
            try:
                with Image.open(frame_path).convert("RGB") as frame:
                    frame.thumbnail((thumb_w - 12, thumb_h), Image.Resampling.LANCZOS)
                    bg = Image.new("RGB", (thumb_w - 12, thumb_h), (18, 18, 18))
                    bg.paste(frame, ((bg.width - frame.width) // 2, (bg.height - frame.height) // 2))
                    sheet.paste(bg, (x, y + 19))
            except Exception:
                draw.rectangle((x, y + 19, x + thumb_w - 12, y + 19 + thumb_h), fill=(210, 210, 210))
        y += row_h
    target = out_dir / "motion_max_matrix_contact_sheet.png"
    sheet.save(target)
    return target


def write_report(cases: list[dict[str, Any]], plan_issues: dict[str, list[str]], out_dir: Path, contact_sheet: Path) -> Path:
    report = out_dir / "REPORT.md"
    lines = [
        "# VibeMotion Max Motion Matrix QA",
        "",
        f"- Total render cases: `{len(cases)}`",
        f"- Pass: `{sum(1 for c in cases if c['status'] == 'pass')}`",
        f"- Fail: `{sum(1 for c in cases if c['status'] != 'pass')}`",
        f"- Plan/registry effects with issues: `{sum(1 for issues in plan_issues.values() if issues)}`",
        f"- Contact sheet: `{contact_sheet}`",
        "",
        f"![contact sheet]({contact_sheet.as_posix()})",
        "",
    ]
    bad_plan = {key: value for key, value in plan_issues.items() if value}
    if bad_plan:
        lines.extend(["## Plan Issues", ""])
        for effect_id, issues in bad_plan.items():
            lines.append(f"- `{effect_id}`: {'; '.join(issues[:8])}")
        lines.append("")
    lines.extend(["## Render Cases", ""])
    for case in cases:
        lines.extend(
            [
                f"### {case['id']} - {case['status'].upper()}",
                "",
                f"- Scope: `{case.get('scope')}`",
                f"- Effect: `{case.get('effect')}`",
                f"- Asset: `{case.get('asset_id')}`",
                f"- Prompt: {case.get('prompt')}",
                f"- Summary: {case.get('summary') or case.get('preset') or ''}",
                f"- Issues: {('; '.join(case.get('issues') or []) or 'none')}",
                f"- Video: `{case.get('video')}`",
                "",
            ]
        )
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260518 + 77)
    parser.add_argument("--fps", type=int, default=4)
    parser.add_argument("--out", default="")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--max-render-effects", type=int, default=0, help="0 means render all effect cases")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    assets = valid_text_assets()
    if not assets:
        raise SystemExit("No text-bearing Figma assets found")
    rng.shuffle(assets)
    effects = list(all_effects())

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-max-matrix-{stamp}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan_issues = {effect.id: plan_effect_issues(effect) for effect in effects}
    cases: list[dict[str, Any]] = []
    if not args.skip_render:
        render_jobs: list[tuple[str, Any, str]] = []
        for effect in effects:
            if "selected-layer" in effect.scopes:
                render_jobs.append(("selected", effect, rng.choice(assets)))
            if "whole-frame" in effect.scopes:
                render_jobs.append(("whole", effect, rng.choice(assets)))
        if args.max_render_effects > 0:
            render_jobs = render_jobs[: args.max_render_effects]
        for index, (scope, effect, asset_id) in enumerate(render_jobs, start=1):
            try:
                if scope == "selected":
                    case = run_selected_case(effect, asset_id, out_dir, max(3, int(args.fps)), index)
                else:
                    case = run_whole_case(effect, asset_id, out_dir, max(3, int(args.fps)), index)
            except Exception as exc:
                case = {
                    "id": f"{scope}-{index:03d}-{effect.id}-{asset_id}".replace("/", "-"),
                    "scope": scope,
                    "effect": effect.id,
                    "quality": effect.quality,
                    "asset_id": asset_id,
                    "prompt": selected_prompt(effect) if scope == "selected" else whole_prompt(effect),
                    "video": "",
                    "frames": [],
                    "issues": [f"{type(exc).__name__}: {exc}"],
                    "status": "fail",
                }
            cases.append(case)
            print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}", flush=True)

        contract_specs = [*WAVE_PROMPTS, *NEGATIVE_PROMPTS]
        for offset, prompt_spec in enumerate(contract_specs, start=len(cases) + 1):
            for repeat in range(2):
                asset_id = rng.choice(assets)
                try:
                    case = run_contract_case(prompt_spec, asset_id, out_dir, max(3, int(args.fps)), offset * 10 + repeat)
                except Exception as exc:
                    case = {
                        "id": f"contract-{offset:03d}-{repeat}-{prompt_spec['id']}-{asset_id}",
                        "scope": "contract",
                        "effect": prompt_spec["id"],
                        "asset_id": asset_id,
                        "prompt": prompt_spec["prompt"],
                        "video": "",
                        "frames": [],
                        "issues": [f"{type(exc).__name__}: {exc}"],
                        "status": "fail",
                    }
                cases.append(case)
                print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}", flush=True)

    contact_sheet = build_contact_sheet(cases, out_dir) if cases else out_dir / "motion_max_matrix_contact_sheet.png"
    report = write_report(cases, plan_issues, out_dir, contact_sheet)
    summary = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) and not any(plan_issues.values()) else "fail",
        "seed": args.seed,
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "plan_effects_total": len(plan_issues),
        "plan_effects_fail": sum(1 for issues in plan_issues.values() if issues),
        "render_total": len(cases),
        "render_pass": sum(1 for case in cases if case["status"] == "pass"),
        "render_fail": sum(1 for case in cases if case["status"] != "pass"),
        "plan_issues": plan_issues,
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("status", "out_dir", "report", "contact_sheet", "plan_effects_fail", "render_total", "render_pass", "render_fail")}, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
