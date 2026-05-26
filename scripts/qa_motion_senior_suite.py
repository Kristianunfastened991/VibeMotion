from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.motion import fit_motion_to_canvas
from app.services.motion_intent import (
    build_layer_motion_recipe_after_delete,
    build_layer_motion_recipe_from_prompt,
    motion_recipe_actions,
)
from app.services.projects import load_state, project_dir
from app.services.render import detect_video_size, render_project_preview
from scripts import qa_motion_random_suite as base_qa


PROMPTS = {
    "fade_0p8": "fade in over 0.8 seconds",
    "fade_1p2": "fade in over 1.2 seconds",
    "fade_2": "fade in over 2 seconds",
    "soft_slide": "\u043c\u044f\u0433\u043a\u043e \u0432\u043b\u0435\u0442\u0430\u0435\u0442 \u0441\u043b\u0435\u0432\u0430 \u0437\u0430 1.2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b",
    "drop_2p5": "at 2.5 seconds drop down like a stone",
    "drop_after": "after that wait one second then drop down like a stone",
    "end_fade_1": "\u0432 \u043a\u043e\u043d\u0446\u0435 \u0441\u0434\u0435\u043b\u0430\u0439 fade-out \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 1 \u0441\u0435\u043a\u0443\u043d\u0434\u0443",
    "longer_by_3": "\u0441\u0434\u0435\u043b\u0430\u0439 \u0434\u043b\u0438\u043d\u043d\u0435\u0435 \u043d\u0430 3 \u0441\u0435\u043a\u0443\u043d\u0434\u044b",
    "set_duration_3": "set duration to 3 seconds",
    "whole_fade_only": "First 1 second background only. Then all elements fly into place over 2 seconds in random order. At the end fade out the full frame over the last 1 second.",
    "whole_complex_alt": "First 2 seconds: background only. Then all layers fly into place over 3 seconds. In the last 3 seconds the whole frame shatters like glass and fades out.",
}


def layer_by_id(layers: list[dict[str, Any]], layer_id: str) -> dict[str, Any]:
    for layer in layers:
        if str(layer.get("id") or "") == str(layer_id):
            return layer
    raise KeyError(layer_id)


def is_root_layer(layer: dict[str, Any], motion: Any) -> bool:
    return str(layer.get("id") or "") == str(getattr(motion, "figma_node_id", "") or "") or (
        str(layer.get("node_type") or "").upper() == "FRAME"
        and abs(float(layer.get("x", 0) or 0)) < 0.001
        and abs(float(layer.get("y", 0) or 0)) < 0.001
    )


def layer_area(layer: dict[str, Any]) -> float:
    return max(0.0, float(layer.get("width") or 0) * float(layer.get("height") or 0))


