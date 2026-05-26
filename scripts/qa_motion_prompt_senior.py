from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.figma_plugin import motion_from_plugin_asset  # noqa: E402
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt  # noqa: E402
from app.services.motion_intent import attach_frame_motion_contract, build_layer_motion_recipe_from_prompt, motion_recipe_actions  # noqa: E402
from app.services.projects import load_state, project_dir  # noqa: E402
from scripts import qa_motion_random_suite as base_qa  # noqa: E402


STRICT_USER_PROMPT = (
    "\u0412\u043d\u0430\u0447\u0430\u043b\u0435 \u0442\u043e\u043b\u044c\u043a\u043e "
    "\u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d - \u0424\u0435\u0439\u0434 \u0438\u043d 2 "
    "\u0441\u0435\u043a\u0443\u043d\u0434\u044b, \u041f\u043e\u0442\u043e\u043c "
    "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437 \u043d\u0430\u0447\u0438\u043d\u0430\u044e\u0442 "
    "\u0433\u0440\u0430\u0444\u0434\u0438\u0435\u043d\u0442\u043e\u043c \u0447\u0435\u0440\u0435\u0437 "
    "\u0444\u0435\u0439\u0434 \u0438\u043d \u043f\u043e\u044f\u0432\u043b\u044f\u0442\u044c\u0441\u044f "
    "\u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u0441\u043b\u043e\u0438.\n\n"
    "\u0412\u044b\u0445\u043e\u0434 - \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c "
    "\u0444\u0435\u0439\u0434\u0430\u0443\u0442 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b "
    "\u0432\u043a\u043e\u043d\u0446\u0435"
)

CLEAN_RU_PROMPT = (
    "\u041f\u0435\u0440\u0432\u044b\u0435 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b "
    "\u0442\u043e\u043b\u044c\u043a\u043e \u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d. "
    "\u041f\u043e\u0442\u043e\u043c \u0432\u0441\u0435 \u0441\u043b\u043e\u0438 \u0441\u0432\u0435\u0440\u0445\u0443 "
    "\u0432\u043d\u0438\u0437 \u043f\u043e\u044f\u0432\u043b\u044f\u044e\u0442\u0441\u044f "
    "\u0433\u0440\u0430\u0434\u0438\u0435\u043d\u0442\u043d\u044b\u043c fade-in. "
    "\u0412 \u043a\u043e\u043d\u0446\u0435 \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c "
    "\u0443\u0445\u043e\u0434\u0438\u0442 \u0432 fade-out 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b."
)


