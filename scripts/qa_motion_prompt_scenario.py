from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.figma_plugin import motion_from_plugin_asset
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.motion_intent import attach_frame_motion_contract, build_layer_motion_recipe_from_prompt, motion_recipe_actions


FINAL_PROMPT = (
    "Фоновый слой появляется через эффект Venetian Blinds (длительность анимации - 0,5сек), "
    "затем фотографии появляются через эффект параллакса, после чего появляется текст через эффект fade up lines "
    "(сначала главный заголовок, затем все остальные части текста - анимация появления происходит в порядке «сверху вниз»), "
    "затем появляются черные кнопки через эффект вылета по position Y снизу вверх + легкий fade in. "
    "Вся композиция должна появиться (и все анимации должны произойти) за 2 секунды. "
    "Анимация исчезания всей композициипусть происходит через эффект «рассыпания» слоев и опадания их вниз "
    "с учетом законов физики"
)


def font(size: int) -> ImageFont.ImageFont:
    for candidate in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def assert_close(name: str, actual: Any, expected: float, tolerance: float = 0.035) -> None:
    value = float(actual)
    if abs(value - expected) > tolerance:
        raise AssertionError(f"{name}: expected {expected:.3f}, got {value:.3f}")


def assert_effects(name: str, steps: list[dict[str, Any]], expected: list[str]) -> None:
    actual = [str(step.get("effect") or "") for step in steps]
    if actual != expected:
        raise AssertionError(f"{name}: effects {actual} != {expected}")


def assert_director(name: str, scenario: dict[str, Any], min_effects: int = 1, min_timing: int = 0) -> None:
    director = scenario.get("motion_director") if isinstance(scenario.get("motion_director"), dict) else {}
    if director.get("status") != "pass":
        raise AssertionError(f"{name}: director status {director.get('status')} != pass")
    if director.get("preview_render_source") != "motion_dsl":
        raise AssertionError(f"{name}: director preview/render source is not motion_dsl")
    if director.get("source_of_truth") != "figma-frame-png":
        raise AssertionError(f"{name}: director source of truth is not figma-frame-png")
    if len(list(director.get("storyboard") or [])) != len(list(scenario.get("steps") or [])):
        raise AssertionError(f"{name}: director storyboard does not mirror scenario steps")
    effects = list(director.get("expected_effects") or [])
    timings = list(director.get("timing_checks") or [])
    if len(effects) < min_effects:
        raise AssertionError(f"{name}: director expected effects too small: {len(effects)}")
    if len(timings) < min_timing:
        raise AssertionError(f"{name}: director timing checks too small: {len(timings)}")
    if any(item.get("status") != "pass" for item in effects + timings):
        raise AssertionError(f"{name}: director contains failing checks")


def assert_gate(name: str, scenario: dict[str, Any], min_checks: int = 7) -> None:
    gate = scenario.get("motion_qa_gate") if isinstance(scenario.get("motion_qa_gate"), dict) else {}
    if gate.get("status") != "pass":
        raise AssertionError(f"{name}: QA gate status {gate.get('status')} != pass; {gate.get('errors')}")
    checks = list(gate.get("checks") or [])
    if len(checks) < min_checks:
        raise AssertionError(f"{name}: QA gate checks too small: {len(checks)}")
    if any(item.get("status") != "pass" for item in checks):
        raise AssertionError(f"{name}: QA gate contains non-pass checks")
    expected = {"prompt_plan", "prompt_timing", "action_stack", "effect_mapping", "dsl_source", "visual_contract", "figma_fidelity"}
    actual = {str(item.get("id") or "") for item in checks}
    missing = sorted(expected - actual)
    if missing:
        raise AssertionError(f"{name}: QA gate missing checks {missing}")


