from __future__ import annotations

import re
from typing import Any


def _num(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _round_time(value: Any) -> float:
    return round(max(0.0, _num(value, 0.0)), 3)


def _clean_text(value: Any, fallback: str = "") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _effect_label(preset: str) -> str:
    labels = {
        "advanced-composition-build": "Semantic composition build",
        "button-y-rise": "Button Y rise + fade",
        "camera-pull": "Camera pull",
        "camera-push": "Camera push",
        "fade-in": "Fade in",
        "fade-out": "Fade out",
        "fade-up-lines": "Fade up lines",
        "basic-frame-drop": "Basic frame drop",
        "basic-frame-drop-out": "Basic frame drop out",
        "full-frame-drop": "Full-frame drop",
        "full-frame-fade-out": "Full-frame fade out",
        "full-frame-shatter": "Full-frame shatter",
        "glitch-bg-fade": "Glitch background fade",
        "glass-light-sweep": "Glass light sweep",
        "gravity-drop-fade": "Gravity drop",
        "handheld": "Handheld camera",
        "layer-scatter-fall": "Scatter and fall",
        "pan": "Camera pan",
        "parallax-photo": "Parallax photo reveal",
        "random-fly-in-stagger": "Random fly-in stagger",
        "scene-camera": "Scene camera",
        "signal-scan-reveal": "Signal scan reveal",
        "soft-pixel-snap": "Soft pixel snap",
        "static": "Hold",
        "static-reveal": "Static reveal",
        "tetris-build": "Tetris build",
        "text-slide-up-lines": "Text slide up lines",
        "venetian-blinds-bg": "Venetian blinds",
        "white-bg-fade": "Background fade",
    }
    return labels.get(preset, preset.replace("-", " ").title() if preset else "Motion")


def _target_label(target: str, fallback: str = "frame") -> str:
    labels = {
        "background-only": "background",
        "button-clusters": "buttons",
        "exact-source-frame": "whole frame",
        "full-frame": "whole frame",
        "image-layers": "photos",
        "remaining-elements": "remaining elements",
        "text-layers": "text",
        "visible-content-layers": "visible layers",
    }
    return labels.get(target, target or fallback)


_PROMPT_NUM = r"(\d+(?:[\.,]\d+)?)(?:\s*[x\u0445])?"
_OUTRO_EFFECTS = {
    "basic-frame-drop",
    "basic-frame-drop-out",
    "fade-out",
    "full-frame-drop",
    "full-frame-fade-out",
    "full-frame-shatter",
    "gravity-drop-fade",
    "layer-scatter-fall",
    "particle-dissolve",
    "smoke-dissolve",
}
_CAMERA_EFFECTS = {"camera-push", "camera-pull", "handheld", "pan", "scene-camera"}


def _prompt_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold().replace(",", ".")).strip()


