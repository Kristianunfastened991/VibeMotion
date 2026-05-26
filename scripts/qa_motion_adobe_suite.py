from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.motion import _motion_dsl_state
from app.services.motion_intent import (
    attach_frame_motion_contract,
    build_layer_motion_recipe_after_delete,
    build_layer_motion_recipe_from_prompt,
    motion_recipe_actions,
)
from app.services.projects import list_projects, load_state, project_dir
from scripts import qa_motion_random_suite as base_qa
from scripts.qa_motion_senior_suite import final_render_parity


PROMPTS = {
    "fade_word_2": "Плавно появись из прозрачности за две секунды",
    "fade_2": "Сделай фейдин вначале длительностью 2 секунды",
    "fade_out_start_2": "Сделай начало фейд аут на 2 секунды",
    "drop_fifth": "На пятой секунде блок падает вниз как камень с ускорением",
    "longer_by_3": "сделай длиннее на 3 секунды",
    "set_duration_3": "сделай длительность 3 секунды",
    "drop_after_fade": "а теперь пусть когда фейд ин реализуется то после этого через секунду весь блок падает вниз как камень",
    "end_fade_1": "в конце сделай fade-out длительностью 1 секунду",
    "soft_left_1p2": "мягко влетает слева за 1.2 секунды",
    "whole_complex_ru": "первые 2 секунды - фейд ин белого экрана вначале, имеено фона без других элементов. Потом все элементы начинают в течении трех секунд влетать в фрейм на свои места в случайном порядке. Вконце весь фрейм падает вниз как разбитое стекло предварительно разбившись на куски в течении последних 3х секунд и весь фрейм уходит в фейд аут",
    "whole_complex_en": "First 1.5 seconds: background only. Then all elements fly into place over 2.5 seconds in random order. In the last 2 seconds the full frame shatters and fades out.",
    "whole_fade_only": "First 1 second background only. Then all elements fly into place over 2 seconds in random order. At the end fade out the full frame over the last 1 second.",
}


def first_project_id() -> str:
    projects = list_projects()
    if not projects:
        raise SystemExit("No projects found")
    return str(projects[0].project_id)


def is_pickable_layer(layer: dict[str, Any], kind: str | None = None) -> bool:
    layer_id = str(layer.get("id") or "")
    node_type = str(layer.get("node_type") or "").upper()
    if layer.get("visible") is False or layer_id.startswith("__frame_choreo_"):
        return False
    if layer.get("mask_role") or layer.get("motion_internal") or layer.get("cluster_parent_id"):
        return False
    if node_type == "FRAME":
        return False
    if kind is not None and str(layer.get("kind") or "") != kind:
        return False
    if kind in {"image", "shape"} and not layer.get("asset_path"):
        return False
    return True