def selected_layer_case(work_dir: Path) -> dict[str, Any]:
    motion = motion_from_plugin_asset(work_dir, "12-159", start=0, duration=8.0)
    target = next(layer for layer in motion.figma_layers if layer.get("kind") == "image")
    recipe = build_layer_motion_recipe_from_prompt("сделай fade-in в начале длительностью 2 секунды", "replace", target, motion.figma_layers, 8.0)
    scenario = recipe.get("prompt_scenario") or {}
    steps = list(scenario.get("steps") or [])
    if (scenario.get("qa") or {}).get("status") != "pass":
        raise AssertionError("selected replace scenario QA failed")
    assert_effects("selected replace", steps, ["fade-in"])
    assert_close("selected fade duration", steps[0]["duration"], 2.0)

    target = {**target, "motion_recipe": recipe}
    layers = [target if str(layer.get("id")) == str(target.get("id")) else layer for layer in motion.figma_layers]
    recipe = build_layer_motion_recipe_from_prompt("сделай длиннее на 3 секунды", "append", target, layers, 8.0)
    scenario = recipe.get("prompt_scenario") or {}
    steps = list(scenario.get("steps") or [])
    assert_effects("selected retime", steps, ["fade-in"])
    assert_close("selected retimed duration", steps[0]["duration"], 5.0)
    if scenario.get("operation_type") != "modify":
        raise AssertionError(f"selected retime should be modify, got {scenario.get('operation_type')}")

    target = {**target, "motion_recipe": recipe}
    layers = [target if str(layer.get("id")) == str(target.get("id")) else layer for layer in motion.figma_layers]
    recipe = build_layer_motion_recipe_from_prompt("после этого через секунду пусть блок падает вниз как камень", "append", target, layers, 8.0)
    scenario = recipe.get("prompt_scenario") or {}
    steps = list(scenario.get("steps") or [])
    if (scenario.get("qa") or {}).get("status") != "pass":
        raise AssertionError("selected append scenario QA failed")
    assert_director("selected append", scenario, min_effects=1)
    assert_gate("selected append", scenario)
    assert_effects("selected append", steps, ["fade-in", "gravity-drop-fade"])
    assert_close("selected drop start", steps[1]["start"], 6.0)
    if len({step.get("id") for step in steps}) != len(steps):
        raise AssertionError("selected scenario step ids are not unique")
    return {"name": "selected-layer-sequence", "scenario": scenario, "status": "pass"}


def selected_layer_compound_case(work_dir: Path) -> dict[str, Any]:
    motion = motion_from_plugin_asset(work_dir, "12-159", start=0, duration=8.0)
    target = next(layer for layer in motion.figma_layers if layer.get("kind") == "image")
    prompt = (
        "\u0441\u043d\u0430\u0447\u0430\u043b\u0430 fade-in \u0432 \u043d\u0430\u0447\u0430\u043b\u0435 "
        "\u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b, "
        "\u043f\u043e\u0442\u043e\u043c \u0447\u0435\u0440\u0435\u0437 \u0441\u0435\u043a\u0443\u043d\u0434\u0443 "
        "\u043f\u0443\u0441\u0442\u044c \u0431\u043b\u043e\u043a \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 "
        "\u043a\u0430\u043a \u043a\u0430\u043c\u0435\u043d\u044c, \u0432 \u043a\u043e\u043d\u0446\u0435 fade-out "
        "\u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 1 \u0441\u0435\u043a\u0443\u043d\u0434\u0430"
    )
    recipe = build_layer_motion_recipe_from_prompt(prompt, "replace", target, motion.figma_layers, 8.0)
    scenario = recipe.get("prompt_scenario") or {}
    steps = list(scenario.get("steps") or [])
    if (scenario.get("qa") or {}).get("status") != "pass":
        raise AssertionError("compound selected-layer scenario QA failed")
    assert_director("compound selected layer", scenario, min_effects=2)
    assert_gate("compound selected layer", scenario)
    assert_effects("compound selected layer", steps, ["fade-in", "gravity-drop-fade", "fade-out"])
    assert_close("compound fade duration", steps[0]["duration"], 2.0)
    assert_close("compound drop start", steps[1]["start"], 3.0)
    assert_close("compound fade-out start", steps[2]["start"], 7.0)
    assert_close("compound fade-out duration", steps[2]["duration"], 1.0)
    actions = motion_recipe_actions(recipe)
    if len(actions) != 3:
        raise AssertionError(f"compound prompt should create 3 actions, got {len(actions)}")
    operation = recipe.get("motion_operation") or {}
    requested = operation.get("requested") or {}
    if requested.get("compound") is not True:
        raise AssertionError("compound prompt was not marked as compound")
    created_ids = [item for item in operation.get("created_action_ids") or [] if item]
    if len(created_ids) != 3 or len(set(created_ids)) != 3:
        raise AssertionError("compound prompt did not create three unique action ids")
    if len(requested.get("clauses") or []) != 3:
        raise AssertionError("compound prompt did not preserve three prompt clauses")
    return {"name": "selected-layer-compound-prompt", "scenario": scenario, "status": "pass"}


