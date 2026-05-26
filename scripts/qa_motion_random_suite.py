from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.motion import _motion_dsl_state, render_motion_video_asset
from app.services.motion_intent import (
    attach_frame_motion_contract,
    build_layer_motion_recipe_after_delete,
    build_layer_motion_recipe_from_prompt,
    motion_recipe_actions,
)
from app.services.projects import load_state, project_dir


PROMPTS = {
    "fade2": "\u0421\u0434\u0435\u043b\u0430\u0439 \u0444\u0435\u0439\u0434\u0438\u043d \u0432\u043d\u0430\u0447\u0430\u043b\u0435 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b",
    "fadeout2_start": "\u0421\u0434\u0435\u043b\u0430\u0439 \u0444\u0435\u0439\u0434 \u0430\u0443\u0442 \u0432 \u043d\u0430\u0447\u0430\u043b\u0435 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b",
    "drop5": "\u041d\u0430 5 \u0441\u0435\u043a\u0443\u043d\u0434\u0435 \u0431\u043b\u043e\u043a \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 \u043a\u0430\u043a \u043a\u0430\u043c\u0435\u043d\u044c",
    "retime3": "\u0421\u0434\u0435\u043b\u0430\u0439 \u0434\u043b\u0438\u043d\u043d\u0435\u0435 \u043d\u0430 3 \u0441\u0435\u043a\u0443\u043d\u0434\u044b",
    "drop_after": "\u0410 \u0442\u0435\u043f\u0435\u0440\u044c \u043f\u0443\u0441\u0442\u044c \u043a\u043e\u0433\u0434\u0430 \u0444\u0435\u0439\u0434 \u0438\u043d \u0440\u0435\u0430\u043b\u0438\u0437\u0443\u0435\u0442\u0441\u044f \u0442\u043e \u043f\u043e\u0441\u043b\u0435 \u044d\u0442\u043e\u0433\u043e \u0447\u0435\u0440\u0435\u0437 \u0441\u0435\u043a\u0443\u043d\u0434\u0443 \u0432\u0435\u0441\u044c \u0431\u043b\u043e\u043a \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 \u043a\u0430\u043a \u043a\u0430\u043c\u0435\u043d\u044c",
    "replace_fadeout1": "\u041d\u043e\u0432\u0430\u044f: \u0441\u0434\u0435\u043b\u0430\u0439 \u0444\u0435\u0439\u0434 \u0430\u0443\u0442 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 1 \u0441\u0435\u043a\u0443\u043d\u0434\u0443",
    "soft_slide": "\u041f\u0443\u0441\u0442\u044c \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a \u043c\u044f\u0433\u043a\u043e \u0432\u044b\u043b\u0435\u0442\u0430\u0435\u0442 \u0441\u043b\u0435\u0432\u0430 \u0437\u0430 1.2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b",
    "english_fade": "Fade in over 1.5 seconds, then hold cleanly",
    "end_fadeout": "\u0412 \u043a\u043e\u043d\u0446\u0435 \u0441\u0434\u0435\u043b\u0430\u0439 \u0444\u0435\u0439\u0434 \u0430\u0443\u0442 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 1 \u0441\u0435\u043a\u0443\u043d\u0434\u0443",
    "whole_complex": "\u043f\u0435\u0440\u0432\u044b\u0435 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b - \u0444\u0435\u0439\u0434 \u0438\u043d \u0431\u0435\u043b\u043e\u0433\u043e \u044d\u043a\u0440\u0430\u043d\u0430 \u0432\u043d\u0430\u0447\u0430\u043b\u0435, \u0438\u043c\u0435\u043d\u043d\u043e \u0444\u043e\u043d\u0430 \u0431\u0435\u0437 \u0434\u0440\u0443\u0433\u0438\u0445 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u043e\u0432. \u041f\u043e\u0442\u043e\u043c \u0432\u0441\u0435 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u044b \u043d\u0430\u0447\u0438\u043d\u0430\u044e\u0442 \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0438 \u0442\u0440\u0435\u0445 \u0441\u0435\u043a\u0443\u043d\u0434 \u0432\u043b\u0435\u0442\u0430\u0442\u044c \u0432 \u0444\u0440\u0435\u0439\u043c \u043d\u0430 \u0441\u0432\u043e\u0438 \u043c\u0435\u0441\u0442\u0430 \u0432 \u0441\u043b\u0443\u0447\u0430\u0439\u043d\u043e\u043c \u043f\u043e\u0440\u044f\u0434\u043a\u0435. \u0412\u043a\u043e\u043d\u0446\u0435 \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 \u043a\u0430\u043a \u0440\u0430\u0437\u0431\u0438\u0442\u043e\u0435 \u0441\u0442\u0435\u043a\u043b\u043e \u043f\u0440\u0435\u0434\u0432\u0430\u0440\u0438\u0442\u0435\u043b\u044c\u043d\u043e \u0440\u0430\u0437\u0431\u0438\u0432\u0448\u0438\u0441\u044c \u043d\u0430 \u043a\u0443\u0441\u043a\u0438 \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0438 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0445 3\u0445 \u0441\u0435\u043a\u0443\u043d\u0434 \u0438 \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c \u0443\u0445\u043e\u0434\u0438\u0442 \u0432 \u0444\u0435\u0439\u0434 \u0430\u0443\u0442",
    "whole_short": "\u043f\u0435\u0440\u0432\u044b\u0435 1 \u0441\u0435\u043a\u0443\u043d\u0434\u0430 \u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d \u0431\u0435\u0437 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u043e\u0432, \u043f\u043e\u0442\u043e\u043c \u0432\u0441\u0435 \u0441\u043b\u043e\u0438 \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0435 2 \u0441\u0435\u043a\u0443\u043d\u0434 \u0432\u043b\u0435\u0442\u0430\u044e\u0442 \u0432 \u0441\u043b\u0443\u0447\u0430\u0439\u043d\u043e\u043c \u043f\u043e\u0440\u044f\u0434\u043a\u0435, \u0432 \u043a\u043e\u043d\u0446\u0435 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c \u0440\u0430\u0437\u0431\u0438\u0432\u0430\u0435\u0442\u0441\u044f \u043d\u0430 \u043a\u0443\u0441\u043a\u0438 \u0438 \u0443\u0445\u043e\u0434\u0438\u0442 \u0432 \u0444\u0435\u0439\u0434 \u0430\u0443\u0442",
    "whole_english": "First 1.5 seconds: background only. Then all elements fly into place over 2.5 seconds in random order. In the last 2 seconds the full frame shatters and fades out.",
}


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower() or "case"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def clean_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for layer in layers:
        layer_id = str(layer.get("id") or "")
        if layer_id.startswith("__frame_choreo_"):
            continue
        clean = dict(layer)
        clean.pop("motion_recipe", None)
        clean.pop("render_cluster_source", None)
        clean.pop("cluster_child_ids", None)
        clean.pop("cluster_parent_id", None)
        clean.pop("source_crop_key_transparent", None)
        clean.pop("motion_internal", None)
        result.append(clean)
    return result