def phase_map(plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(plan, dict):
        return {}
    return {str(item.get("id")): item for item in list(plan.get("phases") or []) if isinstance(item, dict)}


def clean_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return base_qa.clean_layers(layers)


def layer_by_id(layers: list[dict[str, Any]], layer_id: str) -> dict[str, Any]:
    return base_qa.layer_by_id(layers, layer_id)


def is_background_recipe(layer: dict[str, Any]) -> bool:
    recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else {}
    tags = {str(tag) for tag in list(recipe.get("tags") or [])}
    layer_id = str(layer.get("id") or "")
    return layer_id.startswith("__frame_choreo_white_bg_") or "background" in tags or "white-intro" in tags


def content_motion_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for layer in layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if not recipe or is_background_recipe(layer):
            continue
        tags = {str(tag) for tag in list(recipe.get("tags") or [])}
        if "frame" not in tags:
            continue
        result.append(layer)
    return result


def apply_whole_frame(motion: Any, prompt: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    if not should_use_frame_choreography_prompt(prompt):
        raise AssertionError("whole-frame prompt did not route to choreography")
    planning_motion = motion.model_copy(update={"figma_layers": clean_layers(list(motion.figma_layers or []))})
    layers = plan_frame_choreography(prompt, planning_motion)
    plan = describe_motion_plan(layers) or {}
    layers = attach_frame_motion_contract(prompt, str(motion.id), layers, plan)
    return layers, describe_motion_plan(layers) or plan, [{"kind": "whole-frame", "prompt": prompt}]


def apply_selected_steps(
    base_layers: list[dict[str, Any]],
    target_id: str,
    steps: list[dict[str, str]],
    timeline_duration: float,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    layers = [dict(layer) for layer in base_layers]
    target = dict(layer_by_id(layers, target_id))
    recipe: dict[str, Any] | None = None
    operations: list[dict[str, Any]] = []
    for step in steps:
        recipe = build_layer_motion_recipe_from_prompt(
            step["prompt"],
            step.get("mode", "replace"),
            target,
            layers,
            timeline_duration=timeline_duration,
        )
        target["motion_recipe"] = recipe
        operations.append({"kind": "prompt", "mode": step.get("mode", "replace"), "operation": recipe.get("motion_operation")})
    return [target if str(layer.get("id") or "") == str(target_id) else layer for layer in layers], recipe, operations


def assert_phase(plan: dict[str, Any], phase_id: str, preset: str, duration: float | None = None) -> list[str]:
    issues: list[str] = []
    phase = phase_map(plan).get(phase_id)
    if not phase:
        return [f"missing phase {phase_id}"]
    if phase.get("preset") != preset:
        issues.append(f"{phase_id} preset {phase.get('preset')} != {preset}")
    if duration is not None and abs(float(phase.get("duration") or 0) - float(duration)) > 0.04:
        issues.append(f"{phase_id} duration {phase.get('duration')} != {duration}")
    return issues


def assert_strict_gradient_contract(case: dict[str, Any], layers: list[dict[str, Any]], plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    duration = float(plan.get("duration") or case.get("duration") or 0)
    phases = phase_map(plan)
    intro = phases.get("intro") or {}
    build = phases.get("build") or {}
    outro = phases.get("outro") or {}
    issues.extend(assert_phase(plan, "intro", "white-bg-fade", 2.0))
    issues.extend(assert_phase(plan, "build", "gradient-fade-stagger", 3.0))
    issues.extend(assert_phase(plan, "outro", "full-frame-fade-out", 2.0))
    if build.get("order") != "top-down-by-role":
        issues.append(f"build order {build.get('order')} != top-down-by-role")
    if abs(float(outro.get("start") or 0) - max(0.0, duration - 2.0)) > 0.05:
        issues.append(f"outro start {outro.get('start')} is not anchored to final 2s")

    white = next((layer for layer in layers if str(layer.get("id") or "").startswith("__frame_choreo_white_bg_")), None)
    if not white:
        issues.append("missing white intro layer")
    else:
        keyframes = ((white.get("motion_recipe") or {}).get("motion_dsl") or {}).get("keyframes") or []
        if len(keyframes) < 4:
            issues.append("white intro keyframes incomplete")
        else:
            if float(keyframes[0].get("opacity") or 0) != 0:
                issues.append("white intro does not start transparent for 2s fade-in")
            if abs(float(keyframes[1].get("time") or 0) - float(intro.get("duration") or 2.0)) > 0.04 or float(keyframes[1].get("opacity") or 0) != 1:
                issues.append("white intro does not reach full opacity at intro end")
            final = keyframes[-1]
            if float(final.get("opacity", 1)) != 0:
                issues.append("white intro layer does not fade out at end")
            for key in ("x", "y", "rotate"):
                if abs(float(final.get(key) or 0)) > 0.01:
                    issues.append(f"white outro has unwanted {key} motion")
            if abs(float(final.get("scale") or 1) - 1.0) > 0.01:
                issues.append("white outro has unwanted scale motion")

    content = content_motion_layers(layers)
    if not content:
        issues.append("no content layers animated")
    delays: list[tuple[float, float, str]] = []
    for layer in content:
        recipe = layer.get("motion_recipe") or {}
        if recipe.get("preset") != "gradient-fade-stagger":
            issues.append(f"{layer.get('id')} preset {recipe.get('preset')} != gradient-fade-stagger")
        dsl = recipe.get("motion_dsl") or {}
        keyframes = [frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        entry_frames = keyframes[:4]
        for frame in entry_frames:
            if abs(float(frame.get("x") or 0)) > 0.01 or abs(float(frame.get("y") or 0)) > 0.01:
                issues.append(f"{layer.get('id')} has unwanted fly-in x/y during gradient entry")
                break
            if abs(float(frame.get("rotate") or 0)) > 0.01 or abs(float(frame.get("scale") or 1) - 1.0) > 0.01:
                issues.append(f"{layer.get('id')} has unwanted transform during gradient entry")
                break
        effects = [effect for effect in list(dsl.get("effects") or []) if isinstance(effect, dict)]
        if not any(effect.get("type") == "wipe-reveal" and effect.get("direction") == "down" for effect in effects):
            issues.append(f"{layer.get('id')} missing downward wipe-reveal")
        if len(keyframes) >= 2:
            delays.append((float(keyframes[1].get("time") or 0), float(layer.get("y") or 0), str(layer.get("id") or "")))
        final = keyframes[-1] if keyframes else {}
        if abs(float(final.get("x") or 0)) > 0.01 or abs(float(final.get("y") or 0)) > 0.01:
            issues.append(f"{layer.get('id')} fade-only outro has unwanted position movement")
    by_delay = sorted(delays, key=lambda item: (item[0], item[2]))
    for previous, current in zip(by_delay, by_delay[1:]):
        if previous[1] > current[1] + 2.0 and current[0] > previous[0] + 0.001:
            issues.append("top-down delay order is not monotonic by layer y")
            break
    return issues


def case_status(case: dict[str, Any]) -> tuple[str, list[str]]:
    status, issues = base_qa.evaluate_case(case)
    if case.get("strict_gradient_contract"):
        issues.extend(case.get("strict_gradient_issues") or [])
    return ("pass" if not issues else "fail"), issues


def build_active_specs(picked: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": "p01-user-typo-gradient-contract",
            "title": "strict user prompt: white fade-in, top-down gradient layer fade, final fadeout",
            "scope": "whole-frame",
            "prompt": STRICT_USER_PROMPT,
            "samples": [0.4, 1.9, 2.45, 4.8, 10.6],
            "strict_gradient_contract": True,
        },
        {
            "id": "p02-clean-russian-gradient-contract",
            "title": "clean Russian spelling keeps same strict gradient contract",
            "scope": "whole-frame",
            "prompt": CLEAN_RU_PROMPT,
            "samples": [0.4, 2.2, 4.8, 9.7, 10.8],
            "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 2.0}, "build": {"preset": "gradient-fade-stagger", "duration": 3.0}, "outro": {"preset": "full-frame-fade-out", "duration": 2.0}}},
        },
        {
            "id": "p03-english-gradient-contract",
            "title": "English prompt maps to the same gradient fade contract",
            "scope": "whole-frame",
            "prompt": "First 2 seconds: only a white background fades in. Then all layers appear from top to bottom with a soft gradient fade-in, no flying. At the end the whole frame fades out during the final 2 seconds.",
            "samples": [0.4, 2.2, 4.8, 9.7, 10.8],
            "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 2.0}, "build": {"preset": "gradient-fade-stagger", "duration": 3.0}, "outro": {"preset": "full-frame-fade-out", "duration": 2.0}}},
        },
        {
            "id": "p04-shorter-gradient-timing",
            "title": "variant timing: 1.5s intro, 2s build, 1s final fade",
            "scope": "whole-frame",
            "prompt": "First 1.5 seconds background only. Then all layers fade in from top to bottom with a soft gradient over 2 seconds. Full frame fade out in the last 1 second.",
            "samples": [0.3, 1.6, 3.2, 10.4, 11.0],
            "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 1.5}, "build": {"preset": "gradient-fade-stagger", "duration": 2.0}, "outro": {"preset": "full-frame-fade-out", "duration": 1.0}}},
        },
        {
            "id": "p05-explicit-fly-is-not-overridden-by-intro-fade",
            "title": "explicit fly-in remains fly-in even when intro uses fade",
            "scope": "whole-frame",
            "prompt": "First 1 second white background only. Then all layers fly into place over 2 seconds in random order. In the last 2 seconds the full frame shatters and fades out.",
            "samples": [0.4, 1.6, 3.2, 9.7, 10.8],
            "expected": {"phases": {"intro": {"preset": "white-bg-fade", "duration": 1.0}, "build": {"preset": "random-fly-in-stagger", "duration": 2.0}, "outro": {"preset": "full-frame-shatter", "duration": 2.0}}},
        },
        {
            "id": "p06-advanced-composition",
            "title": "advanced choreography keeps role-specific build",
            "scope": "whole-frame",
            "prompt": "First 0.5 seconds the background appears with venetian blinds. Then photos use parallax, text uses fade up lines from top to bottom, and buttons rise on position Y; the whole composition appears within 2 seconds. In the last 2 seconds layers scatter and fall down with physics while fading out.",
            "samples": [0.2, 0.8, 1.8, 9.8, 10.8],
            "expected": {"phases": {"intro": {"preset": "venetian-blinds-bg", "duration": 0.5}, "build": {"preset": "advanced-composition-build", "duration": 1.5}, "outro": {"preset": "layer-scatter-fall", "duration": 2.0}}},
        },
        {
            "id": "p07-camera-only",
            "title": "camera-only prompt does not invent layer choreography",
            "scope": "whole-frame",
            "prompt": "Add a slow camera push in over 3 seconds, keep all layers as they are.",
            "samples": [0.2, 1.5, 2.9],
            "expected": {"phases": {"camera": {"preset": "camera-push", "duration": 11.282917857142857}}},
        },
        {
            "id": "p08-selected-text-fadein",
            "title": "selected text layer: simple 2s fade-in",
            "scope": "selected-layer",
            "layer": picked["text"],
            "steps": [{"mode": "replace", "prompt": "Fade this layer in over 2 seconds."}],
            "samples": [0.2, 1.0, 2.4],
            "expected": {"action_count": 1, "preset": "fade-in", "duration": 2.0},
        },
        {
            "id": "p09-many-small-prompts-one-block",
            "title": "many small prompts on one block: fade, retime, append end fade",
            "scope": "selected-layer",
            "layer": picked["image2"],
            "duration": 7.0,
            "steps": [
                {"mode": "replace", "prompt": "Fade in over 1 second."},
                {"mode": "append", "prompt": "Make the fade-in last 2 seconds instead."},
                {"mode": "append", "prompt": "At the end fade out over the last 1 second."},
            ],
            "samples": [0.3, 1.5, 6.6],
            "expected": {"action_count": 2, "preset": "fade-out", "duration": 1.0, "start": 6.0},
        },
        {
            "id": "p10-replace-clears-stack",
            "title": "new prompt replaces previous stack",
            "scope": "selected-layer",
            "layer": picked["shape"],
            "duration": 4.0,
            "steps": [
                {"mode": "replace", "prompt": "Fade in over 2 seconds."},
                {"mode": "append", "prompt": "After that drop down like a stone."},
                {"mode": "replace", "prompt": "New: fade out over 1 second."},
            ],
            "samples": [0.1, 0.6, 1.4],
            "expected": {"action_count": 1, "preset": "fade-out", "duration": 1.0},
        },
    ]


def run_active_case(motion: Any, base_layers: list[dict[str, Any]], spec: dict[str, Any], project_assets: Path, out_dir: Path) -> dict[str, Any]:
    case = dict(spec)
    case["layer_label"] = ""
    if case["scope"] == "whole-frame":
        layers, plan, operations = apply_whole_frame(motion, case["prompt"])
        case["layers"] = layers
        case["plan"] = plan
        case["recipe"] = None
        case["operations"] = operations
        case["phase_plan"] = plan
        case["duration"] = max(float(motion.duration or 0), float(plan.get("minimum_duration") or 0), 3.0)
        case["summary"] = ", ".join(f"{phase.get('id')}:{phase.get('preset')} {phase.get('duration')}s" for phase in plan.get("phases", []))
        if case.get("strict_gradient_contract"):
            case["strict_gradient_issues"] = assert_strict_gradient_contract(case, layers, plan)
    else:
        target = case["layer"]
        duration_hint = float(case.get("duration") or motion.duration or 0)
        layers, recipe, operations = apply_selected_steps(base_layers, str(target["id"]), case["steps"], duration_hint)
        case["layers"] = layers
        case["recipe"] = recipe
        case["plan"] = None
        case["operations"] = operations
        case["prompt"] = " | ".join(step["prompt"] for step in case["steps"])
        case["layer_label"] = f"{target.get('kind')}:{target.get('name') or target.get('id')}"
        case["fidelity_exclude_layers"] = [target]
        case["actions"] = base_qa.action_summary(recipe)
        if "duration" not in case:
            case["duration"] = max(base_qa.recipe_required_duration(recipe) + 1.0, 3.0)
        else:
            case["duration"] = max(float(case["duration"]), base_qa.recipe_required_duration(recipe), 1.0)
        case["summary"] = f"actions={len(motion_recipe_actions(recipe))}; qa={(recipe.get('visual_qa') or {}).get('status') if recipe else 'none'}"
    video, frames = base_qa.render_case(motion, case, project_assets, out_dir)
    case["video"] = str(video) if video else ""
    case["frames"] = [str(path) for path in frames]
    if case.get("scope") == "selected-layer" and case.get("fidelity_exclude_layers"):
        case["pixel_fidelity"] = base_qa.pixel_fidelity_outside_layers(
            motion,
            frames,
            list(case.get("fidelity_exclude_layers") or []),
            project_assets.parent / str(motion.asset_path or ""),
        )
        case["summary"] = f"{case['summary']}; px_diff={case['pixel_fidelity'].get('max_mean_rgb_diff')}"
    case["status"], case["issues"] = case_status(case)
    for key in ("layers", "recipe", "plan", "fidelity_exclude_layers", "layer"):
        case.pop(key, None)
    return case


def run_plugin_frame_case(asset_id: str, out_dir: Path) -> dict[str, Any]:
    work_dir = out_dir / "plugin-work" / asset_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    motion = motion_from_plugin_asset(work_dir, asset_id, start=0, duration=8.0)
    layers, plan, operations = apply_whole_frame(motion, STRICT_USER_PROMPT)
    case = {
        "id": f"p11-three-frames-{asset_id}",
        "title": f"same strict prompt on plugin frame {asset_id}",
        "scope": "whole-frame",
        "prompt": STRICT_USER_PROMPT,
        "samples": [0.4, 1.9, 2.45, 4.8, 7.4],
        "layers": layers,
        "plan": plan,
        "recipe": None,
        "operations": operations,
        "phase_plan": plan,
        "duration": max(float(plan.get("minimum_duration") or 0), float(motion.duration or 0), 7.0),
        "summary": ", ".join(f"{phase.get('id')}:{phase.get('preset')} {phase.get('duration')}s" for phase in plan.get("phases", [])),
        "strict_gradient_contract": True,
        "strict_gradient_issues": assert_strict_gradient_contract({}, layers, plan),
    }
    video, frames = base_qa.render_case(motion, case, work_dir / "assets", out_dir)
    case["video"] = str(video) if video else ""
    case["frames"] = [str(path) for path in frames]
    case["status"], case["issues"] = case_status(case)
    for key in ("layers", "recipe", "plan"):
        case.pop(key, None)
    return case


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="testmotion1-4892642e")
    parser.add_argument("--out", default="")
    parser.add_argument("--assets", nargs="*", default=["119-43", "12-159", "136-242"])
    args = parser.parse_args()

    state = load_state(args.project)
    motion = next((item for item in state.motions if item.source_type == "figma" and item.figma_layers), None)
    if motion is None:
        raise SystemExit("No Figma motion found")
    base_layers = clean_layers(list(motion.figma_layers or []))
    picked = base_qa.pick_layers(base_layers)
    base = project_dir(args.project)
    project_assets = base / "assets"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-prompt-senior-{stamp}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, Any]] = []
    for spec in build_active_specs(picked):
        try:
            case = run_active_case(motion, base_layers, spec, project_assets, out_dir)
        except Exception as exc:
            case = {"id": spec["id"], "title": spec["title"], "scope": spec.get("scope"), "status": "fail", "issues": [f"{type(exc).__name__}: {exc}"], "frames": [], "video": ""}
        cases.append(case)
        print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}")

    for asset_id in args.assets[:3]:
        try:
            case = run_plugin_frame_case(asset_id, out_dir)
        except Exception as exc:
            case = {"id": f"p11-three-frames-{asset_id}", "title": f"same strict prompt on plugin frame {asset_id}", "scope": "whole-frame", "status": "fail", "issues": [f"{type(exc).__name__}: {exc}"], "frames": [], "video": ""}
        cases.append(case)
        print(f"{case['id']}: {case['status']} - {case.get('issues') or 'ok'}")

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
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("report", "contact_sheet", "total", "pass", "fail")}, ensure_ascii=False, indent=2))
    return 0 if summary["fail"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