def whole_frame_case(work_dir: Path) -> dict[str, Any]:
    motion = motion_from_plugin_asset(work_dir, "12-159", start=0, duration=8.0)
    if not should_use_frame_choreography_prompt(FINAL_PROMPT):
        raise AssertionError("final prompt did not route to whole-frame choreography")
    layers = plan_frame_choreography(FINAL_PROMPT, motion)
    plan = describe_motion_plan(layers) or {}
    layers = attach_frame_motion_contract(FINAL_PROMPT, motion.id, layers, plan)
    plan = describe_motion_plan(layers) or {}
    scenario = plan.get("prompt_scenario") or {}
    steps = list(scenario.get("steps") or [])
    if (scenario.get("qa") or {}).get("status") != "pass":
        raise AssertionError("whole-frame scenario QA failed")
    assert_director("whole-frame final prompt", scenario, min_effects=5, min_timing=1)
    assert_gate("whole-frame final prompt", scenario)
    assert_effects(
        "whole-frame final prompt",
        steps,
        ["venetian-blinds-bg", "parallax-photo", "fade-up-lines", "button-y-rise", "layer-scatter-fall"],
    )
    assert_close("venetian start", steps[0]["start"], 0.0)
    assert_close("venetian duration", steps[0]["duration"], 0.5)
    assert_close("photos start", steps[1]["start"], 0.5)
    assert_close("text start", steps[2]["start"], 1.01)
    assert_close("buttons end", steps[3]["end"], 2.0)
    assert_close("outro duration", steps[4]["duration"], 3.0)
    if not (steps[0]["end"] <= steps[1]["start"] <= steps[2]["start"] <= steps[3]["start"]):
        raise AssertionError("whole-frame scenario is not ordered by the prompt")
    attached = [
        layer
        for layer in layers
        if isinstance(layer.get("motion_recipe"), dict)
        and isinstance(layer["motion_recipe"].get("prompt_scenario"), dict)
    ]
    if not attached:
        raise AssertionError("whole-frame scenario was not attached to rendered recipes")
    return {"name": "whole-frame-final-prompt", "scenario": scenario, "status": "pass"}


def draw_report(cases: list[dict[str, Any]], output: Path) -> None:
    row_h = 42
    width = 1320
    total_rows = sum(max(1, len(case["scenario"].get("steps") or [])) + 1 for case in cases)
    image = Image.new("RGB", (width, max(260, total_rows * row_h + 30)), (246, 246, 246))
    draw = ImageDraw.Draw(image)
    title_font = font(18)
    body_font = font(13)
    y = 18
    for case in cases:
        draw.text((18, y), f"{case['name']} - {case['status']}", fill=(0, 0, 0), font=title_font)
        y += row_h
        for step in list(case["scenario"].get("steps") or []):
            text = f"{step.get('start')} - {step.get('end')}s   {step.get('target')}   {step.get('effect')}   {step.get('label')}"
            draw.rounded_rectangle((18, y - 4, width - 18, y + row_h - 10), radius=6, fill=(255, 255, 255), outline=(218, 218, 218))
            draw.text((30, y + 5), text[:170], fill=(20, 20, 20), font=body_font)
            y += row_h
        y += 8
    image.save(output)


def main() -> int:
    out_dir = ROOT / "qa_artifacts" / f"motion-prompt-scenario-{time.strftime('%Y%m%d-%H%M%S')}"
    work_dir = out_dir / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    issues: list[str] = []
    for case_name, case_fn in (
        ("selected-layer-sequence", selected_layer_case),
        ("selected-layer-compound-prompt", selected_layer_compound_case),
        ("whole-frame-final-prompt", whole_frame_case),
    ):
        try:
            cases.append(case_fn(work_dir / case_name))
        except Exception as exc:
            issues.append(f"{case_name}: {exc}")
    if cases:
        draw_report(cases, out_dir / "prompt_scenario_report.png")
    summary = {"status": "pass" if not issues else "fail", "artifact_dir": str(out_dir), "cases": cases, "issues": issues}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