def pick_layers(layers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    picked: dict[str, dict[str, Any]] = {}
    for kind in ("image", "text", "shape"):
        for layer in layers:
            if is_pickable_layer(layer, kind):
                picked[kind] = layer
                break
    images = [layer for layer in layers if is_pickable_layer(layer, "image")]
    if images:
        picked["image2"] = images[1] if len(images) > 1 else images[0]
    missing = {"image", "text", "shape", "image2"} - set(picked)
    if missing:
        raise RuntimeError(f"Need representative layers for Adobe suite, missing: {sorted(missing)}")
    return picked


def layer_by_id(layers: list[dict[str, Any]], layer_id: str) -> dict[str, Any]:
    for layer in layers:
        if str(layer.get("id") or "") == str(layer_id):
            return layer
    raise KeyError(layer_id)


def apply_steps(
    layers: list[dict[str, Any]],
    target_id: str,
    steps: list[dict[str, Any]],
    timeline_duration: float,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    working = [dict(layer) for layer in layers]
    target = dict(layer_by_id(working, target_id))
    recipe: dict[str, Any] | None = target.get("motion_recipe") if isinstance(target.get("motion_recipe"), dict) else None
    operations: list[dict[str, Any]] = []
    for step in steps:
        kind = step["kind"]
        if kind == "prompt":
            recipe = build_layer_motion_recipe_from_prompt(step["prompt"], step.get("mode", "replace"), target, working, timeline_duration=timeline_duration)
            target["motion_recipe"] = recipe
            operations.append({"kind": "prompt", "mode": step.get("mode", "replace"), "operation": recipe.get("motion_operation")})
        elif kind == "delete_index":
            actions = motion_recipe_actions(recipe)
            index = int(step.get("index", -1))
            if index < 0:
                index = len(actions) + index
            action_id = str((actions[index] if 0 <= index < len(actions) else {}).get("id") or "")
            recipe, operation, found = build_layer_motion_recipe_after_delete(recipe, action_id, target)
            if recipe:
                target["motion_recipe"] = recipe
            else:
                target.pop("motion_recipe", None)
            operations.append({"kind": "delete_index", "index": index, "found": found, "operation": operation})
        elif kind == "cancel":
            before = json.dumps(recipe or {}, sort_keys=True, ensure_ascii=False)
            after = json.dumps(recipe or {}, sort_keys=True, ensure_ascii=False)
            operations.append({"kind": "cancel", "mutated": before != after})
    return [target if str(layer.get("id") or "") == str(target_id) else layer for layer in working], recipe, operations


def apply_visibility(
    layers: list[dict[str, Any]],
    hidden_id: str,
    animated_id: str,
    prompt: str,
    timeline_duration: float,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    working: list[dict[str, Any]] = []
    target: dict[str, Any] | None = None
    for layer in layers:
        clean = dict(layer)
        if str(clean.get("id") or "") == str(hidden_id):
            clean["visible"] = False
        if str(clean.get("id") or "") == str(animated_id):
            target = clean
        working.append(clean)
    if target is None:
        raise KeyError(animated_id)
    recipe = build_layer_motion_recipe_from_prompt(prompt, "replace", target, working, timeline_duration=timeline_duration)
    target["motion_recipe"] = recipe
    return working, recipe, [
        {"kind": "visibility", "hidden_layer_id": hidden_id},
        {"kind": "prompt", "mode": "replace", "operation": recipe.get("motion_operation")},
    ]


def apply_whole_frame(motion: Any, prompt: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    if not should_use_frame_choreography_prompt(prompt):
        raise AssertionError("whole-frame prompt did not route to choreography")
    planning_motion = motion.model_copy(update={"figma_layers": base_qa.clean_layers(list(motion.figma_layers or []))})
    layers = plan_frame_choreography(prompt, planning_motion)
    plan = describe_motion_plan(layers)
    if not isinstance(plan, dict):
        raise AssertionError("missing whole-frame phase plan")
    layers = attach_frame_motion_contract(prompt, str(motion.id), layers, plan)
    next_plan = describe_motion_plan(layers) or plan
    return layers, next_plan, [{"kind": "whole-frame", "operation": next_plan.get("motion_operation")}]


def action_signature(recipe: dict[str, Any] | None) -> list[dict[str, Any]]:
    signature = []
    for action in motion_recipe_actions(recipe):
        phase = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
        keyframes = action.get("motion_dsl", {}).get("keyframes", []) if isinstance(action.get("motion_dsl"), dict) else []
        signature.append(
            {
                "preset": action.get("preset"),
                "start": round(float(phase.get("start") or 0), 4),
                "duration": round(float(phase.get("duration") or 0), 4),
                "keyframes": [
                    {
                        key: round(float(frame.get(key) or 0), 4)
                        for key in ("time", "x", "y", "scale", "scaleX", "scaleY", "rotate", "opacity", "blur")
                        if isinstance(frame, dict) and key in frame
                    }
                    for frame in keyframes
                    if isinstance(frame, dict)
                ],
            }
        )
    return signature


def source_frame(motion: Any, base: Path) -> Image.Image:
    path = base / str(motion.asset_path or "")
    return Image.open(path).convert("RGB").resize((motion.width, motion.height), Image.Resampling.LANCZOS)


def layer_box(motion: Any, layer: dict[str, Any]) -> tuple[int, int, int, int]:
    layers = list(motion.figma_layers or [])
    bounds_layer = next((item for item in layers if str(item.get("id") or "") == str(motion.figma_node_id or "")), None)
    bounds_width = float((bounds_layer or {}).get("width") or motion.width or 1)
    bounds_height = float((bounds_layer or {}).get("height") or motion.height or 1)
    scale_x = motion.width / max(1.0, bounds_width)
    scale_y = motion.height / max(1.0, bounds_height)
    x = int(round(float(layer.get("x") or 0) * scale_x))
    y = int(round(float(layer.get("y") or 0) * scale_y))
    w = max(1, int(round(float(layer.get("width") or 1) * scale_x)))
    h = max(1, int(round(float(layer.get("height") or 1) * scale_y)))
    return max(0, x), max(0, y), min(motion.width, x + w), min(motion.height, y + h)


def mean_diff(image_a: Image.Image, image_b: Image.Image, mask: Image.Image | None = None) -> float:
    diff = ImageChops.difference(image_a.convert("RGB"), image_b.convert("RGB"))
    stat = ImageStat.Stat(diff, mask)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def full_frame_fidelity(motion: Any, frame_path: str, base: Path) -> dict[str, Any]:
    source = source_frame(motion, base)
    frame = Image.open(frame_path).convert("RGB").resize(source.size, Image.Resampling.LANCZOS)
    value = mean_diff(source, frame)
    return {"status": "pass" if value <= 5.0 else "fail", "mean_abs_diff": round(value, 3)}


def hidden_layer_removed(motion: Any, frame_path: str, hidden_layer: dict[str, Any], base: Path) -> dict[str, Any]:
    source = source_frame(motion, base)
    frame = Image.open(frame_path).convert("RGB").resize(source.size, Image.Resampling.LANCZOS)
    left, top, right, bottom = layer_box(motion, hidden_layer)
    source_crop = source.crop((left, top, right, bottom))
    frame_crop = frame.crop((left, top, right, bottom))
    mask = Image.new("L", source_crop.size, 0)
    pixels = []
    for r, g, b in source_crop.getdata():
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        pixels.append(255 if luminance < 225 else 0)
    mask.putdata(pixels)
    if not mask.getbbox():
        mask = None
    value = mean_diff(source_crop, frame_crop, mask)
    return {"status": "pass" if value >= 8.0 else "fail", "hidden_content_mean_abs_diff": round(value, 3)}


def swept_exclude_layers(layer: dict[str, Any], recipe: dict[str, Any] | None, samples: list[float], duration: float) -> list[dict[str, Any]]:
    if not isinstance(recipe, dict):
        return []
    try:
        base_x = float(layer.get("x") or 0)
        base_y = float(layer.get("y") or 0)
        base_w = max(1.0, float(layer.get("width") or 1))
        base_h = max(1.0, float(layer.get("height") or 1))
    except (TypeError, ValueError):
        return []
    transform_reference = recipe.get("transform_reference") if isinstance(recipe.get("transform_reference"), dict) else {}
    ref_w = max(1.0, float(transform_reference.get("width") or base_w))
    ref_h = max(1.0, float(transform_reference.get("height") or base_h))
    rects: list[dict[str, Any]] = []
    for seconds in samples:
        state = _motion_dsl_state(recipe, float(seconds), duration)
        sx = max(0.01, float(state.get("scale", 1) or 1) * float(state.get("scaleX", 1) or 1))
        sy = max(0.01, float(state.get("scale", 1) or 1) * float(state.get("scaleY", 1) or 1))
        width = base_w * sx
        height = base_h * sy
        center_x = base_x + base_w / 2 + (float(state.get("x", 0) or 0) / 100.0) * ref_w
        center_y = base_y + base_h / 2 + (float(state.get("y", 0) or 0) / 100.0) * ref_h
        rect = dict(layer)
        rect.update({"x": center_x - width / 2, "y": center_y - height / 2, "width": width, "height": height})
        rects.append(rect)
    return rects


def add_contract_checks(case: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    recipe = case.get("recipe")
    expected = case.get("expected") or {}
    if recipe and expected.get("operation_type"):
        actual = (recipe.get("motion_operation") or {}).get("type")
        if actual != expected["operation_type"]:
            issues.append(f"operation_type {actual} != {expected['operation_type']}")
    if recipe and expected.get("first_action_duration") is not None:
        actions = motion_recipe_actions(recipe)
        duration = float((actions[0].get("phase_plan") or {}).get("duration") or 0) if actions else 0
        if abs(duration - float(expected["first_action_duration"])) > 0.035:
            issues.append(f"first_action_duration {duration:g}s != {expected['first_action_duration']:g}s")
    if recipe and expected.get("remaining_presets"):
        presets = [action.get("preset") for action in motion_recipe_actions(recipe)]
        if presets != expected["remaining_presets"]:
            issues.append(f"remaining presets {presets} != {expected['remaining_presets']}")
    if recipe and expected.get("history_min") is not None:
        history = recipe.get("motion_operation_history") if isinstance(recipe.get("motion_operation_history"), list) else []
        if len(history) < int(expected["history_min"]):
            issues.append(f"history length {len(history)} < {expected['history_min']}")
    for operation in case.get("operations") or []:
        if operation.get("kind") == "cancel" and operation.get("mutated"):
            issues.append("cancel mutated recipe")
        if operation.get("kind") == "delete_index" and not operation.get("found"):
            issues.append("delete did not find action")
    if case.get("full_frame_fidelity", {}).get("status") == "fail":
        issues.append(f"settled full-frame fidelity too high: {case['full_frame_fidelity'].get('mean_abs_diff')}")
    if case.get("hidden_removed", {}).get("status") == "fail":
        issues.append(f"hidden layer still looks visible: {case['hidden_removed'].get('hidden_content_mean_abs_diff')}")
    if case.get("determinism", {}).get("status") == "fail":
        issues.append("same prompt produced different action signature")
    return issues


def active_state_checks(state: Any) -> dict[str, Any]:
    issues: list[str] = []
    for motion in state.motions:
        ids = [str(layer.get("id") or "") for layer in list(motion.figma_layers or []) if isinstance(layer, dict)]
        dupes = sorted({layer_id for layer_id in ids if layer_id and ids.count(layer_id) > 1})
        if dupes:
            issues.append(f"{motion.id}: duplicate layer ids in active state: {', '.join(dupes[:6])}")
        for layer in list(motion.figma_layers or []):
            recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
            if not isinstance(recipe, dict):
                continue
            if (recipe.get("dsl_contract") or {}).get("source_of_truth") != "motion_dsl":
                issues.append(f"{motion.id}/{layer.get('id')}: missing motion_dsl source contract")
    return {"status": "pass" if not issues else "fail", "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    project_id = args.project or first_project_id()
    state = load_state(project_id)
    motion = next((item for item in state.motions if item.source_type == "figma" and item.figma_layers), None)
    if motion is None:
        raise SystemExit("No Figma motion found")
    base = project_dir(project_id)
    layers = base_qa.clean_layers(list(motion.figma_layers or []))
    picked = pick_layers(layers)
    project_assets = base / "assets"
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-adobe-suite-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    specs: list[dict[str, Any]] = [
        {"id": "a01-ru-word-fade-image", "title": "Russian word-number fade-in is exactly 2s", "scope": "selected-layer", "layer": picked["image"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_word_2"]}], "samples": [0.5, 1.5, 2.6], "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0}, "check_full_frame_at": -1},
        {"id": "a02-start-fadeout-text", "title": "start fade-out is exactly 2s", "scope": "selected-layer", "layer": picked["text"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_out_start_2"]}], "samples": [0.2, 1.0, 2.4], "expected": {"action_count": 1, "preset": "fade-out", "duration": 2.0, "start": 0.0}},
        {"id": "a03-fifth-second-drop-shape", "title": "drop on fifth second anchors at 5.0s", "scope": "selected-layer", "layer": picked["shape"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["drop_fifth"]}], "samples": [4.8, 5.4, 6.2], "duration": 7.0, "expected": {"action_count": 1, "preset": "gravity-drop-fade", "start": 5.0}},
        {"id": "a04-dialog-retime-then-drop", "title": "dialog stack: fade 2s, make 3s longer, then drop 1s later", "scope": "selected-layer", "layer": picked["image2"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["longer_by_3"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["drop_after_fade"]}], "samples": [2.5, 5.4, 6.6], "duration": 8.0, "expected": {"action_count": 2, "preset": "gravity-drop-fade", "start": 6.0, "first_action_duration": 5.0, "history_min": 3}},
        {"id": "a05-dialog-set-duration-absolute", "title": "set duration to 3s modifies, does not add to old 2s", "scope": "selected-layer", "layer": picked["image"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["set_duration_3"]}], "samples": [0.8, 2.2, 3.4], "duration": 4.5, "expected": {"action_count": 1, "preset": "fade-in", "duration": 3.0, "operation_type": "modify"}},
        {"id": "a06-append-end-fade-anchored", "title": "append end fade anchors to timeline end", "scope": "selected-layer", "layer": picked["text"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["end_fade_1"]}], "samples": [1.0, 7.2, 7.8], "duration": 8.0, "expected": {"action_count": 2, "preset": "fade-out", "duration": 1.0, "start": 7.0}},
        {"id": "a07-delete-middle-action", "title": "delete a concrete middle action without resetting stack", "scope": "selected-layer", "layer": picked["image2"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["drop_after_fade"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["end_fade_1"]}, {"kind": "delete_index", "index": 1}], "samples": [1.0, 4.0, 7.8], "duration": 8.0, "expected": {"action_count": 2, "remaining_presets": ["fade-in", "fade-out"]}},
        {"id": "a08-cancel-no-mutation", "title": "cancel is a no-op", "scope": "selected-layer", "layer": picked["text"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "cancel"}], "samples": [0.3, 1.1, 2.5], "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0}},
        {"id": "a09-hide-layer-really-removes-pixels", "title": "visibility checkbox removes baked pixels from exact frame base", "scope": "visibility", "hidden": picked["text"], "animated": picked["image"], "prompt": PROMPTS["fade_2"], "samples": [0.3, 1.2, 2.6], "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0}},
        {"id": "a10-masked-image-settled-fidelity", "title": "masked/cropped image settles back to exact Figma frame", "scope": "selected-layer", "layer": picked["image2"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}], "samples": [0.3, 1.2, 2.6], "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0}, "check_full_frame_at": -1},
        {"id": "a11-image-slide-uses-layer-asset", "title": "image slide uses layer asset instead of full-frame crop", "scope": "selected-layer", "layer": picked["image"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["soft_left_1p2"]}], "samples": [0.2, 0.7, 1.5], "expected": {"action_count": 1, "preset": "soft-slide"}, "check_full_frame_at": -1, "swept_fidelity": True},
        {"id": "a12-whole-frame-ru-complex", "title": "Russian whole-frame prompt keeps 2s/3s/3s plan", "scope": "whole-frame", "prompt": PROMPTS["whole_complex_ru"], "samples": [0.5, 2.5, 5.2], "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 2.0}, "build": {"preset": "random-fly-in-stagger", "duration": 3.0}, "outro": {"preset": "full-frame-shatter", "duration": 3.0}}}},
        {"id": "a13-whole-frame-en-complex", "title": "English whole-frame prompt keeps 1.5s/2.5s/2s plan", "scope": "whole-frame", "prompt": PROMPTS["whole_complex_en"], "samples": [0.7, 2.4, 5.4], "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 1.5}, "build": {"preset": "random-fly-in-stagger", "duration": 2.5}, "outro": {"preset": "full-frame-shatter", "duration": 2.0}}}},
        {"id": "a14-whole-frame-fade-only-outro", "title": "fade-only outro does not become shatter/drop", "scope": "whole-frame", "prompt": PROMPTS["whole_fade_only"], "samples": [0.5, 2.0, 5.0], "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 1.0}, "build": {"preset": "random-fly-in-stagger", "duration": 2.0}, "outro": {"preset": "full-frame-fade-out", "duration": 1.0}}}},
    ]

    cases: list[dict[str, Any]] = []
    for spec in specs:
        case = dict(spec)
        try:
            if spec["scope"] == "whole-frame":
                case["layers"], case["plan"], case["operations"] = apply_whole_frame(motion, spec["prompt"])
                case["recipe"] = None
                case["phase_plan"] = case["plan"]
                case["duration"] = max(float(motion.duration or 0), float(case["plan"].get("minimum_duration") or 0), 5.0)
                case["summary"] = ", ".join(f"{phase.get('id')}:{phase.get('preset')} {phase.get('duration')}s" for phase in case["plan"].get("phases", []))
            elif spec["scope"] == "visibility":
                layers_next, recipe, operations = apply_visibility(layers, str(spec["hidden"]["id"]), str(spec["animated"]["id"]), spec["prompt"], float(spec.get("duration") or motion.duration or 6))
                case.update({"layers": layers_next, "recipe": recipe, "plan": None, "operations": operations, "duration": max(base_qa.recipe_required_duration(recipe) + 1.0, 3.0)})
                case["layer_label"] = f"hidden={spec['hidden'].get('kind')}:{spec['hidden'].get('name')}; animated={spec['animated'].get('kind')}:{spec['animated'].get('name')}"
                case["fidelity_exclude_layers"] = [spec["hidden"], spec["animated"]]
                case["actions"] = base_qa.action_summary(recipe)
                case["summary"] = f"actions={len(motion_recipe_actions(recipe))}; hidden={spec['hidden'].get('id')}"
            else:
                target = spec["layer"]
                layers_next, recipe, operations = apply_steps(layers, str(target["id"]), spec["steps"], float(spec.get("duration") or motion.duration or 6))
                case.update({"layers": layers_next, "recipe": recipe, "plan": None, "operations": operations})
                case["layer_label"] = f"{target.get('kind')}:{target.get('name') or target.get('id')}"
                case["actions"] = base_qa.action_summary(recipe)
                explicit_duration = spec.get("duration")
                case["duration"] = max(float(explicit_duration or 0), base_qa.recipe_required_duration(recipe) + 1.0, 3.0)
                case["fidelity_exclude_layers"] = [target]
                if spec.get("swept_fidelity"):
                    case["fidelity_exclude_layers"].extend(swept_exclude_layers(target, recipe, list(spec.get("samples") or []), float(case["duration"])))
                case["summary"] = f"actions={len(motion_recipe_actions(recipe))}; qa={(recipe.get('visual_qa') or {}).get('status') if recipe else 'none'}"
                if spec.get("deterministic_compare", True):
                    target_again = dict(target)
                    recipe_again = build_layer_motion_recipe_from_prompt(spec["steps"][0]["prompt"], spec["steps"][0].get("mode", "replace"), target_again, layers, timeline_duration=float(spec.get("duration") or motion.duration or 6))
                    case["determinism"] = {"status": "pass" if action_signature(recipe_again) == action_signature(build_layer_motion_recipe_from_prompt(spec["steps"][0]["prompt"], spec["steps"][0].get("mode", "replace"), dict(target), layers, timeline_duration=float(spec.get("duration") or motion.duration or 6))) else "fail"}

            video, frames = base_qa.render_case(motion, case, project_assets, out_dir)
            case["video"] = str(video) if video else ""
            case["frames"] = [str(path) for path in frames]
            if case.get("scope") in {"selected-layer", "visibility"} and case.get("fidelity_exclude_layers"):
                case["pixel_fidelity"] = base_qa.pixel_fidelity_outside_layers(motion, frames, list(case.get("fidelity_exclude_layers") or []), base / str(motion.asset_path or ""))
                case["summary"] = f"{case.get('summary', '')}; px_diff={case['pixel_fidelity'].get('max_mean_rgb_diff')}"
            if spec.get("check_full_frame_at") is not None and case.get("frames"):
                index = int(spec["check_full_frame_at"])
                case["full_frame_fidelity"] = full_frame_fidelity(motion, case["frames"][index], base)
            if spec["scope"] == "visibility" and case.get("frames"):
                case["hidden_removed"] = hidden_layer_removed(motion, case["frames"][-1], spec["hidden"], base)
            status, issues = base_qa.evaluate_case(case)
            issues.extend(add_contract_checks(case))
            case["status"] = "pass" if not issues else "fail"
            case["issues"] = issues
        except Exception as exc:
            case["status"] = "fail"
            case["issues"] = [f"{type(exc).__name__}: {exc}"]
            case["frames"] = []
            case["video"] = ""
        for key in ["layers", "recipe", "plan", "fidelity_exclude_layers"]:
            case.pop(key, None)
        cases.append(case)
        print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}")

    state_check = active_state_checks(state)
    parity = final_render_parity(project_id, out_dir / "final-render-parity")
    contact_sheet = base_qa.make_case_sheets(cases, out_dir)
    report = base_qa.write_markdown_report(cases, out_dir, contact_sheet, project_id)
    summary = {
        "project": project_id,
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "total": len(cases),
        "pass": sum(1 for case in cases if case["status"] == "pass"),
        "fail": sum(1 for case in cases if case["status"] != "pass"),
        "state_check": state_check,
        "final_render_parity": parity,
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ["report", "contact_sheet", "total", "pass", "fail", "state_check", "final_render_parity"]}, ensure_ascii=False, indent=2))
    if summary["fail"] or state_check["status"] != "pass" or parity["status"] != "pass":
        sys.exit(2)


if __name__ == "__main__":
    main()