def _first_seconds(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern.replace("<num>", _PROMPT_NUM), text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(str(match.group(1)).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return None


def _prompt_requirements(prompt: str, scope: str) -> dict[str, Any]:
    text = _prompt_text(prompt)
    required: list[tuple[str, tuple[str, ...]]] = []
    if re.search(r"venetian|blinds|\u0436\u0430\u043b\u044e\u0437", text):
        required.append(("venetian blinds", ("venetian-blinds-bg",)))
    if re.search(r"parallax|\u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441", text):
        required.append(("photo parallax", ("parallax-photo",)))
    if re.search(r"fade\s*up\s*lines|line\s*by\s*line|text\s*fade\s*up|\u0441\u0442\u0440\u043e\u043a", text) or (
        re.search(r"text|\u0442\u0435\u043a\u0441\u0442", text) and re.search(r"top\s+down|\u0441\u0432\u0435\u0440\u0445\u0443\s+\u0432\u043d\u0438\u0437", text)
    ):
        required.append(("text fade up lines", ("fade-up-lines",)))
    if re.search(r"text[\s\S]{0,80}(?:from\s+below|slide\s+up|rise)|\u0442\u0435\u043a\u0441\u0442[\s\S]{0,120}(?:\u0441\u043d\u0438\u0437\u0443|\u0432\u044b\u043f\u043b\u044b\u0432|\u043f\u043e\u0434\u043d\u0438\u043c)", text):
        required.append(("text slide up", ("text-slide-up-lines", "fade-up-lines")))
    if re.search(r"button|cta|position\s*y|from\s+below|bottom\s+to\s+top|\u043a\u043d\u043e\u043f\u043a|\u0441\u043d\u0438\u0437\u0443\s+\u0432\u0432\u0435\u0440\u0445", text):
        required.append(("button y rise", ("button-y-rise", "slide-up")))
    has_scatter_intent = bool(re.search(r"scatter|falling\s+pieces|\u0440\u0430\u0441\u0441\u044b\u043f|\u043e\u043f\u0430\u0434|\u043a\u0443\u0441\u043a", text))
    if has_scatter_intent:
        required.append(("scatter fall", ("layer-scatter-fall",)))
    if re.search(r"tetris|\u0442\u0435\u0442\u0440\u0438\u0441", text):
        required.append(("tetris build", ("tetris-build",)))
    if re.search(r"glitch|\u0433\u043b\u0438\u0442\u0447", text):
        required.append(("glitch", ("signal-scan-reveal", "glitch-bg-fade", "glitch")))
    if re.search(r"signal\s+scan|digital\s+scan|scan\s+reveal|\u0446\u0438\u0444\u0440\u043e\u0432\w*\s+\u0441\u043a\u0430\u043d|\u0441\u043a\u0430\u043d", text):
        required.append(("signal scan", ("signal-scan-reveal",)))
    if re.search(r"glass\s+sweep|light\s+sweep|premium\s+shine|shimmer\s+reveal|\u0441\u0442\u0435\u043a\u043b|\u0431\u043b\u0438\u043a|\u0448\u0438\u043c\u043c\u0435\u0440", text):
        required.append(("glass light sweep", ("glass-light-sweep",)))
    if re.search(r"pixel\s+snap|pixel\s+reveal|pixelated|\u043f\u0438\u043a\u0441\u0435\u043b", text):
        required.append(("soft pixel snap", ("soft-pixel-snap",)))
    if re.search(r"shatter|broken\s+glass|glass\s+shards|\u0441\u0442\u0435\u043a\u043b|\u043e\u0441\u043a\u043e\u043b|\u0440\u0430\u0437\u0431\u0438\u0432", text):
        required.append(("shatter", ("full-frame-shatter", "layer-scatter-fall")))
    if re.search(r"gravity\s+drop|fall\s+down|drop\s+down|fall\s+like\s+stone|\u043f\u0430\u0434\u0430\u0435\u0442\s+\u0432\u043d\u0438\u0437|\u043a\u0430\u043a\s+\u043a\u0430\u043c\u0435\u043d", text):
        required.append(("gravity drop", ("basic-frame-drop", "basic-frame-drop-out", "gravity-drop-fade", "full-frame-drop", "layer-scatter-fall", "full-frame-shatter")))
    if not has_scatter_intent and re.search(r"(?:whole|entire)\s+(?:frame|picture|composition)[\s\S]{0,120}(?:fall|drop)|(?:\u0432\u0441\u044f|\u0432\u0435\u0441\u044c)\s+(?:\u043a\u0430\u0440\u0442\u0438\u043d\u043a\w*|\u0444\u0440\u0435\u0439\u043c|\u043a\u0430\u0434\u0440|\u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\w*)[\s\S]{0,120}\u043f\u0430\u0434|\u043f\u0430\u0434[\w\s]{0,80}\u0446\u0435\u043b\u0438\u043a\u043e\u043c", text):
        required.append(("full frame drop", ("basic-frame-drop", "basic-frame-drop-out", "full-frame-drop")))
    if re.search(r"fade\s*out|fade-out|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0438\u0441\u0447\u0435\u0437|\u0443\u0445\u043e\u0434", text):
        required.append(("fade out", ("basic-frame-drop-out", "fade-out", "full-frame-fade-out", "gravity-drop-fade", "layer-scatter-fall", "full-frame-shatter")))

    appearance_deadline = _first_seconds(
        text,
        [
            r"(?:whole|entire|all)[\s\S]{0,90}?(?:appear|appearance|finish)[\s\S]{0,80}?(?:within|in|for)\s+<num>\s*(?:seconds?|sec|s)\b",
            r"(?:all\s+animations|all\s+appearance)[\s\S]{0,80}?(?:within|in|for)\s+<num>\s*(?:seconds?|sec|s)\b",
            r"\u0432\u0441\u044f\s+\u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446[\w]*[\s\S]{0,180}?\u0437\u0430\s+<num>\s*(?:\u0441\u0435\u043a[\w]*|sec|s)\b",
            r"\u0432\u0441\u0435\s+\u0430\u043d\u0438\u043c\u0430\u0446[\w]*[\s\S]{0,180}?\u0437\u0430\s+<num>\s*(?:\u0441\u0435\u043a[\w]*|sec|s)\b",
            r"(?:\u043f\u043e\u044f\u0432\u0438\u0442|\u043f\u043e\u044f\u0432\u043b\u0435\u043d)[\s\S]{0,180}?\u0437\u0430\s+<num>\s*(?:\u0441\u0435\u043a[\w]*|sec|s)\b",
        ],
    )
    venetian_duration = _first_seconds(
        text,
        [
            r"(?:venetian|blinds|\u0436\u0430\u043b\u044e\u0437)[\s\S]{0,120}?(?:duration|lasts?|for|over|within|in|\u0434\u043b\u0438\u0442|\u0437\u0430)[\s\S]{0,50}?<num>\s*(?:seconds?|sec|s|\u0441\u0435\u043a)",
            r"<num>\s*(?:seconds?|sec|s|\u0441\u0435\u043a)[\s\S]{0,120}?(?:venetian|blinds|\u0436\u0430\u043b\u044e\u0437)",
        ],
    )
    final_duration = _first_seconds(
        text,
        [
            r"(?:last|final|ending)[\s\S]{0,30}?<num>\s*(?:seconds?|sec|s)\b",
            r"(?:\u043f\u043e\u0441\u043b\u0435\u0434\u043d|\u0432\s+\u043a\u043e\u043d\u0446\u0435)[\s\S]{0,40}?<num>\s*(?:\u0441\u0435\u043a|sec|s)\b",
        ],
    )
    return {
        "scope": scope,
        "effects": required,
        "appearance_deadline": appearance_deadline,
        "venetian_duration": venetian_duration,
        "final_duration": final_duration,
    }


def _prompt_compliance_errors(steps: list[dict[str, Any]], prompt: str, scope: str) -> list[str]:
    requirements = _prompt_requirements(prompt, scope)
    if not requirements["effects"] and requirements["appearance_deadline"] is None and requirements["venetian_duration"] is None and requirements["final_duration"] is None:
        return []
    errors: list[str] = []
    effect_ids = {str(step.get("effect") or "") for step in steps}
    for label, accepted in requirements["effects"]:
        if not any(effect in effect_ids for effect in accepted):
            errors.append(f"prompt compliance: missing {label} ({' or '.join(accepted)})")

    venetian_duration = requirements["venetian_duration"]
    if venetian_duration is not None:
        venetian_steps = [step for step in steps if str(step.get("effect") or "") == "venetian-blinds-bg"]
        if not venetian_steps:
            errors.append("prompt compliance: missing timed venetian blinds step")
        elif abs(_num(venetian_steps[0].get("duration"), 0) - float(venetian_duration)) > 0.08:
            errors.append(f"prompt compliance: venetian duration {_num(venetian_steps[0].get('duration'), 0):.2f}s != {float(venetian_duration):.2f}s")

    deadline = requirements["appearance_deadline"]
    if deadline is not None:
        build_steps = [
            step
            for step in steps
            if str(step.get("effect") or "") not in _OUTRO_EFFECTS and str(step.get("effect") or "") not in _CAMERA_EFFECTS
        ]
        visible_end = max((_num(step.get("end"), 0) for step in build_steps), default=0.0)
        if visible_end > float(deadline) + 0.08:
            errors.append(f"prompt compliance: appearance ends at {visible_end:.2f}s, requested by {float(deadline):.2f}s")

    final_duration = requirements["final_duration"]
    if final_duration is not None:
        outro_steps = [step for step in steps if str(step.get("effect") or "") in _OUTRO_EFFECTS]
        if outro_steps:
            outro = max(outro_steps, key=lambda step: _num(step.get("start"), 0))
            if abs(_num(outro.get("duration"), 0) - float(final_duration)) > 0.12:
                errors.append(f"prompt compliance: outro duration {_num(outro.get('duration'), 0):.2f}s != {float(final_duration):.2f}s")
    return errors


def _director_contract(steps: list[dict[str, Any]], prompt: str, scope: str, qa: dict[str, Any]) -> dict[str, Any]:
    requirements = _prompt_requirements(prompt, scope)
    effect_ids = {str(step.get("effect") or "") for step in steps}
    expected_effects = []
    for label, accepted in requirements["effects"]:
        matched = [effect for effect in accepted if effect in effect_ids]
        expected_effects.append(
            {
                "label": label,
                "accepted_effects": list(accepted),
                "matched_effects": matched,
                "status": "pass" if matched else "fail",
            }
        )

    build_steps = [
        step
        for step in steps
        if str(step.get("effect") or "") not in _OUTRO_EFFECTS and str(step.get("effect") or "") not in _CAMERA_EFFECTS
    ]
    visible_end = max((_num(step.get("end"), 0) for step in build_steps), default=0.0)
    venetian_steps = [step for step in steps if str(step.get("effect") or "") == "venetian-blinds-bg"]
    outro_steps = [step for step in steps if str(step.get("effect") or "") in _OUTRO_EFFECTS]
    outro = max(outro_steps, key=lambda step: _num(step.get("start"), 0)) if outro_steps else None

    timing_checks = []
    deadline = requirements["appearance_deadline"]
    if deadline is not None:
        timing_checks.append(
            {
                "label": "appearance deadline",
                "requested": _round_time(deadline),
                "actual": _round_time(visible_end),
                "status": "pass" if visible_end <= float(deadline) + 0.08 else "fail",
            }
        )
    venetian_duration = requirements["venetian_duration"]
    if venetian_duration is not None:
        actual = _num(venetian_steps[0].get("duration"), 0) if venetian_steps else None
        timing_checks.append(
            {
                "label": "venetian duration",
                "requested": _round_time(venetian_duration),
                "actual": _round_time(actual) if actual is not None else None,
                "status": "pass" if actual is not None and abs(actual - float(venetian_duration)) <= 0.08 else "fail",
            }
        )
    final_duration = requirements["final_duration"]
    if final_duration is not None and outro is not None:
        actual = _num(outro.get("duration"), 0)
        timing_checks.append(
            {
                "label": "outro duration",
                "requested": _round_time(final_duration),
                "actual": _round_time(actual),
                "status": "pass" if abs(actual - float(final_duration)) <= 0.12 else "fail",
            }
        )

    errors = list(qa.get("errors") or [])
    status = "pass" if qa.get("status") == "pass" and not any(item.get("status") == "fail" for item in expected_effects + timing_checks) else "fail"
    return {
        "version": 1,
        "name": "motion-director",
        "status": status,
        "language": "deterministic",
        "pipeline": ["prompt", "motion_operation", "action_stack", "motion_dsl", "preview_render", "visual_qa"],
        "source_of_truth": "figma-frame-png",
        "preview_render_source": "motion_dsl",
        "expected_effects": expected_effects,
        "timing_checks": timing_checks,
        "storyboard": [
            {
                "id": str(step.get("id") or ""),
                "label": str(step.get("label") or step.get("effect") or "Motion"),
                "effect": str(step.get("effect") or ""),
                "target": str(step.get("target") or ""),
                "start": _round_time(step.get("start")),
                "end": _round_time(step.get("end")),
            }
            for step in steps
        ],
        "errors": errors,
        "summary": f"{len(steps)} steps · {len(expected_effects)} requested effects · {len(timing_checks)} timing checks",
    }


def build_motion_qa_gate(
    scenario: dict[str, Any] | None,
    visual_qa: dict[str, Any] | None = None,
    dsl_contract: dict[str, Any] | None = None,
    repair_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario = scenario if isinstance(scenario, dict) else {}
    scenario_qa = scenario.get("qa") if isinstance(scenario.get("qa"), dict) else {}
    director = scenario.get("motion_director") if isinstance(scenario.get("motion_director"), dict) else {}
    visual = visual_qa if isinstance(visual_qa, dict) else {}
    contract = dsl_contract if isinstance(dsl_contract, dict) else {}
    repair = repair_report if isinstance(repair_report, dict) else {}
    checks_src = scenario_qa.get("checks") if isinstance(scenario_qa.get("checks"), dict) else {}
    director_effects = [item for item in list(director.get("expected_effects") or []) if isinstance(item, dict)]
    director_timings = [item for item in list(director.get("timing_checks") or []) if isinstance(item, dict)]
    visual_checks = visual.get("checks") if isinstance(visual.get("checks"), dict) else {}

    def status_from(condition: bool, pending: bool = False) -> str:
        if condition:
            return "pass"
        return "pending" if pending else "fail"

    checks = [
        {
            "id": "prompt_plan",
            "label": "Prompt plan",
            "status": status_from(scenario_qa.get("status") == "pass" and director.get("status") == "pass"),
            "detail": "Prompt was converted into deterministic steps.",
        },
        {
            "id": "prompt_timing",
            "label": "Prompt timing",
            "status": status_from(
                checks_src.get("prompt_compliance") is True
                and not any(item.get("status") == "fail" for item in director_timings)
            ),
            "detail": "Literal timing checks passed.",
        },
        {
            "id": "action_stack",
            "label": "Action stack",
            "status": status_from(
                checks_src.get("steps_have_ids") is True
                and checks_src.get("steps_have_labels") is True
                and checks_src.get("steps_have_timing") is True
            ),
            "detail": "Every action has an id, label, and timing.",
        },
        {
            "id": "effect_mapping",
            "label": "Effect mapping",
            "status": status_from(not any(item.get("status") == "fail" for item in director_effects)),
            "detail": "Requested effects map to native motion effects.",
        },
        {
            "id": "dsl_source",
            "label": "DSL source",
            "status": status_from(
                contract.get("source_of_truth") == "motion_dsl"
                and bool(contract.get("preview_engine"))
                and bool(contract.get("render_engine")),
                pending=not contract,
            ),
            "detail": "Preview and final render use the same motion DSL contract.",
        },
        {
            "id": "visual_contract",
            "label": "Visual contract",
            "status": status_from(
                visual.get("status") == "pass"
                and visual_checks.get("preview_render_contract") == "motion_dsl",
                pending=not visual,
            ),
            "detail": "Visual QA contract is present and uses motion DSL.",
        },
        {
            "id": "figma_fidelity",
            "label": "Figma fidelity",
            "status": status_from(
                director.get("source_of_truth") == "figma-frame-png"
                and visual.get("status") == "pass",
                pending=not visual,
            ),
            "detail": "Settled state must match the exact Figma frame PNG.",
        },
    ]
    errors = [
        *[str(item) for item in list(scenario_qa.get("errors") or []) if item],
        *[str(item) for item in list(director.get("errors") or []) if item],
        *[str(item) for item in list(visual.get("errors") or []) if item],
    ]
    statuses = {item["status"] for item in checks}
    status = "fail" if "fail" in statuses else "pending" if "pending" in statuses else "pass"
    repair_actions = [dict(item) for item in list(repair.get("actions") or []) if isinstance(item, dict)]
    normalized_repair = {
        "attempted": bool(repair.get("attempted") or repair_actions),
        "status": str(repair.get("status") or ("repaired" if repair_actions else "not-needed")),
        "actions": repair_actions,
    }
    return {
        "version": 1,
        "name": "motion-qa-gate",
        "status": status,
        "blocking": status != "pass",
        "checks": checks,
        "repair": normalized_repair,
        "errors": errors,
        "warnings": [str(item) for item in list(visual.get("warnings") or []) if item],
        "summary": f"{sum(item['status'] == 'pass' for item in checks)}/{len(checks)} checks passed",
    }


def _step(step_id: str, label: str, effect: str, target: str, start: Any, duration: Any, **extra: Any) -> dict[str, Any]:
    start_value = _round_time(start)
    duration_value = _round_time(duration)
    result = {
        "id": step_id,
        "label": _clean_text(label, _effect_label(effect)),
        "effect": str(effect or "static-reveal"),
        "target": _target_label(str(target or "")),
        "start": start_value,
        "duration": duration_value,
        "end": _round_time(start_value + duration_value),
    }
    for key, value in extra.items():
        if value is not None:
            result[key] = value
    return result


def _scenario_qa(steps: list[dict[str, Any]], total_duration: float, prompt: str = "", scope: str = "") -> dict[str, Any]:
    errors: list[str] = []
    ids: list[str] = []
    for index, step in enumerate(steps):
        step_id = str(step.get("id") or "")
        if not step_id:
            errors.append(f"step {index + 1}: missing id")
        else:
            ids.append(step_id)
        if not str(step.get("label") or "").strip():
            errors.append(f"{step_id or index + 1}: missing label")
        if not str(step.get("effect") or "").strip():
            errors.append(f"{step_id or index + 1}: missing effect")
        start = _num(step.get("start"), -1)
        duration = _num(step.get("duration"), -1)
        end = _num(step.get("end"), -1)
        if start < -0.001 or duration < -0.001:
            errors.append(f"{step_id or index + 1}: invalid timing")
        if abs((start + duration) - end) > 0.015:
            errors.append(f"{step_id or index + 1}: end does not match start + duration")
        if total_duration > 0 and end > total_duration + 0.05:
            errors.append(f"{step_id or index + 1}: extends past motion duration")
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        errors.append(f"duplicate step ids: {', '.join(duplicates[:8])}")
    prompt_errors = _prompt_compliance_errors(steps, prompt, scope) if prompt else []
    errors.extend(prompt_errors)
    ordered = all(_num(steps[index].get("start"), 0) <= _num(steps[index + 1].get("start"), 0) + 0.001 for index in range(max(0, len(steps) - 1)))
    return {
        "status": "fail" if errors else "pass",
        "checks": {
            "steps_have_ids": bool(steps) and not any("missing id" in item for item in errors),
            "steps_have_labels": bool(steps) and not any("missing label" in item for item in errors),
            "steps_have_timing": bool(steps) and not any("timing" in item or "end does not match" in item for item in errors),
            "steps_ordered_by_start": ordered,
            "steps_within_duration": not any("extends past" in item for item in errors),
            "prompt_compliance": not prompt_errors,
            "motion_director_contract": not errors,
        },
        "errors": errors,
    }


def build_selected_layer_prompt_scenario(
    prompt: str,
    operation: dict[str, Any],
    actions: list[dict[str, Any]],
    total_duration: float,
) -> dict[str, Any]:
    target_name = _clean_text(operation.get("target_layer_name"), operation.get("target_layer_id") or "selected layer")
    steps: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
        intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
        start = phase_plan.get("start", intent.get("start", 0))
        duration = phase_plan.get("duration", intent.get("duration", 0))
        preset = str(action.get("preset") or intent.get("type") or "custom-dsl")
        steps.append(
            _step(
                str(action.get("id") or f"action-{index}"),
                str(action.get("label") or _effect_label(preset)),
                preset,
                target_name,
                start,
                duration,
                order=index,
                action_id=str(action.get("id") or ""),
                operation_id=str(action.get("operation_id") or operation.get("id") or ""),
                anchor=phase_plan.get("anchor") or intent.get("anchor"),
                source="action-stack",
            )
        )
    scenario = {
        "version": 1,
        "scope": "selected-layer",
        "source": "deterministic-motion-director",
        "prompt": _clean_text(prompt),
        "operation_id": str(operation.get("id") or ""),
        "operation_type": str(operation.get("type") or operation.get("mode") or ""),
        "target": target_name,
        "total_duration": _round_time(total_duration),
        "steps": steps,
    }
    scenario["qa"] = _scenario_qa(steps, _num(total_duration, 0.0), prompt, "selected-layer")
    scenario["motion_director"] = _director_contract(steps, prompt, "selected-layer", scenario["qa"])
    scenario["motion_qa_gate"] = build_motion_qa_gate(scenario)
    return scenario


def build_whole_frame_prompt_scenario(prompt: str, phase_plan: dict[str, Any]) -> dict[str, Any]:
    duration = _num(phase_plan.get("duration") or phase_plan.get("minimum_duration"), 0.0)
    steps: list[dict[str, Any]] = []
    phases = [phase for phase in list(phase_plan.get("phases") or []) if isinstance(phase, dict)]
    for phase in phases:
        phase_id = str(phase.get("id") or f"phase-{len(steps) + 1}")
        preset = str(phase.get("preset") or phase_id)
        if phase_id == "hold" or preset == "static":
            continue
        if phase_id == "build" and isinstance(phase.get("subphases"), list):
            for subphase in [item for item in list(phase.get("subphases") or []) if isinstance(item, dict)]:
                sub_id = str(subphase.get("id") or f"build-{len(steps) + 1}")
                sub_preset = str(subphase.get("preset") or preset)
                target = str(subphase.get("target") or phase.get("visibility") or "visible layers")
                steps.append(
                    _step(
                        f"scene-{sub_id}",
                        f"{_target_label(target).title()}: {_effect_label(sub_preset)}",
                        sub_preset,
                        target,
                        subphase.get("start", phase.get("start", 0)),
                        subphase.get("duration", phase.get("duration", 0)),
                        order=len(steps) + 1,
                        phase_id=phase_id,
                        subphase_id=sub_id,
                        source="phase-plan",
                    )
                )
            continue
        target = str(phase.get("visibility") or phase.get("target") or "whole frame")
        label_target = "Background" if phase_id == "intro" else "Outro" if phase_id == "outro" else _target_label(target).title()
        steps.append(
            _step(
                f"scene-{phase_id}",
                f"{label_target}: {_effect_label(preset)}",
                preset,
                target,
                phase.get("start", 0),
                phase.get("duration", 0),
                order=len(steps) + 1,
                phase_id=phase_id,
                anchor=phase.get("anchor"),
                source="phase-plan",
            )
        )
    camera = phase_plan.get("camera") if isinstance(phase_plan.get("camera"), dict) else None
    if camera and not any(str(step.get("effect") or "") in {"camera-push", "camera-pull", "pan"} for step in steps):
        effect = str(camera.get("id") or "scene-camera")
        steps.append(
            _step(
                f"scene-camera-{effect}",
                _effect_label(effect),
                effect,
                "whole frame",
                camera.get("start", 0),
                camera.get("duration", duration),
                order=len(steps) + 1,
                phase_id="camera",
                source="post-composite-camera",
            )
        )
        steps = sorted(steps, key=lambda item: (_num(item.get("start"), 0), _num(item.get("order"), 0)))
        for order, step in enumerate(steps, start=1):
            step["order"] = order
    scenario = {
        "version": 1,
        "scope": "whole-frame",
        "source": "deterministic-motion-director",
        "prompt": _clean_text(prompt),
        "total_duration": _round_time(duration),
        "steps": steps,
        "constraints": {
            "preview_render_source": "motion_dsl",
            "settled_source_of_truth": "figma-frame-png",
        },
    }
    scenario["qa"] = _scenario_qa(steps, duration, prompt, "whole-frame")
    scenario["motion_director"] = _director_contract(steps, prompt, "whole-frame", scenario["qa"])
    scenario["motion_qa_gate"] = build_motion_qa_gate(scenario)
    return scenario
