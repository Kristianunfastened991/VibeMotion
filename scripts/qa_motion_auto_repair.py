from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.motion_intent import (  # noqa: E402
    _attach_motion_recipe_qa,
    attach_frame_motion_contract,
    build_layer_motion_recipe_from_prompt,
    motion_recipe_actions,
)


def assert_gate_pass(name: str, scenario: dict) -> dict:
    gate = scenario.get("motion_qa_gate") if isinstance(scenario.get("motion_qa_gate"), dict) else {}
    if gate.get("status") != "pass":
        raise AssertionError(f"{name}: gate failed: {gate}")
    failed = [item for item in list(gate.get("checks") or []) if item.get("status") != "pass"]
    if failed:
        raise AssertionError(f"{name}: gate checks failed: {failed}")
    return gate


def assert_repaired(name: str, gate: dict, expected_kind: str) -> None:
    repair = gate.get("repair") if isinstance(gate.get("repair"), dict) else {}
    actions = list(repair.get("actions") or [])
    if repair.get("status") != "repaired" or not actions:
        raise AssertionError(f"{name}: expected auto-repair, got {repair}")
    if not any(item.get("kind") == expected_kind for item in actions if isinstance(item, dict)):
        raise AssertionError(f"{name}: expected repair kind {expected_kind}, got {actions}")


def selected_layer_missing_dsl_case() -> dict:
    layer = {"id": "layer-photo", "name": "Photo", "kind": "image", "visible": True, "bounds": {"x": 80, "y": 60, "width": 320, "height": 220}}
    recipe = build_layer_motion_recipe_from_prompt(
        "make fade-in in the beginning duration 2 seconds",
        "replace",
        layer,
        [layer],
        6.0,
    )
    operation = dict(recipe.get("motion_operation") or {})
    action = motion_recipe_actions(recipe)[0]
    action.pop("motion_dsl", None)
    action["phase_plan"] = {**dict(action.get("phase_plan") or {}), "duration": 0.1, "minimum_duration": 0.1}
    broken = {**recipe, "motion_actions": [action]}
    broken.pop("motion_dsl", None)

    fixed = _attach_motion_recipe_qa(broken, operation)
    scenario = fixed.get("prompt_scenario") or {}
    gate = assert_gate_pass("selected missing DSL", scenario)
    assert_repaired("selected missing DSL", gate, "motion-dsl")
    fixed_action = motion_recipe_actions(fixed)[0]
    keyframes = list((fixed_action.get("motion_dsl") or {}).get("keyframes") or [])
    if len(keyframes) < 2:
        raise AssertionError("selected missing DSL: keyframes were not synthesized")
    duration = float((fixed_action.get("phase_plan") or {}).get("duration") or 0)
    if abs(duration - 2.0) > 0.025:
        raise AssertionError(f"selected missing DSL: expected 2s duration, got {duration}")
    return {"name": "selected-layer-missing-dsl", "status": "pass", "repair": gate.get("repair")}


def selected_layer_bad_keyframes_case() -> dict:
    layer = {"id": "layer-title", "name": "Title", "kind": "text", "visible": True, "bounds": {"x": 120, "y": 90, "width": 420, "height": 80}}
    recipe = build_layer_motion_recipe_from_prompt(
        "make fade-out at the end duration 1 second",
        "replace",
        layer,
        [layer],
        6.0,
    )
    operation = dict(recipe.get("motion_operation") or {})
    action = motion_recipe_actions(recipe)[0]
    dsl = dict(action.get("motion_dsl") or {})
    action["motion_dsl"] = {
        **dsl,
        "keyframes": [
            {**dict((dsl.get("keyframes") or [{}])[-1]), "time": 6.0},
            {**dict((dsl.get("keyframes") or [{}])[0]), "time": -0.5},
            {"time": "bad", "opacity": 0.5, "x": 0, "y": 0, "scale": 1, "rotate": 0},
        ],
    }
    broken = {**recipe, "motion_actions": [action], "motion_dsl": action["motion_dsl"]}

    fixed = _attach_motion_recipe_qa(broken, operation)
    scenario = fixed.get("prompt_scenario") or {}
    gate = assert_gate_pass("selected bad keyframes", scenario)
    assert_repaired("selected bad keyframes", gate, "keyframes")
    times = [float(frame.get("time") or 0) for frame in ((motion_recipe_actions(fixed)[0].get("motion_dsl") or {}).get("keyframes") or [])]
    if times != sorted(times) or any(value < 0 for value in times):
        raise AssertionError(f"selected bad keyframes: times not repaired: {times}")
    return {"name": "selected-layer-bad-keyframes", "status": "pass", "repair": gate.get("repair")}