def pick_layers(layers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    def is_pickable(layer: dict[str, Any], kind: str) -> bool:
        layer_id = str(layer.get("id") or "")
        node_type = str(layer.get("node_type") or "").upper()
        if layer.get("visible") is False or layer.get("mask_role") or layer_id.startswith("__frame_choreo_"):
            return False
        if layer.get("cluster_parent_id") or layer.get("motion_internal"):
            return False
        if node_type == "FRAME":
            return False
        if str(layer.get("kind") or "") != kind:
            return False
        if kind in {"image", "shape"} and not layer.get("asset_path"):
            return False
        return True

    picked: dict[str, dict[str, Any]] = {}
    for kind in ("image", "text", "shape"):
        for layer in layers:
            if is_pickable(layer, kind):
                picked[kind] = layer
                break
    if {"image", "text", "shape"} - set(picked):
        raise RuntimeError("Need image, text and shape layers for QA")
    images = [layer for layer in layers if is_pickable(layer, "image")]
    if len(images) > 1:
        picked["image2"] = images[1]
    else:
        picked["image2"] = picked["image"]
    return picked


def layer_by_id(layers: list[dict[str, Any]], layer_id: str) -> dict[str, Any]:
    for layer in layers:
        if str(layer.get("id") or "") == str(layer_id):
            return layer
    raise KeyError(layer_id)


def recipe_required_duration(recipe: dict[str, Any] | None) -> float:
    if not isinstance(recipe, dict):
        return 0.0
    raw_actions = recipe.get("motion_actions")
    if isinstance(raw_actions, list):
        actions = [action for action in raw_actions if isinstance(action, dict)]
        if actions:
            return max((recipe_required_duration(action) for action in actions), default=0.0)
    phase = recipe.get("phase_plan") if isinstance(recipe.get("phase_plan"), dict) else {}
    required = 0.0
    for key in ("minimum_duration", "duration"):
        try:
            required = max(required, float(phase.get(key) or 0))
        except (TypeError, ValueError):
            pass
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    for frame in list(dsl.get("keyframes") or []):
        if isinstance(frame, dict):
            try:
                required = max(required, float(frame.get("time") or 0))
            except (TypeError, ValueError):
                pass
    return required


def action_summary(recipe: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = []
    for action in motion_recipe_actions(recipe):
        phase = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
        result.append(
            {
                "id": action.get("id"),
                "preset": action.get("preset"),
                "label": action.get("label"),
                "start": phase.get("start", 0),
                "duration": phase.get("duration"),
                "minimum_duration": phase.get("minimum_duration"),
            }
        )
    return result


def backend_samples(recipe: dict[str, Any] | None, times: list[float], duration: float) -> list[dict[str, Any]]:
    if not isinstance(recipe, dict):
        return []
    samples = []
    for seconds in times:
        state = _motion_dsl_state(recipe, seconds, duration)
        samples.append({key: round(float(state.get(key, 0) or 0), 4) for key in ("x", "y", "scale", "scaleX", "scaleY", "rotate", "opacity", "blur")})
    return samples


def cleanup_qa_render_cache(project_assets: Path, spec_id: str, video: Path | None) -> None:
    """Remove render cache produced by this QA case after artifacts are copied."""
    project_assets = project_assets.resolve()
    candidates = [project_assets / f"{spec_id}_frames"]
    if video is not None:
        candidates.append(video)
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved == project_assets or project_assets not in resolved.parents:
            continue
        if not resolved.name.startswith("qa-random-"):
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)


def apply_selected_steps(base_layers: list[dict[str, Any]], target_id: str, steps: list[dict[str, Any]], timeline_duration: float | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    layers = [dict(layer) for layer in base_layers]
    target = dict(layer_by_id(layers, target_id))
    operations: list[dict[str, Any]] = []
    recipe: dict[str, Any] | None = None
    for step in steps:
        if step["kind"] == "prompt":
            recipe = build_layer_motion_recipe_from_prompt(step["prompt"], step.get("mode", "replace"), target, layers, timeline_duration=timeline_duration)
            target["motion_recipe"] = recipe
            operations.append({"kind": "prompt", "mode": step.get("mode", "replace"), "prompt": step["prompt"], "operation": recipe.get("motion_operation")})
        elif step["kind"] == "delete_first":
            actions = motion_recipe_actions(recipe)
            if not actions:
                operations.append({"kind": "delete_first", "error": "no action"})
                continue
            recipe, operation, found = build_layer_motion_recipe_after_delete(recipe, str(actions[0].get("id") or ""), target)
            if recipe:
                target["motion_recipe"] = recipe
            else:
                target.pop("motion_recipe", None)
            operations.append({"kind": "delete_first", "found": found, "operation": operation})
    output = []
    for layer in layers:
        if str(layer.get("id") or "") == str(target_id):
            output.append(target)
        else:
            output.append(layer)
    return output, recipe, operations


def apply_visibility(base_layers: list[dict[str, Any]], hidden_id: str, animated_id: str, prompt: str, timeline_duration: float | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    layers = []
    target = None
    for layer in base_layers:
        clean = dict(layer)
        if str(clean.get("id") or "") == str(hidden_id):
            clean["visible"] = False
        if str(clean.get("id") or "") == str(animated_id):
            target = clean
        layers.append(clean)
    if target is None:
        raise KeyError(animated_id)
    recipe = build_layer_motion_recipe_from_prompt(prompt, "replace", target, layers, timeline_duration=timeline_duration)
    target["motion_recipe"] = recipe
    return layers, recipe, [{"kind": "visibility", "hidden_layer_id": hidden_id}, {"kind": "prompt", "mode": "replace", "prompt": prompt, "operation": recipe.get("motion_operation")}]


def apply_whole_frame(motion: Any, prompt: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    if not should_use_frame_choreography_prompt(prompt):
        return clean_layers(list(motion.figma_layers or [])), None, [{"kind": "whole-frame", "prompt": prompt, "error": "not routed to frame choreography"}]
    planning_motion = motion.model_copy(update={"figma_layers": clean_layers(list(motion.figma_layers or []))})
    layers = plan_frame_choreography(prompt, planning_motion)
    phase_plan = describe_motion_plan(layers)
    layers = attach_frame_motion_contract(prompt, str(motion.id), layers, phase_plan)
    return layers, describe_motion_plan(layers), [{"kind": "whole-frame", "prompt": prompt, "operation": (describe_motion_plan(layers) or {}).get("motion_operation")}]


def render_case(motion: Any, case: dict[str, Any], project_assets: Path, out_dir: Path) -> tuple[Path | None, list[Path]]:
    spec_id = f"qa-random-{safe_slug(case['id'])}"
    spec = motion.model_copy(
        update={
            "id": spec_id,
            "start": 0,
            "duration": float(case["duration"]),
            "figma_layers": case["layers"],
            "enter_animation": "none",
            "exit_animation": "none",
            "prompt": f"QA random suite: {case['title']}",
            "motion_plan": None,
            "motion_units": [],
            "video_asset_path": None,
            "asset_version": None,
        }
    )
    video = render_motion_video_asset(spec, project_assets, fps=8)
    if video is None or not video.exists():
        return None, []
    try:
        copied_video = out_dir / f"{case['id']}.mp4"
        shutil.copy2(video, copied_video)
        frame_dir = out_dir / case["id"]
        frame_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for seconds in case["samples"]:
            label = str(seconds).replace(".", "p")
            frame = frame_dir / f"{case['id']}_{label}s.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{seconds:.3f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(frame),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            frames.append(frame)
        return copied_video, frames
    finally:
        cleanup_qa_render_cache(project_assets, spec_id, video)


def pixel_fidelity_outside_layers(motion: Any, frame_paths: list[Path], exclude_layers: list[dict[str, Any]], source_path: Path) -> dict[str, Any]:
    if not frame_paths or not source_path.exists():
        return {"status": "skip", "max_mean_rgb_diff": None}
    source = Image.open(source_path).convert("RGB").resize((motion.width, motion.height), Image.Resampling.LANCZOS)
    bounds_layer = next((layer for layer in list(motion.figma_layers or []) if str(layer.get("id") or "") == str(motion.figma_node_id or "")), None)
    bounds_width = float((bounds_layer or {}).get("width") or motion.width or 1)
    bounds_height = float((bounds_layer or {}).get("height") or motion.height or 1)
    scale_x = motion.width / max(1.0, bounds_width)
    scale_y = motion.height / max(1.0, bounds_height)
    mask = Image.new("L", source.size, 255)
    draw = ImageDraw.Draw(mask)
    pad = max(8, int(round(min(motion.width, motion.height) * 0.006)))
    for layer in exclude_layers:
        x = int(round(float(layer.get("x") or 0) * scale_x)) - pad
        y = int(round(float(layer.get("y") or 0) * scale_y)) - pad
        right = int(round((float(layer.get("x") or 0) + float(layer.get("width") or 1)) * scale_x)) + pad
        bottom = int(round((float(layer.get("y") or 0) + float(layer.get("height") or 1)) * scale_y)) + pad
        draw.rectangle((max(0, x), max(0, y), min(source.width, right), min(source.height, bottom)), fill=0)
    max_mean = 0.0
    samples: list[dict[str, Any]] = []
    for path in frame_paths:
        frame = Image.open(path).convert("RGB").resize(source.size, Image.Resampling.LANCZOS)
        diff = ImageChops.difference(frame, source)
        stat = ImageStat.Stat(diff, mask)
        value = max(float(item) for item in stat.mean)
        max_mean = max(max_mean, value)
        samples.append({"frame": str(path), "mean_rgb_diff": round(value, 3)})
    return {
        "status": "pass" if max_mean <= 6.0 else "fail",
        "max_mean_rgb_diff": round(max_mean, 3),
        "samples": samples,
    }


def evaluate_case(case: dict[str, Any]) -> tuple[str, list[str]]:
    issues: list[str] = []
    recipe = case.get("recipe")
    plan = case.get("plan")
    expected = case.get("expected") or {}
    if recipe:
        qa = recipe.get("visual_qa") if isinstance(recipe.get("visual_qa"), dict) else {}
        if qa.get("status") != "pass":
            issues.append(f"visual_qa={qa.get('status')}")
        actions = motion_recipe_actions(recipe)
        if "action_count" in expected and len(actions) != expected["action_count"]:
            issues.append(f"action_count {len(actions)} != {expected['action_count']}")
        if "preset" in expected and (not actions or actions[-1].get("preset") != expected["preset"]):
            issues.append(f"preset {actions[-1].get('preset') if actions else None} != {expected['preset']}")
        if "duration" in expected and actions:
            duration = float((actions[-1].get("phase_plan") or {}).get("duration") or 0)
            if abs(duration - float(expected["duration"])) > 0.035:
                issues.append(f"duration {duration:g}s != {expected['duration']:g}s")
        if "start" in expected and actions:
            start = float((actions[-1].get("phase_plan") or {}).get("start") or 0)
            if abs(start - float(expected["start"])) > 0.035:
                issues.append(f"start {start:g}s != {expected['start']:g}s")
        if expected.get("should_anchor_to_end") and actions:
            start = float((actions[-1].get("phase_plan") or {}).get("start") or 0)
            intended_start = max(0.0, float(case["duration"]) - float(expected.get("duration", 1.0)))
            if abs(start - intended_start) > 0.25:
                issues.append(f"not end-anchored: start {start:g}s, expected about {intended_start:g}s")
    elif plan:
        qa = plan.get("visual_qa") if isinstance(plan.get("visual_qa"), dict) else {}
        if qa.get("status") != "pass":
            issues.append(f"visual_qa={qa.get('status')}")
        phases = {phase.get("id"): phase for phase in plan.get("phases", []) if isinstance(phase, dict)}
        for phase_id, phase_expected in expected.get("phases", {}).items():
            phase = phases.get(phase_id)
            if not phase:
                issues.append(f"missing phase {phase_id}")
                continue
            if "preset" in phase_expected and phase.get("preset") != phase_expected["preset"]:
                issues.append(f"{phase_id} preset {phase.get('preset')} != {phase_expected['preset']}")
            if "duration" in phase_expected and abs(float(phase.get("duration") or 0) - float(phase_expected["duration"])) > 0.035:
                issues.append(f"{phase_id} duration {phase.get('duration')} != {phase_expected['duration']}")
    else:
        issues.append("no recipe or plan")
    fidelity = case.get("pixel_fidelity") if isinstance(case.get("pixel_fidelity"), dict) else None
    if fidelity and fidelity.get("status") == "fail":
        issues.append(f"pixel fidelity outside animated layer too high: {fidelity.get('max_mean_rgb_diff')}")
    return ("pass" if not issues else "fail"), issues


def make_case_sheets(cases: list[dict[str, Any]], out_dir: Path) -> Path:
    label_w = 310
    thumb_w = 260
    thumb_h = 146
    row_h = 198
    cols = 3
    width = label_w + cols * thumb_w + 34
    height = 56 + len(cases) * row_h
    sheet = Image.new("RGB", (width, height), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    title_font = font(22, True)
    label_font = font(14, True)
    small_font = font(11)
    draw.text((18, 14), "VibeMotion motion random QA suite", fill=(18, 18, 18), font=title_font)
    y = 56
    for case in cases:
        status_color = (28, 128, 68) if case["status"] == "pass" else (170, 56, 32)
        draw.rounded_rectangle((10, y + 6, width - 10, y + row_h - 8), radius=8, fill=(255, 255, 255), outline=(218, 214, 204))
        draw.text((22, y + 18), f"{case['id']} {case['status'].upper()}", fill=status_color, font=label_font)
        title = case["title"][:58]
        draw.text((22, y + 40), title, fill=(18, 18, 18), font=small_font)
        layer = case.get("layer_label") or case.get("scope") or ""
        draw.text((22, y + 58), layer[:62], fill=(82, 82, 82), font=small_font)
        if case.get("issues"):
            draw.text((22, y + 78), ("; ".join(case["issues"]))[:70], fill=(155, 45, 30), font=small_font)
        else:
            draw.text((22, y + 78), case.get("summary", "")[:70], fill=(72, 72, 72), font=small_font)
        for index, frame_path in enumerate(case.get("frames", [])[:cols]):
            x = label_w + 12 + index * thumb_w
            try:
                with Image.open(frame_path).convert("RGB") as frame:
                    frame.thumbnail((thumb_w - 14, thumb_h), Image.Resampling.LANCZOS)
                    px = x + max(0, (thumb_w - 14 - frame.width) // 2)
                    py = y + 22 + max(0, (thumb_h - frame.height) // 2)
                    sheet.paste(frame, (px, py))
            except Exception:
                draw.rectangle((x, y + 24, x + thumb_w - 14, y + 24 + thumb_h), fill=(210, 210, 210))
            sample = case.get("samples", [])[index] if index < len(case.get("samples", [])) else ""
            draw.text((x + 4, y + 24 + thumb_h + 5), f"t={sample}s", fill=(65, 65, 65), font=small_font)
        y += row_h
    sheet_path = out_dir / "motion_random_suite_contact_sheet.png"
    sheet.save(sheet_path)
    return sheet_path


def write_markdown_report(cases: list[dict[str, Any]], out_dir: Path, contact_sheet: Path, project_id: str) -> Path:
    lines = [
        "# VibeMotion motion random QA suite",
        "",
        f"- Project: `{project_id}`",
        f"- Generated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Contact sheet: `{contact_sheet}`",
        f"- Total: `{len(cases)}` cases, pass: `{sum(1 for case in cases if case['status'] == 'pass')}`, fail: `{sum(1 for case in cases if case['status'] != 'pass')}`",
        "",
        f"![contact sheet]({contact_sheet.as_posix()})",
        "",
        "## Cases",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"### {case['id']} - {case['title']} - {case['status'].upper()}",
                "",
                f"- Scope: `{case.get('scope')}`",
                f"- Layer: `{case.get('layer_label', '')}`",
                f"- Samples: `{case.get('samples')}`",
                f"- Summary: {case.get('summary', '')}",
                f"- Issues: {('; '.join(case.get('issues') or []) or 'none')}",
                f"- Video: `{case.get('video')}`",
            ]
        )
        if case.get("prompt"):
            lines.append(f"- Prompt: {case['prompt']}")
        if case.get("actions"):
            lines.append(f"- Actions: `{json.dumps(case['actions'], ensure_ascii=False)}`")
        if case.get("phase_plan"):
            phases = case["phase_plan"].get("phases", [])
            lines.append(f"- Phases: `{json.dumps(phases, ensure_ascii=False)}`")
        if case.get("pixel_fidelity"):
            lines.append(f"- Pixel fidelity outside animated layer: `{json.dumps(case['pixel_fidelity'], ensure_ascii=False)}`")
        for frame in case.get("frames", []):
            lines.append(f"![{case['id']}]({Path(frame).as_posix()})")
        lines.append("")
    report = out_dir / "REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="test-b5b1e836")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    state = load_state(args.project)
    motion = next((item for item in state.motions if item.source_type == "figma" and item.figma_layers), None)
    if motion is None:
        raise SystemExit("No Figma motion found")
    base_layers = clean_layers(list(motion.figma_layers or []))
    picked = pick_layers(base_layers)
    base = project_dir(args.project)
    project_assets = base / "assets"
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-random-suite-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    specs: list[dict[str, Any]] = [
        {
            "id": "t01-image-fadein-2s",
            "title": "single image fade-in with literal 2s timing",
            "scope": "selected-layer",
            "layer": picked["image"],
            "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade2"]}],
            "samples": [0.1, 1.0, 2.4],
            "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0},
        },
        {
            "id": "t02-text-fadeout-2s",
            "title": "single text fade-out at start for 2s",
            "scope": "selected-layer",
            "layer": picked["text"],
            "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fadeout2_start"]}],
            "samples": [0.1, 1.0, 2.4],
            "expected": {"action_count": 1, "preset": "fade-out", "duration": 2.0, "start": 0},
        },
        {
            "id": "t03-shape-drop-at-5s",
            "title": "shape gravity drop anchored at absolute 5s",
            "scope": "selected-layer",
            "layer": picked["shape"],
            "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["drop5"]}],
            "samples": [4.8, 5.4, 6.2],
            "duration": 7.0,
            "expected": {"action_count": 1, "preset": "gravity-drop-fade", "start": 5.0},
        },
        {
            "id": "t04-conversation-stack",
            "title": "many small prompts on one image: fade, retime, then drop",
            "scope": "selected-layer",
            "layer": picked["image2"],
            "steps": [
                {"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade2"]},
                {"kind": "prompt", "mode": "append", "prompt": PROMPTS["retime3"]},
                {"kind": "prompt", "mode": "append", "prompt": PROMPTS["drop_after"]},
            ],
            "samples": [2.5, 5.4, 6.6],
            "duration": 8.0,
            "expected": {"action_count": 2, "preset": "gravity-drop-fade", "start": 6.0},
        },
        {
            "id": "t05-replace-resets-stack",
            "title": "replace/new clears previous stack and creates one fade-out",
            "scope": "selected-layer",
            "layer": picked["text"],
            "steps": [
                {"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade2"]},
                {"kind": "prompt", "mode": "append", "prompt": PROMPTS["drop_after"]},
                {"kind": "prompt", "mode": "replace", "prompt": PROMPTS["replace_fadeout1"]},
            ],
            "samples": [0.1, 0.6, 1.4],
            "expected": {"action_count": 1, "preset": "fade-out", "duration": 1.0},
        },
        {
            "id": "t06-delete-action",
            "title": "delete one action from stacked motion",
            "scope": "selected-layer",
            "layer": picked["image"],
            "steps": [
                {"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade2"]},
                {"kind": "prompt", "mode": "append", "prompt": PROMPTS["drop_after"]},
                {"kind": "delete_first"},
            ],
            "samples": [0.8, 2.6, 3.3],
            "duration": 4.5,
            "expected": {"action_count": 1, "preset": "gravity-drop-fade", "start": 3.0},
        },
        {
            "id": "t07-soft-slide-fallback",
            "title": "non-deterministic style phrase falls back to soft-slide planner",
            "scope": "selected-layer",
            "layer": picked["text"],
            "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["soft_slide"]}],
            "samples": [0.1, 0.6, 1.4],
            "expected": {"action_count": 1, "preset": "soft-slide"},
        },
        {
            "id": "t08-english-fade-1p5",
            "title": "English fade-in over 1.5s",
            "scope": "selected-layer",
            "layer": picked["image2"],
            "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["english_fade"]}],
            "samples": [0.1, 0.75, 1.8],
            "expected": {"action_count": 1, "preset": "fade-in", "duration": 1.5},
        },
        {
            "id": "t09-end-fadeout-gap",
            "title": "end fade-out request exposes current selected-layer anchoring gap",
            "scope": "selected-layer",
            "layer": picked["shape"],
            "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["end_fadeout"]}],
            "samples": [0.1, 1.2, 4.8],
            "duration": 5.0,
            "expected": {"action_count": 1, "preset": "fade-out", "duration": 1.0, "should_anchor_to_end": True},
        },
        {
            "id": "t10-visibility-plus-motion",
            "title": "hide one layer, animate another, verify render still builds",
            "scope": "selected-layer",
            "visibility": {"hidden": picked["text"], "animated": picked["image"]},
            "prompt": PROMPTS["fade2"],
            "samples": [0.1, 1.0, 2.5],
            "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0},
        },
        {
            "id": "t11-whole-frame-complex",
            "title": "single complex whole-frame Russian prompt",
            "scope": "whole-frame",
            "prompt": PROMPTS["whole_complex"],
            "samples": [0.5, 2.5, 4.5],
            "expected": {
                "phases": {
                    "intro": {"preset": "white-bg-fade", "duration": 2.0},
                    "build": {"preset": "random-fly-in-stagger", "duration": 3.0},
                    "outro": {"preset": "full-frame-shatter", "duration": 3.0},
                }
            },
        },
        {
            "id": "t12-whole-frame-short",
            "title": "whole-frame Russian prompt with 1s/2s/2s timings",
            "scope": "whole-frame",
            "prompt": PROMPTS["whole_short"],
            "samples": [0.35, 1.6, 3.5],
            "expected": {
                "phases": {
                    "intro": {"preset": "white-bg-fade", "duration": 1.0},
                    "build": {"preset": "random-fly-in-stagger", "duration": 2.0},
                    "outro": {"preset": "full-frame-shatter", "duration": 2.0},
                }
            },
        },
        {
            "id": "t13-whole-frame-english",
            "title": "whole-frame English prompt with 1.5s/2.5s/2s timings",
            "scope": "whole-frame",
            "prompt": PROMPTS["whole_english"],
            "samples": [0.7, 2.4, 5.4],
            "expected": {
                "phases": {
                    "intro": {"preset": "white-bg-fade", "duration": 1.5},
                    "build": {"preset": "random-fly-in-stagger", "duration": 2.5},
                    "outro": {"preset": "full-frame-shatter", "duration": 2.0},
                }
            },
        },
    ]

    cases: list[dict[str, Any]] = []
    for spec in specs:
        case = dict(spec)
        case["layer_label"] = ""
        try:
            if spec["scope"] == "whole-frame":
                layers, plan, operations = apply_whole_frame(motion, spec["prompt"])
                case["layers"] = layers
                case["plan"] = plan
                case["recipe"] = None
                case["operations"] = operations
                case["phase_plan"] = plan
                if plan:
                    case["duration"] = max(float(motion.duration or 0), float(plan.get("minimum_duration") or 0), 5.0)
                    case["summary"] = ", ".join(f"{p.get('id')}:{p.get('preset')} {p.get('duration')}s" for p in plan.get("phases", []))
            elif "visibility" in spec:
                hidden = spec["visibility"]["hidden"]
                animated = spec["visibility"]["animated"]
                duration_hint = float(spec.get("duration") or motion.duration or 0)
                layers, recipe, operations = apply_visibility(base_layers, str(hidden["id"]), str(animated["id"]), spec["prompt"], timeline_duration=duration_hint)
                case["layers"] = layers
                case["recipe"] = recipe
                case["plan"] = None
                case["operations"] = operations
                case["layer_label"] = f"hidden={hidden.get('kind')}:{hidden.get('name')}; animated={animated.get('kind')}:{animated.get('name')}"
                case["fidelity_exclude_layers"] = [hidden, animated]
                case["actions"] = action_summary(recipe)
                explicit_duration = spec.get("duration")
                if explicit_duration is not None:
                    case["duration"] = max(float(explicit_duration), recipe_required_duration(recipe), 0.25)
                else:
                    case["duration"] = max(recipe_required_duration(recipe) + 1.0, 3.0)
                case["summary"] = f"actions={len(motion_recipe_actions(recipe))}; qa={(recipe.get('visual_qa') or {}).get('status') if recipe else 'none'}"
            else:
                target = spec["layer"]
                duration_hint = float(spec.get("duration") or motion.duration or 0)
                layers, recipe, operations = apply_selected_steps(base_layers, str(target["id"]), spec["steps"], timeline_duration=duration_hint)
                case["layers"] = layers
                case["recipe"] = recipe
                case["plan"] = None
                case["operations"] = operations
                case["prompt"] = " | ".join(step["prompt"] for step in spec["steps"] if step.get("prompt"))
                case["layer_label"] = f"{target.get('kind')}:{target.get('name') or target.get('id')}"
                case["fidelity_exclude_layers"] = [target]
                case["actions"] = action_summary(recipe)
                explicit_duration = spec.get("duration")
                if explicit_duration is not None:
                    case["duration"] = max(float(explicit_duration), recipe_required_duration(recipe), 0.25)
                else:
                    case["duration"] = max(recipe_required_duration(recipe) + 1.0, 3.0)
                case["summary"] = f"actions={len(motion_recipe_actions(recipe))}; qa={(recipe.get('visual_qa') or {}).get('status') if recipe else 'none'}"
                if recipe:
                    case["backend_samples"] = backend_samples(recipe, spec["samples"], float(case["duration"]))
            video, frames = render_case(motion, case, project_assets, out_dir)
            case["video"] = str(video) if video else ""
            case["frames"] = [str(path) for path in frames]
            if case.get("scope") == "selected-layer" and case.get("fidelity_exclude_layers"):
                case["pixel_fidelity"] = pixel_fidelity_outside_layers(
                    motion,
                    frames,
                    list(case.get("fidelity_exclude_layers") or []),
                    base / str(motion.asset_path or ""),
                )
                if case.get("summary"):
                    case["summary"] = f"{case['summary']}; px_diff={case['pixel_fidelity'].get('max_mean_rgb_diff')}"
            status, issues = evaluate_case(case)
            case["status"] = status
            case["issues"] = issues
        except Exception as exc:
            case["status"] = "fail"
            case["issues"] = [f"{type(exc).__name__}: {exc}"]
            case["video"] = ""
            case["frames"] = []
            case.setdefault("duration", 0)
        case.pop("layers", None)
        case.pop("recipe", None)
        case.pop("plan", None)
        case.pop("fidelity_exclude_layers", None)
        cases.append(case)
        print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}")

    contact_sheet = make_case_sheets(cases, out_dir)
    report = write_markdown_report(cases, out_dir, contact_sheet, args.project)
    summary = {
        "project": args.project,
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "total": len(cases),
        "pass": sum(1 for case in cases if case["status"] == "pass"),
        "fail": sum(1 for case in cases if case["status"] != "pass"),
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("report", "contact_sheet", "total", "pass", "fail")}, ensure_ascii=False, indent=2))
    if summary["fail"] > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
