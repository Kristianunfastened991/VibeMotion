from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.motion_intent import attach_frame_motion_contract, build_layer_motion_recipe_after_delete, build_layer_motion_recipe_from_prompt, motion_recipe_actions
from app.services.projects import list_projects, load_state


FADE_PROMPT = "\u0421\u0434\u0435\u043b\u0430\u0439 \u0444\u0435\u0439\u0434\u0438\u043d \u0432\u043d\u0430\u0447\u0430\u043b\u0435 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b"
RETIME_PROMPT = "\u0441\u0434\u0435\u043b\u0430\u0439 \u0434\u043b\u0438\u043d\u043d\u0435\u0435 \u043d\u0430 3 \u0441\u0435\u043a\u0443\u043d\u0434\u044b"
DROP_PROMPT = "\u0430 \u0442\u0435\u043f\u0435\u0440\u044c \u043f\u0443\u0441\u0442\u044c \u043a\u043e\u0433\u0434\u0430 \u0444\u0435\u0439\u0434 \u0438\u043d \u0440\u0435\u0430\u043b\u0438\u0437\u0443\u0435\u0442\u0441\u044f \u0442\u043e \u043f\u043e\u0441\u043b\u0435 \u044d\u0442\u043e\u0433\u043e \u0447\u0435\u0440\u0435\u0437 \u0441\u0435\u043a\u0443\u043d\u0434\u0443 \u0432\u0435\u0441\u044c \u0431\u043b\u043e\u043a \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 \u043a\u0430\u043a \u043a\u0430\u043c\u0435\u043d\u044c"
WHOLE_FRAME_PROMPT = "\u043f\u0435\u0440\u0432\u044b\u0435 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b - \u0444\u0435\u0439\u0434 \u0438\u043d \u0431\u0435\u043b\u043e\u0433\u043e \u044d\u043a\u0440\u0430\u043d\u0430 \u0432\u043d\u0430\u0447\u0430\u043b\u0435, \u0438\u043c\u0435\u043d\u043d\u043e \u0444\u043e\u043d\u0430 \u0431\u0435\u0437 \u0434\u0440\u0443\u0433\u0438\u0445 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u043e\u0432. \u041f\u043e\u0442\u043e\u043c \u0432\u0441\u0435 \u044d\u043b\u0435\u043c\u0435\u043d\u0442\u044b \u043d\u0430\u0447\u0438\u043d\u0430\u044e\u0442 \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0438 \u0442\u0440\u0435\u0445 \u0441\u0435\u043a\u0443\u043d\u0434 \u0432\u043b\u0435\u0442\u0430\u0442\u044c \u0432 \u0444\u0440\u0435\u0439\u043c \u043d\u0430 \u0441\u0432\u043e\u0438 \u043c\u0435\u0441\u0442\u0430 \u0432 \u0441\u043b\u0443\u0447\u0430\u0439\u043d\u043e\u043c \u043f\u043e\u0440\u044f\u0434\u043a\u0435. \u0412\u043a\u043e\u043d\u0446\u0435 \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 \u043a\u0430\u043a \u0440\u0430\u0437\u0431\u0438\u0442\u043e\u0435 \u0441\u0442\u0435\u043a\u043b\u043e \u043f\u0440\u0435\u0434\u0432\u0430\u0440\u0438\u0442\u0435\u043b\u044c\u043d\u043e \u0440\u0430\u0437\u0431\u0438\u0432\u0448\u0438\u0441\u044c \u043d\u0430 \u043a\u0443\u0441\u043a\u0438 \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0438 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0445 3\u0445 \u0441\u0435\u043a\u0443\u043d\u0434 \u0438 \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c \u0443\u0445\u043e\u0434\u0438\u0442 \u0432 \u0444\u0435\u0439\u0434 \u0430\u0443\u0442"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def first_fallback_project_id() -> str:
    projects = list_projects()
    check(bool(projects), "no projects found")
    return str(projects[0].project_id)


def representative_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def is_pickable(layer: dict[str, Any], kind: str | None = None) -> bool:
        layer_id = str(layer.get("id") or "")
        node_type = str(layer.get("node_type") or "").upper()
        if layer.get("visible") is False or layer_id.startswith("__frame_choreo_") or layer.get("motion_internal") or layer.get("mask_role"):
            return False
        if layer.get("cluster_parent_id") or node_type == "FRAME":
            return False
        if kind is not None and str(layer.get("kind") or "") != kind:
            return False
        if kind in {"image", "shape"} and not layer.get("asset_path"):
            return False
        return True

    picked: list[dict[str, Any]] = []
    wanted = ["image", "text", "shape"]
    for kind in wanted:
        for layer in layers:
            if is_pickable(layer, kind) and layer not in picked:
                picked.append(layer)
                break
    if len(picked) < 3:
        for layer in layers:
            if is_pickable(layer) and layer not in picked:
                picked.append(layer)
            if len(picked) >= 3:
                break
    return picked[:3]