def whole_frame_bad_plan_case() -> dict:
    prompt = (
        "Background appears via Venetian Blinds duration 0.5 seconds, then photos appear through parallax, "
        "then text appears with fade up lines from top to bottom, then black buttons rise on position Y with fade in. "
        "The whole composition must appear within 2 seconds. At the end the composition scatters and falls down with physics."
    )
    layers = [
        {
            "id": "bg",
            "name": "Background",
            "kind": "rect",
            "visible": True,
            "motion_recipe": {
                "id": "dup-recipe",
                "preset": "venetian-blinds-bg",
                "phase_plan": {"start": 0, "duration": 0.5},
                "motion_dsl": {},
            },
        },
        {
            "id": "photo",
            "name": "Photo",
            "kind": "image",
            "visible": True,
            "motion_recipe": {
                "id": "dup-recipe",
            "preset": "parallax-photo",
                "phase_plan": {"start": 0.5, "duration": 0.6},
                "motion_dsl": {"keyframes": [{"time": 1.1, "opacity": 1}, {"time": 0.5, "opacity": 0}]},
            },
        },
    ]
    phase_plan = {
        "phases": [
            {"id": "layer-scatter-fall", "label": "scatter", "target": "frame", "preset": "layer-scatter-fall", "start": 2.0, "duration": 1.0},
            {"id": "venetian-blinds-bg", "label": "background", "target": "background", "preset": "venetian-blinds-bg", "start": 0.0, "duration": 0.5},
            {"id": "parallax-photo", "label": "photos", "target": "photos", "preset": "parallax-photo", "start": 0.5, "duration": 0.55},
            {"id": "fade-up-lines", "label": "text", "target": "text", "preset": "fade-up-lines", "start": 1.05, "duration": 0.5},
            {"id": "button-y-rise", "label": "buttons", "target": "buttons", "preset": "button-y-rise", "start": 1.55, "duration": 0.45},
        ],
        "acceptance": {"sample_times": [0, 0.5, 1.05, 1.55, 2.0]},
    }
    fixed_layers = attach_frame_motion_contract(prompt, "motion-auto-repair", layers, phase_plan)
    recipe = fixed_layers[0]["motion_recipe"]
    scenario = recipe.get("prompt_scenario") or {}
    gate = assert_gate_pass("whole-frame bad plan", scenario)
    assert_repaired("whole-frame bad plan", gate, "motion-dsl")
    plan = recipe.get("phase_plan") or {}
    starts = [float(phase.get("start") or 0) for phase in list(plan.get("phases") or [])]
    if starts != sorted(starts):
        raise AssertionError(f"whole-frame bad plan: phases not sorted: {starts}")
    recipe_ids = [str((layer.get("motion_recipe") or {}).get("id") or "") for layer in fixed_layers]
    if len(recipe_ids) != len(set(recipe_ids)):
        raise AssertionError(f"whole-frame bad plan: duplicate recipe ids survived: {recipe_ids}")
    return {"name": "whole-frame-bad-plan", "status": "pass", "repair": gate.get("repair")}


def main() -> None:
    started = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = ROOT / "qa_artifacts" / f"motion-auto-repair-{started}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    results = [
        selected_layer_missing_dsl_case(),
        selected_layer_bad_keyframes_case(),
        whole_frame_bad_plan_case(),
    ]
    report = {"status": "pass", "tests": results, "artifact_dir": str(artifact_dir)}
    (artifact_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