def select_current_frame_targets(layers: list[dict[str, Any]], motion: Any) -> dict[str, str]:
    candidates = [
        layer
        for layer in layers
        if layer.get("visible") is not False and not is_root_layer(layer, motion) and layer_area(layer) > 4
    ]
    images = sorted(
        [layer for layer in candidates if layer.get("kind") == "image" and layer.get("asset_path")],
        key=layer_area,
        reverse=True,
    )
    texts = sorted(
        [layer for layer in candidates if layer.get("kind") == "text"],
        key=lambda layer: (
            -len(str(layer.get("text") or layer.get("name") or "")),
            float(layer.get("y") or 0),
        ),
    )
    title_texts = sorted(
        texts,
        key=lambda layer: (float(layer.get("y") or 0), float(layer.get("x") or 0)),
    )
    shapes = sorted(
        [
            layer
            for layer in candidates
            if layer.get("kind") == "shape"
            and str(layer.get("mask_role") or "") != "visual-mask"
            and not str(layer.get("id") or "").startswith("__frame_choreo_")
        ],
        key=layer_area,
    )
    fallback = candidates[0] if candidates else None

    def pick(collection: list[dict[str, Any]], index: int = 0) -> str:
        if collection:
            return str(collection[min(index, len(collection) - 1)].get("id") or "")
        if fallback:
            return str(fallback.get("id") or "")
        raise AssertionError("no visible Figma layer targets")

    return {
        "image_main": pick(images, 0),
        "image_secondary": pick(images, 1),
        "shape_main": pick(shapes, len(shapes) // 2 if shapes else 0),
        "shape_small": pick(shapes, 0),
        "text_body": pick(texts, 0),
        "text_title": pick(title_texts, 0),
    }


def apply_steps(layers: list[dict[str, Any]], target_id: str, steps: list[dict[str, Any]], timeline_duration: float) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    working = [dict(layer) for layer in layers]
    target = dict(layer_by_id(working, target_id))
    recipe: dict[str, Any] | None = target.get("motion_recipe") if isinstance(target.get("motion_recipe"), dict) else None
    operations: list[dict[str, Any]] = []
    for step in steps:
        if step["kind"] == "prompt":
            recipe = build_layer_motion_recipe_from_prompt(step["prompt"], step.get("mode", "replace"), target, working, timeline_duration=timeline_duration)
            target["motion_recipe"] = recipe
            operations.append({"kind": "prompt", "mode": step.get("mode", "replace"), "operation": recipe.get("motion_operation")})
        elif step["kind"] == "delete_last":
            actions = motion_recipe_actions(recipe)
            action_id = str((actions[-1] if actions else {}).get("id") or "")
            recipe, operation, found = build_layer_motion_recipe_after_delete(recipe, action_id, target)
            if recipe:
                target["motion_recipe"] = recipe
            else:
                target.pop("motion_recipe", None)
            operations.append({"kind": "delete_last", "found": found, "operation": operation})
        elif step["kind"] == "cancel":
            before = json.dumps(recipe or {}, sort_keys=True, ensure_ascii=False)
            after = json.dumps(recipe or {}, sort_keys=True, ensure_ascii=False)
            operations.append({"kind": "cancel", "mutated": before != after})
    return [target if str(layer.get("id") or "") == str(target_id) else layer for layer in working], recipe, operations


def apply_whole_frame(motion: Any, prompt: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    if not should_use_frame_choreography_prompt(prompt):
        raise AssertionError("whole-frame prompt did not route to choreography")
    planning_motion = motion.model_copy(update={"figma_layers": base_qa.clean_layers(list(motion.figma_layers or []))})
    layers = plan_frame_choreography(prompt, planning_motion)
    plan = describe_motion_plan(layers)
    if not isinstance(plan, dict):
        raise AssertionError("missing whole-frame phase plan")
    from app.services.motion_intent import attach_frame_motion_contract as attach_contract

    layers = attach_contract(prompt, str(motion.id), layers, plan)
    return layers, describe_motion_plan(layers) or plan, [{"kind": "whole-frame", "operation": (describe_motion_plan(layers) or {}).get("motion_operation")}]


def case_status(case: dict[str, Any]) -> tuple[str, list[str]]:
    status, issues = base_qa.evaluate_case(case)
    for operation in case.get("operations") or []:
        if operation.get("kind") == "cancel" and operation.get("mutated"):
            issues.append("cancel mutated recipe")
    expected = case.get("expected") or {}
    operation_type = expected.get("operation_type")
    if operation_type and case.get("recipe"):
        actual = ((case["recipe"].get("motion_operation") or {}).get("type"))
        if actual != operation_type:
            issues.append(f"operation_type {actual} != {operation_type}")
    return ("pass" if not issues else "fail"), issues


def final_render_parity(project_id: str, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(project_id)
    base = project_dir(project_id)
    preview = render_project_preview(state, base)
    motion = next(item for item in state.motions if item.source_type == "figma" and item.video_asset_path)
    canvas_w, canvas_h = detect_video_size(base / state.source_video)
    fitted = fit_motion_to_canvas(motion, canvas_w, canvas_h)
    checks = []
    for local_t in [3.5, 5.0]:
        abs_t = float(fitted.start) + local_t
        final_frame = out_dir / f"final_render_{str(local_t).replace('.', 'p')}s.png"
        motion_frame = out_dir / f"motion_asset_{str(local_t).replace('.', 'p')}s.png"
        subprocess.run(["ffmpeg", "-y", "-ss", f"{abs_t:.3f}", "-i", str(preview), "-frames:v", "1", "-q:v", "2", str(final_frame)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-y", "-ss", f"{local_t:.3f}", "-i", str(base / motion.video_asset_path), "-frames:v", "1", "-q:v", "2", str(motion_frame)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        final_img = Image.open(final_frame).convert("RGB")
        motion_img = Image.open(motion_frame).convert("RGB").resize((int(fitted.width), int(fitted.height)), Image.Resampling.LANCZOS)
        x, y, w, h = int(fitted.x), int(fitted.y), int(fitted.width), int(fitted.height)
        crop = final_img.crop((x, y, x + w, y + h))
        diff = ImageChops.difference(crop, motion_img)
        stat = ImageStat.Stat(diff)
        mae = float(sum(stat.mean) / max(1, len(stat.mean)))
        diff_boost = ImageEnhance.Brightness(diff).enhance(5.0)
        compare = Image.new("RGB", (w * 3, h + 34), "white")
        compare.paste(motion_img, (0, 34))
        compare.paste(crop, (w, 34))
        compare.paste(diff_boost, (w * 2, 34))
        draw = ImageDraw.Draw(compare)
        for index, title in enumerate(["Motion MP4 scaled", "Final render crop", "Diff x5"]):
            draw.text((index * w + 8, 10), title, fill=(0, 0, 0))
        compare_path = out_dir / f"final_render_parity_{str(local_t).replace('.', 'p')}s.png"
        compare.save(compare_path)
        checks.append({"local_time": local_t, "mean_abs_diff": round(mae, 3), "compare": str(compare_path), "status": "pass" if mae <= 18 else "fail"})
    return {"status": "pass" if all(item["status"] == "pass" for item in checks) else "fail", "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="test-b5b1e836")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    state = load_state(args.project)
    motion = next((item for item in state.motions if item.source_type == "figma" and item.figma_layers), None)
    if motion is None:
        raise SystemExit("No Figma motion found")
    layers = base_qa.clean_layers(list(motion.figma_layers or []))
    targets = select_current_frame_targets(layers, motion)
    base = project_dir(args.project)
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-senior-suite-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    project_assets = base / "assets"

    specs = [
        {"id": "s01-masked-main-image-fade", "title": "masked main image fade-in", "scope": "selected-layer", "layer_id": targets["image_main"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_1p2"]}], "samples": [0.1, 0.6, 1.5], "expected": {"action_count": 1, "preset": "fade-in", "duration": 1.2}},
        {"id": "s02-center-overlay-soft-slide", "title": "center overlay image soft slide", "scope": "selected-layer", "layer_id": targets["image_secondary"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["soft_slide"]}], "samples": [0.1, 0.7, 1.5], "expected": {"action_count": 1, "preset": "soft-slide"}},
        {"id": "s03-orange-card-drop-2p5", "title": "shape/card drops at 2.5s", "scope": "selected-layer", "layer_id": targets["shape_main"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["drop_2p5"]}], "samples": [2.4, 2.9, 3.7], "duration": 4.2, "expected": {"action_count": 1, "preset": "gravity-drop-fade", "start": 2.5}},
        {"id": "s04-badge-bg-fade", "title": "small badge/background fade-in", "scope": "selected-layer", "layer_id": targets["shape_small"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_0p8"]}], "samples": [0.1, 0.4, 1.0], "expected": {"action_count": 1, "preset": "fade-in", "duration": 0.8}},
        {"id": "s05-body-text-end-fade", "title": "body text fades at end", "scope": "selected-layer", "layer_id": targets["text_body"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["end_fade_1"]}], "samples": [0.2, 5.2, 5.9], "duration": 6.0, "expected": {"action_count": 1, "preset": "fade-out", "duration": 1.0, "start": 5.0}},
        {"id": "s06-add-end-fade-after-fadein", "title": "append fade-out as separate action", "scope": "selected-layer", "layer_id": targets["text_title"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["end_fade_1"]}], "samples": [1.0, 5.2, 5.9], "duration": 6.0, "expected": {"action_count": 2, "preset": "fade-out", "duration": 1.0, "start": 5.0}},
        {"id": "s07-longer-by-3-adds-time", "title": "longer by 3 seconds adds to action duration", "scope": "selected-layer", "layer_id": targets["image_main"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["longer_by_3"]}], "samples": [1.0, 3.2, 5.3], "duration": 6.0, "expected": {"action_count": 1, "preset": "fade-in", "duration": 5.0, "operation_type": "modify"}},
        {"id": "s08-set-duration-3-replaces-duration", "title": "set duration to 3 seconds is absolute", "scope": "selected-layer", "layer_id": targets["image_main"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["set_duration_3"]}], "samples": [0.8, 2.2, 3.4], "duration": 4.5, "expected": {"action_count": 1, "preset": "fade-in", "duration": 3.0, "operation_type": "modify"}},
        {"id": "s09-delete-last-action", "title": "delete last action keeps earlier fade", "scope": "selected-layer", "layer_id": targets["image_main"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "prompt", "mode": "append", "prompt": PROMPTS["drop_after"]}, {"kind": "delete_last"}], "samples": [0.6, 1.5, 3.3], "duration": 4.5, "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0}},
        {"id": "s10-cancel-no-op", "title": "cancel does not mutate existing stack", "scope": "selected-layer", "layer_id": targets["text_title"], "steps": [{"kind": "prompt", "mode": "replace", "prompt": PROMPTS["fade_2"]}, {"kind": "cancel"}], "samples": [0.1, 1.0, 2.4], "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0}},
        {"id": "s11-whole-frame-fade-only", "title": "whole frame pure fade-out does not become gravity drop", "scope": "whole-frame", "prompt": PROMPTS["whole_fade_only"], "samples": [0.5, 2.0, 5.0], "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 1.0}, "build": {"preset": "random-fly-in-stagger", "duration": 2.0}, "outro": {"preset": "full-frame-fade-out", "duration": 1.0}}}},
        {"id": "s12-whole-frame-complex-alt", "title": "whole frame shatter/fade complex English", "scope": "whole-frame", "prompt": PROMPTS["whole_complex_alt"], "samples": [0.5, 2.5, 4.8], "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 2.0}, "build": {"preset": "random-fly-in-stagger", "duration": 3.0}, "outro": {"preset": "full-frame-shatter", "duration": 3.0}}}},
    ]

    cases: list[dict[str, Any]] = []
    for spec in specs:
        case = dict(spec)
        try:
            if spec["scope"] == "whole-frame":
                case["layers"], case["plan"], case["operations"] = apply_whole_frame(motion, spec["prompt"])
                case["phase_plan"] = case["plan"]
                case["recipe"] = None
                case["duration"] = max(float(motion.duration or 0), float(case["plan"].get("minimum_duration") or 0), 5.0)
                case["summary"] = ", ".join(f"{p.get('id')}:{p.get('preset')} {p.get('duration')}s" for p in case["plan"].get("phases", []))
            else:
                target = layer_by_id(layers, spec["layer_id"])
                case["layers"], case["recipe"], case["operations"] = apply_steps(layers, spec["layer_id"], spec["steps"], float(spec.get("duration") or motion.duration or 6))
                case["layer_label"] = f"{target.get('kind')}:{target.get('name') or target.get('id')}"
                case["duration"] = max(float(spec.get("duration") or 0), base_qa.recipe_required_duration(case["recipe"]) + 1.0, 3.0)
                case["actions"] = base_qa.action_summary(case["recipe"])
                case["summary"] = f"actions={len(motion_recipe_actions(case['recipe']))}; qa={(case['recipe'].get('visual_qa') or {}).get('status') if case['recipe'] else 'none'}"
                case["fidelity_exclude_layers"] = [target]
            video, frames = base_qa.render_case(motion, case, project_assets, out_dir)
            case["video"] = str(video) if video else ""
            case["frames"] = [str(path) for path in frames]
            if case["scope"] == "selected-layer":
                case["pixel_fidelity"] = base_qa.pixel_fidelity_outside_layers(motion, frames, case["fidelity_exclude_layers"], base / str(motion.asset_path or ""))
                case["summary"] = f"{case['summary']}; px_diff={case['pixel_fidelity'].get('max_mean_rgb_diff')}"
            case["status"], case["issues"] = case_status(case)
        except Exception as exc:
            case["status"] = "fail"
            case["issues"] = [f"{type(exc).__name__}: {exc}"]
            case["frames"] = []
            case["video"] = ""
        for key in ["layers", "recipe", "plan", "fidelity_exclude_layers"]:
            case.pop(key, None)
        cases.append(case)
        print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}")

    parity = final_render_parity(args.project, out_dir / "final-render-parity")
    contact_sheet = base_qa.make_case_sheets(cases, out_dir)
    report = base_qa.write_markdown_report(cases, out_dir, contact_sheet, args.project)
    summary = {
        "project": args.project,
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "total": len(cases),
        "pass": sum(1 for case in cases if case["status"] == "pass"),
        "fail": sum(1 for case in cases if case["status"] != "pass"),
        "final_render_parity": parity,
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ["report", "contact_sheet", "total", "pass", "fail", "final_render_parity"]}, ensure_ascii=False, indent=2))
    if summary["fail"] or parity["status"] != "pass":
        sys.exit(2)


if __name__ == "__main__":
    main()