def assert_selected_layer_contract(layer: dict[str, Any], layers: list[dict[str, Any]]) -> str:
    clean_layer = dict(layer)
    clean_layer.pop("motion_recipe", None)
    fade = build_layer_motion_recipe_from_prompt(FADE_PROMPT, "replace", clean_layer, layers)
    actions = motion_recipe_actions(fade)
    check(fade["motion_operation"]["type"] == "replace", "fade prompt must create replace operation")
    check(fade["visual_qa"]["status"] == "pass", "fade prompt QA must pass")
    check(len(actions) == 1 and actions[0]["preset"] == "fade-in", "fade prompt must create one fade-in action")
    check(abs(float(actions[0]["phase_plan"]["duration"]) - 2.0) <= 0.025, "fade-in duration must be exactly 2s")
    check(fade["dsl_contract"]["source_of_truth"] == "motion_dsl", "selected-layer DSL must be source of truth")

    retime_layer = {**clean_layer, "motion_recipe": fade}
    retimed = build_layer_motion_recipe_from_prompt(RETIME_PROMPT, "append", retime_layer, layers)
    retimed_actions = motion_recipe_actions(retimed)
    check(retimed["motion_operation"]["type"] == "modify", "retime prompt must modify, not append text")
    check(len(retimed_actions) == 1, "retime prompt must preserve action count")
    check(abs(float(retimed_actions[0]["phase_plan"]["duration"]) - 5.0) <= 0.025, "longer-by retime must add 3s to the existing 2s action")

    drop_layer = {**clean_layer, "motion_recipe": retimed}
    stacked = build_layer_motion_recipe_from_prompt(DROP_PROMPT, "append", drop_layer, layers)
    stacked_actions = motion_recipe_actions(stacked)
    check(stacked["motion_operation"]["type"] == "append", "drop prompt must append a new action")
    check(len(stacked_actions) == 2, "drop prompt must produce two stacked actions")
    check(stacked_actions[1]["preset"] == "gravity-drop-fade", "second action must be gravity drop")
    check(abs(float(stacked_actions[1]["phase_plan"]["start"]) - 6.0) <= 0.025, "drop must start 1s after the extended 5s fade")
    check(stacked["visual_qa"]["status"] == "pass", "stacked action QA must pass")

    deleted, delete_operation, found = build_layer_motion_recipe_after_delete(stacked, str(stacked_actions[0]["id"]), clean_layer)
    check(found, "delete must find the selected action")
    check(delete_operation["type"] == "delete", "delete must be recorded as an operation")
    check(len(motion_recipe_actions(deleted)) == 1, "delete must remove only the selected action")
    check(deleted["visual_qa"]["status"] == "pass", "delete result QA must pass")
    return f"{layer.get('kind')}:{layer.get('name') or layer.get('id')}"


def assert_whole_frame_contract(motion: Any) -> str:
    check(should_use_frame_choreography_prompt(WHOLE_FRAME_PROMPT), "whole-frame prompt must route to choreography")
    layers = plan_frame_choreography(WHOLE_FRAME_PROMPT, motion)
    phase_plan = describe_motion_plan(layers)
    check(isinstance(phase_plan, dict), "whole-frame plan must be present")
    layers = attach_frame_motion_contract(WHOLE_FRAME_PROMPT, str(motion.id), layers, phase_plan)
    plan = describe_motion_plan(layers)
    check(plan and plan.get("scope") == "whole-frame", "whole-frame scope must be explicit")
    phases = {phase.get("id"): phase for phase in plan.get("phases", [])}
    recipe_ids = [
        str((layer.get("motion_recipe") or {}).get("id") or "")
        for layer in layers
        if isinstance(layer.get("motion_recipe"), dict)
    ]
    recipe_ids = [recipe_id for recipe_id in recipe_ids if recipe_id]
    check(len(recipe_ids) == len(set(recipe_ids)), "whole-frame motion recipes must have unique action ids")
    check(phases["intro"]["preset"] == "white-bg-fade", "intro must be white-bg-fade")
    check(abs(float(phases["intro"]["duration"]) - 2.0) <= 0.025, "intro duration must be 2s")
    check(phases["build"]["preset"] == "random-fly-in-stagger", "build must be random-fly-in-stagger")
    check(abs(float(phases["build"]["duration"]) - 3.0) <= 0.025, "build duration must be 3s")
    check(phases["outro"]["preset"] == "full-frame-shatter", "outro must be full-frame-shatter")
    check(abs(float(phases["outro"]["duration"]) - 3.0) <= 0.025, "outro duration must be 3s")
    check(plan["visual_qa"]["status"] == "pass", "whole-frame QA must pass")
    check(plan["dsl_contract"]["source_of_truth"] == "motion_dsl", "whole-frame DSL must be source of truth")
    return str(motion.id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="")
    args = parser.parse_args()

    project_id = args.project or first_fallback_project_id()
    state = load_state(project_id)
    figma_motion = next((motion for motion in state.motions if motion.source_type == "figma" and motion.figma_layers), None)
    check(figma_motion is not None, "no Figma motion found")
    layers = list(figma_motion.figma_layers or [])
    picked = representative_layers(layers)
    check(len(picked) >= 3, "need at least 3 representative Figma layers")

    selected = [assert_selected_layer_contract(layer, layers) for layer in picked]
    whole = assert_whole_frame_contract(figma_motion)
    print("motion intent contract: pass")
    print("selected layers:", ", ".join(selected))
    print("whole-frame motion:", whole)


if __name__ == "__main__":
    main()
