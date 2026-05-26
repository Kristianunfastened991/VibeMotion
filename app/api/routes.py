from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.models.schemas import (
    AgentEditRequest,
    ClipUpdateRequest,
    CutRange,
    FigmaAssetsRequest,
    FigmaImportRequest,
    FigmaLayerUpdateRequest,
    FigmaLayerMotionPromptRequest,
    FigmaPluginAssetImportRequest,
    FigmaPluginAssetsRequest,
    JobStatus,
    LtxLayerVideoApplyRequest,
    LtxLayerVideoRequest,
    MotionSpec,
    MotionAnimationPromptRequest,
    MotionPromptRequest,
    MotionUpdateRequest,
    NativeMotionCueApplyRequest,
    NativeMotionCuePreviewRequest,
    ProjectActionRequest,
    ProjectNoteRequest,
    StylePresetSelectRequest,
    TimelineDeleteRequest,
    TimelineSplitRequest,
    EditPlan,
)
from app.services.agent_edit import build_agent_edit_artifacts, self_eval_preview
from app.services.figma_import import FigmaImportError, import_figma_node, list_figma_assets, refresh_motion_from_figma
from app.services.figma_plugin import (
    compare_plugin_asset_to_render,
    list_plugin_assets,
    motion_from_plugin_asset,
    refresh_motion_from_plugin_asset,
    save_plugin_assets,
)
from app.services.jobs import JobCancelled, cancel_job, create_job, get_job, get_job_cancel_event, submit_job, update_job
from app.services.layer_motion import describe_motion_plan, describe_motion_units, frame_choreography_required_duration, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.ltx_video import LtxGenerationCancelled, cancel_ltx_generation, generate_ltx_layer_preview
from app.services.media import detect_duration, detect_video_size
from app.services.motion import (
    apply_animation_prompt,
    fit_motion_to_canvas,
    motion_asset_signature,
    place_motion_on_quiet_area,
    prompt_to_motion,
    render_motion_asset,
)
from app.services.motion_director import (
    DEFAULT_MOTION_BLOCK_DURATION,
    build_context_motions,
    build_directed_motion,
    extract_user_motion_text,
    is_general_motion_request,
    normalize_motion_type,
    variant_motion,
)
from app.services.motion_intent import attach_frame_motion_contract, build_layer_motion_recipe_after_delete, build_layer_motion_recipe_from_prompt
from app.services.planning import generate_edit_plan, source_plan
from app.services.projects import add_note, clear_projects, create_project, list_projects, load_state, project_dir, save_state
from app.services.render import render_project_preview
from app.services.style_presets import SOFT_NEUMORPHISM_PRESET_ID, apply_style_to_motion, create_style_preset, list_style_presets, load_style_preset
from app.services.timeline import build_timeline, normalize_clip_handles
from app.services.transcription import load_cached_transcript, transcribe_with_faster_whisper


router = APIRouter()


HISTORY_VOLATILE_KEYS = {"video_asset_path", "asset_version", "asset_signature", "motion_units"}


def _motion_prompt_mode(value: str | None) -> str:
    mode = str(value or "replace").strip().casefold()
    if mode in {"append", "add", "extend"}:
        return "append"
    return "replace"


def _strip_stored_motion_prompt(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for prefix in ("Frame choreography prompt:", "Animation prompt:"):
        if text.casefold().startswith(prefix.casefold()):
            return text[len(prefix):].strip()
    return text


def _compose_motion_prompt(existing: str | None, addition: str, mode: str) -> str:
    addition_text = _strip_stored_motion_prompt(addition)
    if _motion_prompt_mode(mode) != "append":
        return addition_text
    existing_text = _strip_stored_motion_prompt(existing)
    if not existing_text:
        return addition_text
    if not addition_text:
        return existing_text
    existing_normalized = re.sub(r"\s+", " ", existing_text).casefold()
    addition_normalized = re.sub(r"\s+", " ", addition_text).casefold()
    if addition_normalized == existing_normalized:
        return existing_text
    if addition_normalized.startswith(existing_normalized):
        delta = addition_text[len(existing_text):].strip(" \t\r\n:.-")
        if not delta:
            return existing_text
        addition_text = delta
    return f"{existing_text}\nThen add this motion instruction: {addition_text}"


def _motion_action_id() -> str:
    return f"act-{uuid.uuid4().hex[:10]}"


def _motion_action_identity(action: dict, index: int) -> str:
    return str(action.get("id") or action.get("action_id") or action.get("recipe_id") or f"legacy-{index}")


def _motion_recipe_actions(recipe: dict | None) -> list[dict]:
    if not isinstance(recipe, dict) or not recipe:
        return []
    actions = recipe.get("motion_actions")
    if isinstance(actions, list):
        result = [dict(action) for action in actions if isinstance(action, dict)]
    elif recipe.get("motion_dsl") or recipe.get("intro") or recipe.get("outro") or recipe.get("preset"):
        result = [dict(recipe)]
    else:
        result = []
    clean: list[dict] = []
    for index, action in enumerate(result):
        action.pop("motion_actions", None)
        action.setdefault("id", _motion_action_identity(action, index))
        clean.append(action)
    return clean


def _motion_action_duration(action: dict) -> float:
    if not isinstance(action, dict):
        return 0.0
    required = 0.0
    phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
    for key in ("minimum_duration", "duration"):
        try:
            value = float(phase_plan.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            required = max(required, value)
    intro = action.get("intro") if isinstance(action.get("intro"), dict) else {}
    try:
        required = max(required, float(intro.get("delay") or 0) + float(intro.get("duration") or 0))
    except (TypeError, ValueError):
        pass
    dsl = action.get("motion_dsl") if isinstance(action.get("motion_dsl"), dict) else {}
    for frame in list(dsl.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        try:
            required = max(required, float(frame.get("time") or 0))
        except (TypeError, ValueError):
            continue
    outro = action.get("outro") if isinstance(action.get("outro"), dict) else {}
    try:
        required = max(required, float(outro.get("duration") or 0))
    except (TypeError, ValueError):
        pass
    return max(0.0, required)


def _motion_recipe_from_actions(actions: list[dict], prompt: str | None = None) -> dict | None:
    clean_actions: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        clean = dict(action)
        clean.pop("motion_actions", None)
        clean["id"] = str(clean.get("id") or _motion_action_id())
        clean_actions.append(clean)
    if not clean_actions:
        return None
    latest = clean_actions[-1]
    labels = [str(action.get("label") or action.get("preset") or action.get("prompt") or "motion").strip() for action in clean_actions]
    prompts = [str(action.get("prompt") or "").strip() for action in clean_actions if str(action.get("prompt") or "").strip()]
    tags: list[str] = []
    for action in clean_actions:
        for tag in list(action.get("tags") or []):
            text = str(tag or "").strip()
            if text and text not in tags:
                tags.append(text)
    if "motion-stack" not in tags:
        tags.append("motion-stack")
    required = max((_motion_action_duration(action) for action in clean_actions), default=0.0)
    recipe = {
        "id": f"recipe-stack-{uuid.uuid4().hex[:10]}",
        "prompt": _strip_stored_motion_prompt(prompt) if prompt else "\n".join(prompts[-8:]),
        "preset": latest.get("preset") if len(clean_actions) == 1 else "motion-stack",
        "time_mode": "action-stack",
        "label": labels[-1] if len(clean_actions) == 1 else f"{len(clean_actions)} motion actions",
        "tags": tags,
        "motion_actions": clean_actions,
        "phase_plan": {
            "scope": "selected-layer",
            "mode": "action-stack",
            "action_count": len(clean_actions),
            "minimum_duration": required,
            "actions": [
                {
                    "id": action.get("id"),
                    "label": action.get("label") or action.get("preset"),
                    "preset": action.get("preset"),
                    "duration": _motion_action_duration(action),
                }
                for action in clean_actions
            ],
        },
    }
    for key in ("intro", "hold", "outro", "motion_dsl", "transform_reference"):
        if key in latest:
            recipe[key] = latest[key]
    return recipe


MOTION_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "одну": 1,
    "один": 1,
    "одна": 1,
    "первую": 1,
    "первой": 1,
    "две": 2,
    "два": 2,
    "вторую": 2,
    "второй": 2,
    "три": 3,
    "третью": 3,
    "третьей": 3,
    "четыре": 4,
    "четвертую": 4,
    "четвертой": 4,
    "пять": 5,
    "пятую": 5,
    "пятой": 5,
    "шесть": 6,
    "шестую": 6,
    "шестой": 6,
    "семь": 7,
    "седьмую": 7,
    "седьмой": 7,
    "восемь": 8,
    "восьмую": 8,
    "восьмой": 8,
    "девять": 9,
    "девятую": 9,
    "девятой": 9,
    "десять": 10,
    "десятую": 10,
    "десятой": 10,
}

MOTION_NUMBER_RE = r"\d+(?:\.\d+)?|" + "|".join(re.escape(key) for key in sorted(MOTION_NUMBER_WORDS, key=len, reverse=True))
MOTION_SECOND_UNIT_RE = r"(?:сек(?:унд(?:а|у|ы|е|ой)?|\.?)?|seconds?|second|secs?|s)"


def _motion_text(prompt: str) -> str:
    text = str(prompt or "").casefold().replace(",", ".").replace("ё", "е")
    return re.sub(r"[\u2010-\u2015_-]+", " ", text)


def _motion_number_value(raw: str) -> float | None:
    text = str(raw or "").casefold().replace(",", ".").replace("ё", "е")
    if text in MOTION_NUMBER_WORDS:
        return float(MOTION_NUMBER_WORDS[text])
    try:
        return max(0.0, float(text))
    except ValueError:
        return None


def _motion_second_mentions(prompt: str) -> list[tuple[float, int, int, str]]:
    text = _motion_text(prompt)
    pattern = rf"\b({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}\b"
    mentions: list[tuple[float, int, int, str]] = []
    for match in re.finditer(pattern, text):
        value = _motion_number_value(match.group(1))
        if value is None:
            continue
        mentions.append((value, match.start(), match.end(), text))
    return mentions


def _seconds_from_prompt(prompt: str, fallback: float = 0.0) -> float:
    mentions = _motion_second_mentions(prompt)
    return mentions[0][0] if mentions else fallback


def _duration_seconds_from_prompt(prompt: str, fallback: float = 0.75) -> float:
    mentions = _motion_second_mentions(prompt)
    if not mentions:
        return fallback
    for value, start, end, text in mentions:
        context = text[max(0, start - 48) : min(len(text), end + 16)]
        if re.search(r"длительн|продолжительн|в\s*течени|за\s*$|за\s+\d|duration|for|over|на\s*$|растян|длин", context):
            return value
    return mentions[0][0]


def _relative_delay_seconds_from_prompt(prompt: str, fallback: float = 0.0) -> float:
    text = _motion_text(prompt)
    if re.search(r"(?:через\s+секунду|after\s+(?:a\s+)?second)\b", text):
        return 1.0
    pattern = rf"(?:через|after)\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}\b"
    match = re.search(pattern, text)
    if not match:
        return fallback
    return _motion_number_value(match.group(1)) or fallback


def _absolute_start_seconds_from_prompt(prompt: str) -> float | None:
    text = _motion_text(prompt)
    patterns = [
        rf"(?:на|at)\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*(?:секунде|секунду|second\s*mark|sec\s*mark|s\b)",
        rf"(?:с|from|start(?:ing)?\s+at)\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _motion_number_value(match.group(1))
    return None


def _has_fade_in_intent(text: str) -> bool:
    return bool(re.search(r"fade\s*in|fadein|фейд\s*ин|фейдин|появ|прояв|из\s+прозрач|opacity\s*0\s*(?:to|->)\s*1", text))


def _has_fade_out_intent(text: str) -> bool:
    return bool(re.search(r"fade\s*out|fadeout|фейд\s*аут|фейдаут|исчез|пропад|убер|в\s+прозрач|opacity\s*1\s*(?:to|->)\s*0", text))


def _has_drop_intent(text: str) -> bool:
    return bool(re.search(r"пада|упад|паден|рух|вниз|камнем|камень|гравитац|ускор|drop|fall|down|stone|gravity", text))


def _has_retime_intent(text: str) -> bool:
    return bool(re.search(r"длин|удлин|дольше|растян|растяг|медлен|duration|longer|slower|make.+long|stretch", text))


def _target_action_index_for_prompt(prompt: str, actions: list[dict]) -> int:
    if not actions:
        return -1
    text = _motion_text(prompt)
    wants_fade_in = _has_fade_in_intent(text)
    wants_fade_out = _has_fade_out_intent(text)
    wants_drop = _has_drop_intent(text)
    for index in range(len(actions) - 1, -1, -1):
        action = actions[index]
        haystack = " ".join(
            str(action.get(key) or "")
            for key in ("preset", "label", "prompt")
        ).casefold()
        if wants_fade_in and ("fade-in" in haystack or "fade in" in haystack or "фейдин" in haystack or "фейд ин" in haystack):
            return index
        if wants_fade_out and ("fade-out" in haystack or "fade out" in haystack or "фейдаут" in haystack or "фейд аут" in haystack):
            return index
        if wants_drop and ("drop" in haystack or "fall" in haystack or "пад" in haystack):
            return index
    return len(actions) - 1


def _reference_time_for_prompt(prompt: str, actions: list[dict]) -> float:
    index = _target_action_index_for_prompt(prompt, actions)
    if index >= 0:
        return _motion_action_duration(actions[index])
    return max((_motion_action_duration(action) for action in actions), default=0.0)


def _retime_motion_action(action: dict, duration: float) -> dict:
    duration = max(0.05, min(60.0, float(duration or 0)))
    next_action = dict(action)
    dsl = next_action.get("motion_dsl") if isinstance(next_action.get("motion_dsl"), dict) else {}
    keyframes = [dict(frame) for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
    if keyframes:
        times = []
        for frame in keyframes:
            try:
                times.append(float(frame.get("time") or 0))
            except (TypeError, ValueError):
                pass
        start = min(times) if times else 0.0
        end = max(times) if times else start
        span = max(0.001, end - start)
        for frame in keyframes:
            try:
                current = float(frame.get("time") or 0)
            except (TypeError, ValueError):
                current = start
            frame["time"] = round(start + ((current - start) / span) * duration, 4)
        next_action["motion_dsl"] = {**dsl, "keyframes": keyframes}
    intro = next_action.get("intro") if isinstance(next_action.get("intro"), dict) else None
    if intro is not None:
        next_action["intro"] = {**intro, "duration": duration}
    phase_plan = next_action.get("phase_plan") if isinstance(next_action.get("phase_plan"), dict) else {}
    dsl = next_action.get("motion_dsl") if isinstance(next_action.get("motion_dsl"), dict) else {}
    keyframe_end = 0.0
    for frame in list(dsl.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        try:
            keyframe_end = max(keyframe_end, float(frame.get("time") or 0))
        except (TypeError, ValueError):
            continue
    next_action["phase_plan"] = {**phase_plan, "duration": duration, "minimum_duration": max(duration, keyframe_end)}
    preset = str(next_action.get("preset") or "")
    if preset == "fade-in":
        next_action["label"] = f"Fade in {duration:g}s"
    elif preset == "fade-out":
        next_action["label"] = f"Fade out {duration:g}s"
    else:
        next_action["label"] = str(next_action.get("label") or next_action.get("preset") or "Motion action")
    return next_action


def _modify_existing_motion_action(prompt: str, actions: list[dict]) -> list[dict] | None:
    if not actions:
        return None
    text = _motion_text(prompt)
    wants_retime = _has_retime_intent(text)
    seconds = _duration_seconds_from_prompt(prompt, 0.0)
    if wants_retime and seconds > 0:
        updated = [dict(action) for action in actions]
        target_index = _target_action_index_for_prompt(prompt, updated)
        if target_index < 0:
            target_index = len(updated) - 1
        updated[target_index] = _retime_motion_action(updated[target_index], seconds)
        updated[target_index]["prompt"] = _strip_stored_motion_prompt(prompt)
        intent = updated[target_index].get("intent") if isinstance(updated[target_index].get("intent"), dict) else {}
        updated[target_index]["intent"] = {
            **intent,
            "duration": seconds,
            "edit": "retime",
            "source": "deterministic-dialog",
        }
        return updated
    return None


def _deterministic_layer_motion_action(prompt: str, layer: dict, existing_actions: list[dict] | None = None) -> dict | None:
    text = _motion_text(prompt)
    kind = str(layer.get("kind") or "layer")
    seconds = _seconds_from_prompt(prompt, 0.0)
    duration = _duration_seconds_from_prompt(prompt, 0.75)
    base = {"x": 0, "y": 0, "scale": 1, "scaleX": 1, "scaleY": 1, "rotate": 0, "skewX": 0, "skewY": 0, "opacity": 1, "blur": 0, "brightness": 1}

    wants_fade_out = _has_fade_out_intent(text)
    wants_fade_in = _has_fade_in_intent(text)
    wants_drop = _has_drop_intent(text)

    if wants_fade_in and not wants_drop:
        end = max(0.05, duration)
        return {
            "id": _motion_action_id(),
            "prompt": _strip_stored_motion_prompt(prompt),
            "preset": "fade-in",
            "time_mode": "absolute-action",
            "label": f"Fade in {end:g}s",
            "tags": [kind, "motion-action", "fade-in"],
            "intent": {"type": "fade-in", "scope": "selected-layer", "start": 0, "duration": end, "source": "deterministic-dialog"},
            "intro": {"type": "fade", "direction": "center", "delay": 0, "duration": end, "distance": 0, "ease": "sine"},
            "motion_dsl": {"version": 1, "keyframes": [{**base, "time": 0, "opacity": 0}, {**base, "time": end, "opacity": 1, "ease": "sine"}], "effects": []},
            "phase_plan": {"scope": "selected-layer", "preset": "fade-in", "start": 0, "duration": end, "minimum_duration": end},
        }

    if wants_fade_out and not wants_drop:
        end = max(0.05, duration)
        return {
            "id": _motion_action_id(),
            "prompt": _strip_stored_motion_prompt(prompt),
            "preset": "fade-out",
            "time_mode": "absolute-action",
            "label": f"Fade out {end:g}s",
            "tags": [kind, "motion-action", "fade-out"],
            "intent": {"type": "fade-out", "scope": "selected-layer", "start": 0, "duration": end, "source": "deterministic-dialog"},
            "intro": {"type": "fade", "direction": "center", "delay": 0, "duration": end, "distance": 0, "ease": "sine"},
            "motion_dsl": {"version": 1, "keyframes": [{**base, "time": 0, "opacity": 1}, {**base, "time": end, "opacity": 0, "ease": "sine"}], "effects": []},
            "phase_plan": {"scope": "selected-layer", "preset": "fade-out", "start": 0, "duration": end, "minimum_duration": end},
        }

    if wants_drop:
        explicit_start = _absolute_start_seconds_from_prompt(prompt)
        if explicit_start is not None:
            delay = max(0.0, explicit_start)
        else:
            relative_delay = _relative_delay_seconds_from_prompt(prompt, 0.0)
            after_existing = _reference_time_for_prompt(prompt, list(existing_actions or []))
            delay = after_existing + relative_delay if re.search(r"после|after|когда", text) else relative_delay
        fall_duration = 1.15
        end = delay + fall_duration
        return {
            "id": _motion_action_id(),
            "prompt": _strip_stored_motion_prompt(prompt),
            "preset": "gravity-drop-fade",
            "time_mode": "absolute-action",
            "label": f"Gravity drop at {delay:g}s",
            "tags": [kind, "motion-action", "gravity-drop-fade", "dynamic"],
            "intent": {"type": "gravity-drop-fade", "scope": "selected-layer", "start": delay, "duration": fall_duration, "source": "deterministic-dialog"},
            "motion_dsl": {
                "version": 1,
                "keyframes": [
                    {**base, "time": 0},
                    {**base, "time": round(delay, 4)},
                    {**base, "time": round(delay + fall_duration * 0.32, 4), "y": 32, "rotate": 1.5, "opacity": 1, "ease": "gravity"},
                    {**base, "time": round(end, 4), "y": 260, "rotate": 7, "opacity": 0, "blur": 1.5, "ease": "gravity"},
                ],
                "effects": [],
            },
            "phase_plan": {"scope": "selected-layer", "preset": "gravity-drop-fade", "start": delay, "duration": fall_duration, "minimum_duration": end},
        }

    return None


def _reset_figma_layer_motion(layers) -> list[dict]:
    clean_layers = []
    for layer in list(layers or []):
        layer_id = str(layer.get("id") or "")
        if layer_id.startswith("__frame_choreo_"):
            continue
        clean_layer = dict(layer)
        for key in (
            "motion_recipe",
            "render_cluster_source",
            "choreo_static_skip",
            "whole_frame_static_skip",
            "cluster_child_ids",
            "cluster_parent_id",
            "cluster_source_kind",
            "cluster_source_mask_id",
            "motion_internal",
            "source_crop_key_transparent",
            "mask_role",
        ):
            clean_layer.pop(key, None)
        clean_layers.append(clean_layer)
    return clean_layers


def _neutralize_figma_outer_motion(motion: MotionSpec) -> MotionSpec:
    if getattr(motion, "source_type", "generated") != "figma":
        return motion
    return motion.model_copy(
        update={
            "animation": "fade",
            "enter_animation": "none",
            "exit_animation": "none",
            "enter_from": "center",
            "exit_to": "center",
            "enter_duration": 0.05,
            "exit_duration": 0.05,
        }
    )


def _apply_frame_choreography_plan(motion: MotionSpec, prompt: str) -> MotionSpec:
    required_duration = frame_choreography_required_duration(prompt, float(motion.duration or 0))
    neutral_motion = _neutralize_figma_outer_motion(motion)
    planning_motion = neutral_motion.model_copy(
        update={
            "duration": max(float(neutral_motion.duration or 0), required_duration),
            "figma_layers": _reset_figma_layer_motion(neutral_motion.figma_layers),
        }
    )
    figma_layers = plan_frame_choreography(prompt, planning_motion)
    motion_plan = describe_motion_plan(figma_layers)
    figma_layers = attach_frame_motion_contract(prompt, motion.id, figma_layers, motion_plan)
    motion_plan = describe_motion_plan(figma_layers)
    return neutral_motion.model_copy(
        update={
            "duration": planning_motion.duration,
            "figma_layers": figma_layers,
            "motion_plan": motion_plan,
            "motion_units": describe_motion_units(figma_layers),
            "enter_animation": "none",
            "exit_animation": "none",
            "enter_from": "center",
            "exit_to": "center",
            "prompt": f"Frame choreography prompt: {prompt}",
        }
    )


def _stored_frame_choreography_prompt(motion: MotionSpec) -> str:
    candidates: list[str] = []
    raw_prompt = _strip_stored_motion_prompt(getattr(motion, "prompt", "") or "")
    if raw_prompt:
        candidates.append(raw_prompt)
    plan = getattr(motion, "motion_plan", None)
    operation = plan.get("motion_operation") if isinstance(plan, dict) and isinstance(plan.get("motion_operation"), dict) else None
    if operation and operation.get("prompt"):
        candidates.append(_strip_stored_motion_prompt(str(operation.get("prompt") or "")))
    for layer in list(getattr(motion, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        operation = phase_plan.get("motion_operation") if isinstance(phase_plan, dict) and isinstance(phase_plan.get("motion_operation"), dict) else None
        if operation and operation.get("prompt"):
            candidates.append(_strip_stored_motion_prompt(str(operation.get("prompt") or "")))
    for candidate in candidates:
        if candidate and should_use_frame_choreography_prompt(candidate):
            return candidate
    return ""


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _action_phase_timing(action: dict) -> tuple[float, float]:
    phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
    intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
    start = _safe_float(phase_plan.get("start", intent.get("start", 0)), 0.0)
    duration = _safe_float(phase_plan.get("duration", intent.get("duration", 0)), 0.0)
    if duration <= 0:
        keyframes = list(((action.get("motion_dsl") or {}).get("keyframes") or [])) if isinstance(action.get("motion_dsl"), dict) else []
        times = [_safe_float(frame.get("time"), 0.0) for frame in keyframes if isinstance(frame, dict)]
        if times:
            duration = max(0.0, max(times) - start)
    return max(0.0, start), max(0.05, duration)


def _is_end_anchored_action(action: dict, old_duration: float) -> bool:
    phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
    intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
    if str(phase_plan.get("anchor") or intent.get("anchor") or "").strip().casefold() == "end":
        return True
    preset = str(action.get("preset") or intent.get("type") or phase_plan.get("preset") or "").strip().casefold()
    if preset not in {"fade-out", "gravity-drop-fade", "drop-bounce", "full-frame-fade-out"}:
        return False
    start, duration = _action_phase_timing(action)
    return abs((start + duration) - old_duration) <= 0.15


def _retime_end_anchored_action(action: dict, old_duration: float, new_duration: float) -> dict:
    next_action = dict(action)
    old_start, action_duration = _action_phase_timing(next_action)
    new_start = max(0.0, float(new_duration) - action_duration)
    delta = new_start - old_start
    dsl = dict(next_action.get("motion_dsl") or {})
    keyframes = []
    for frame in list(dsl.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        next_frame = dict(frame)
        time_value = _safe_float(next_frame.get("time"), 0.0)
        if time_value > 0.001 and time_value >= old_start - 0.001:
            next_frame["time"] = round(max(0.0, time_value + delta), 4)
        keyframes.append(next_frame)
    if keyframes:
        dsl["keyframes"] = keyframes
    effects = []
    for effect in list(dsl.get("effects") or []):
        if not isinstance(effect, dict):
            continue
        next_effect = dict(effect)
        start_value = _safe_float(next_effect.get("start"), 0.0)
        if start_value >= old_start - 0.001:
            next_effect["start"] = round(max(0.0, start_value + delta), 4)
        effects.append(next_effect)
    if effects:
        dsl["effects"] = effects
    if keyframes or effects:
        next_action["motion_dsl"] = dsl
    phase_plan = dict(next_action.get("phase_plan") or {})
    phase_plan.update({"start": round(new_start, 4), "duration": action_duration, "minimum_duration": round(float(new_duration), 4), "anchor": "end"})
    next_action["phase_plan"] = phase_plan
    intent = dict(next_action.get("intent") or {})
    if intent:
        intent.update({"start": round(new_start, 4), "duration": action_duration, "anchor": "end"})
        next_action["intent"] = intent
    intro = dict(next_action.get("intro") or {})
    if intro:
        intro["delay"] = round(new_start, 4)
        intro["duration"] = action_duration
        next_action["intro"] = intro
    preset = str(next_action.get("preset") or intent.get("type") or "").strip().casefold()
    if preset == "fade-out":
        next_action["label"] = "Fade out at end"
    elif preset in {"gravity-drop-fade", "drop-bounce"}:
        next_action["label"] = "Gravity drop at end"
    return next_action


def _reanchor_layer_end_actions(motion: MotionSpec, old_duration: float, new_duration: float) -> MotionSpec:
    if abs(float(new_duration) - float(old_duration)) <= 0.001 or not getattr(motion, "figma_layers", None):
        return motion
    changed = False
    layers = []
    for layer in list(getattr(motion, "figma_layers", []) or []):
        next_layer = dict(layer)
        layer_changed = False
        recipe = next_layer.get("motion_recipe") if isinstance(next_layer.get("motion_recipe"), dict) else None
        if not recipe:
            layers.append(next_layer)
            continue
        next_recipe = dict(recipe)
        actions = list(next_recipe.get("motion_actions") or []) if isinstance(next_recipe.get("motion_actions"), list) else []
        if actions:
            next_actions = []
            for action in actions:
                if isinstance(action, dict) and _is_end_anchored_action(action, old_duration):
                    next_actions.append(_retime_end_anchored_action(action, old_duration, new_duration))
                    layer_changed = True
                    changed = True
                else:
                    next_actions.append(action)
            if layer_changed:
                next_recipe["motion_actions"] = next_actions
        elif _is_end_anchored_action(next_recipe, old_duration):
            next_recipe = _retime_end_anchored_action(next_recipe, old_duration, new_duration)
            layer_changed = True
            changed = True
        if layer_changed:
            next_layer["motion_recipe"] = next_recipe
        layers.append(next_layer)
    return motion.model_copy(update={"figma_layers": layers}) if changed else motion


def _scrub_history_value(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            if key in HISTORY_VOLATILE_KEYS or key in {"ltx_prompt", "prompt"}:
                continue
            if key == "ltx_preview" and isinstance(child, dict):
                cleaned[key] = {
                    preview_key: _scrub_history_value(preview_value)
                    for preview_key, preview_value in child.items()
                    if preview_key != "prompt"
                }
                continue
            cleaned[key] = _scrub_history_value(child)
        return cleaned
    if isinstance(value, list):
        return [_scrub_history_value(item) for item in value]
    return value


def _sanitize_timeline_snapshot(snapshot: dict) -> dict:
    return _scrub_history_value(snapshot)


def _timeline_snapshot(state) -> dict:
    return _sanitize_timeline_snapshot({
        "mode": state.mode,
        "status": state.status,
        "edit_plan": state.edit_plan.model_dump(mode="json") if state.edit_plan else None,
        "motions": [motion.model_dump(mode="json") for motion in state.motions],
    })


def _restore_timeline_snapshot(state, snapshot: dict) -> None:
    from app.models.schemas import EditPlan, MotionSpec

    prompts_by_layer: dict[tuple[str, str], str] = {}
    for motion in state.motions:
        for layer in motion.figma_layers:
            prompt = layer.get("ltx_prompt")
            layer_id = str(layer.get("id") or "")
            if layer_id and isinstance(prompt, str) and prompt:
                prompts_by_layer[(motion.id, layer_id)] = prompt

    snapshot = _sanitize_timeline_snapshot(snapshot)
    state.mode = snapshot.get("mode", state.mode)
    state.status = "timeline-updated"
    state.edit_plan = EditPlan.model_validate(snapshot["edit_plan"]) if snapshot.get("edit_plan") else None
    restored_motions = []
    for item in snapshot.get("motions", []):
        motion = MotionSpec.model_validate(item)
        layers = []
        changed = False
        for layer in motion.figma_layers:
            layer_id = str(layer.get("id") or "")
            prompt = prompts_by_layer.get((motion.id, layer_id))
            if prompt:
                next_layer = dict(layer)
                next_layer["ltx_prompt"] = prompt
                layers.append(next_layer)
                changed = True
            else:
                layers.append(layer)
        restored_motions.append(motion.model_copy(update={"figma_layers": layers}) if changed else motion)
    state.motions = restored_motions
    state.outputs.pop("preview", None)


def _push_history(state) -> None:
    state.undo_stack.append(_timeline_snapshot(state))
    state.undo_stack = state.undo_stack[-50:]
    state.redo_stack.clear()


def _estimate_duration(state) -> None:
    if state.edit_plan:
        state.edit_plan.estimated_duration = sum(max(0.0, item.end - item.start) for item in state.edit_plan.keep_ranges)


def _latest_motion_video_relpath(base: Path, motion_id: str) -> str | None:
    assets_dir = base / "assets"
    versioned = sorted(assets_dir.glob(f"{motion_id}-*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if versioned:
        return str(versioned[0].relative_to(base))
    legacy = assets_dir / f"{motion_id}.mp4"
    if legacy.exists():
        return str(legacy.relative_to(base))
    return None


def _repair_missing_motion_video_paths(state, base: Path) -> bool:
    changed = False
    for index, motion in enumerate(list(state.motions or [])):
        if getattr(motion, "source_type", "generated") != "figma":
            continue
        current = str(getattr(motion, "video_asset_path", "") or "").strip()
        if current and (base / current).exists():
            continue
        latest = _latest_motion_video_relpath(base, str(motion.id))
        if latest and latest != current:
            state.motions[index] = motion.model_copy(update={"video_asset_path": latest})
            changed = True
    return changed


def _render_motion_assets(state, base: Path) -> None:
    assets_dir = base / "assets"
    for index, motion in enumerate(state.motions):
        motion = _with_motion_units(motion)
        asset_path = render_motion_asset(motion, assets_dir)
        motion.asset_version = str(asset_path.stat().st_mtime_ns)
        motion.asset_signature = motion_asset_signature(motion)
        motion.video_asset_path = _latest_motion_video_relpath(base, motion.id)
        state.motions[index] = motion


def _with_motion_units(motion: MotionSpec) -> MotionSpec:
    if getattr(motion, "source_type", "generated") != "figma":
        return motion
    layers = list(getattr(motion, "figma_layers", []) or [])
    return motion.model_copy(
        update={
            "motion_plan": describe_motion_plan(layers),
            "motion_units": describe_motion_units(layers),
        }
    )


def _render_motion_with_version(motion: MotionSpec, assets_dir: Path) -> MotionSpec:
    motion = _with_motion_units(motion)
    asset_path = render_motion_asset(motion, assets_dir)
    base = assets_dir.parent
    return motion.model_copy(
        update={
            "asset_version": str(asset_path.stat().st_mtime_ns),
            "asset_signature": motion_asset_signature(motion),
            "video_asset_path": _latest_motion_video_relpath(base, motion.id),
        }
    )


def _selected_style_preset_id(state) -> str:
    preset_id = str(getattr(state, "style_preset_id", None) or SOFT_NEUMORPHISM_PRESET_ID)
    return preset_id if load_style_preset(preset_id) is not None else SOFT_NEUMORPHISM_PRESET_ID


def _lock_generated_motions_to_soft_neumorphism(state) -> bool:
    changed = False
    preset_id = _selected_style_preset_id(state)
    if getattr(state, "style_preset_id", None) != preset_id:
        state.style_preset_id = preset_id
        changed = True
    locked = []
    style_profile = load_style_preset(preset_id) or load_style_preset(SOFT_NEUMORPHISM_PRESET_ID)
    tokens = dict((style_profile or {}).get("tokens") or {})
    target_family = str((style_profile or {}).get("style_family") or tokens.get("shape_language") or "")
    target_preset = preset_id
    target_background = str(tokens.get("overlay_background") or "rgba(242, 241, 237, 0.98)")
    target_accent = str(tokens.get("accent") or "#2b8cff")
    for motion in state.motions:
        motion_plan = dict(motion.motion_plan or {})
        style = motion_plan.get("style") if isinstance(motion_plan.get("style"), dict) else {}
        tokens = style.get("tokens") if isinstance(style.get("tokens"), dict) else {}
        style_family = str(style.get("style_family") or tokens.get("shape_language") or "")
        needs_soft_lock = (
            getattr(motion, "source_type", "generated") != "figma"
            and (
                motion.design_preset != target_preset
                or style_family != target_family
                or str(motion.background or "") != target_background
                or str(motion.accent or "") != target_accent
            )
        )
        if needs_soft_lock:
            motion_plan = dict(motion.motion_plan or {})
            motion_plan["style"] = style_profile
            locked.append(
                motion.model_copy(
                    update={
                        "design_preset": target_preset,
                        "background": target_background,
                        "accent": target_accent,
                        "motion_plan": motion_plan,
                    }
                )
            )
            changed = True
        else:
            locked.append(motion)
    if changed:
        state.motions = locked
        state.outputs.pop("preview", None)
    return changed


def _require_project(project_id: str):
    try:
        state = load_state(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    base = project_dir(project_id)
    needs_save = False
    if _lock_generated_motions_to_soft_neumorphism(state):
        preset = load_style_preset(_selected_style_preset_id(state)) or {}
        add_note(state, f"Generated motion styles locked to {preset.get('name') or _selected_style_preset_id(state)}.")
        needs_save = True
    if _repair_missing_motion_video_paths(state, base):
        add_note(state, "Repaired a stale motion video asset reference.")
        needs_save = True
    if needs_save:
        save_state(state)
    return state


def _fit_motion_for_project(state, base: Path, motion):
    if not state.source_video:
        return motion
    width, height = detect_video_size(base / state.source_video)
    return fit_motion_to_canvas(motion, width, height)


def _sync_figma_text_layer(motion: MotionSpec, text: str) -> MotionSpec:
    if getattr(motion, "source_type", "generated") != "figma" or not motion.figma_layers:
        return motion
    layers = []
    changed = False
    for layer in motion.figma_layers:
        if not changed and layer.get("kind") == "text":
            layers.append({**layer, "text": text})
            changed = True
        else:
            layers.append(layer)
    return motion.model_copy(update={"figma_layers": layers}) if changed else motion


def _recipe_settled_duration(recipe: dict) -> float:
    if not isinstance(recipe, dict):
        return 0.0
    if isinstance(recipe.get("motion_actions"), list):
        return max((_recipe_settled_duration(action) for action in _motion_recipe_actions(recipe)), default=0.0)
    intro = recipe.get("intro") if isinstance(recipe.get("intro"), dict) else {}
    try:
        settled = float(intro.get("delay") or 0) + float(intro.get("duration") or 0)
    except (TypeError, ValueError):
        settled = 0.0
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    first_settled = None
    for frame in list(dsl.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        try:
            opacity = float(frame.get("opacity", 1) or 0)
            x = abs(float(frame.get("x", 0) or 0))
            y = abs(float(frame.get("y", 0) or 0))
            scale = abs(float(frame.get("scale", 1) or 1) - 1)
            scale_x = abs(float(frame.get("scaleX", 1) or 1) - 1)
            scale_y = abs(float(frame.get("scaleY", 1) or 1) - 1)
            rotate = abs(float(frame.get("rotate", 0) or 0))
            blur = abs(float(frame.get("blur", 0) or 0))
            time_value = float(frame.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if opacity >= 0.99 and x <= 0.01 and y <= 0.01 and scale <= 0.01 and scale_x <= 0.01 and scale_y <= 0.01 and rotate <= 0.01 and blur <= 0.01:
            first_settled = time_value if first_settled is None else min(first_settled, time_value)
    return max(0.0, first_settled if first_settled is not None else settled)


def _recipe_required_duration(recipe: dict) -> float:
    if not isinstance(recipe, dict):
        return 0.0
    if isinstance(recipe.get("motion_actions"), list):
        return max((_recipe_required_duration(action) for action in _motion_recipe_actions(recipe)), default=0.0)
    phase_plan = recipe.get("phase_plan") if isinstance(recipe.get("phase_plan"), dict) else {}
    required = 0.0
    for key in ("minimum_duration", "duration"):
        try:
            value = float(phase_plan.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            required = max(required, value)
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    required = max(required, _recipe_settled_duration(recipe))
    for frame in list(dsl.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        try:
            required = max(required, float(frame.get("time") or 0))
        except (TypeError, ValueError):
            continue
    outro = recipe.get("outro") if isinstance(recipe.get("outro"), dict) else {}
    try:
        required = max(required, float(outro.get("duration") or 0))
    except (TypeError, ValueError):
        pass
    return max(0.0, required)


def _motion_minimum_duration(motion: MotionSpec) -> float:
    motion_plan = getattr(motion, "motion_plan", None)
    if isinstance(motion_plan, dict):
        try:
            plan_minimum = float(motion_plan.get("minimum_duration") or 0)
        except (TypeError, ValueError):
            plan_minimum = 0.0
        if plan_minimum > 0:
            return max(0.25, min(60.0, plan_minimum))
    minimum = 0.25
    for layer in list(getattr(motion, "figma_layers", []) or []):
        if layer.get("visible") is False:
            continue
        minimum = max(minimum, _recipe_required_duration(layer.get("motion_recipe") or {}))
    return min(60.0, minimum)


def _place_new_motion_for_project(state, base: Path, motion):
    motion = _fit_motion_for_project(state, base, motion)
    if not state.source_video:
        return motion
    return place_motion_on_quiet_area(motion, base / state.source_video, base / "assets")


def _project_style_profile(state) -> dict | None:
    return load_style_preset(_selected_style_preset_id(state))


def _apply_project_style_to_motion(state, motion: MotionSpec) -> MotionSpec:
    if getattr(motion, "source_type", "generated") == "figma":
        return motion
    return apply_style_to_motion(motion, _project_style_profile(state))


def _prompt_has_spatial_position(prompt: str) -> bool:
    text = str(prompt or "").casefold()
    return any(
        marker in text
        for marker in (
            "left",
            "right",
            "top",
            "bottom",
            "corner",
            "center",
            "слева",
            "справа",
            "сверху",
            "снизу",
            "угол",
            "центр",
            "вверху",
            "внизу",
        )
    )


def _native_motion_cue_text(prompt: str, variant_index: int) -> str | None:
    exact = extract_user_motion_text(prompt)
    if exact:
        return exact
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    lowered = text.casefold()
    generic = any(marker in lowered for marker in ("сделай", "добавь", "красив", "моушн", "motion", "animation", "анимац"))
    content_words = [
        word
        for word in re.findall(r"[\w\u0400-\u04ff-]+", text, flags=re.UNICODE)
        if word.casefold() not in {"сделай", "добавь", "красивый", "красивую", "красиво", "моушн", "motion", "animation", "тут", "здесь"}
    ]
    if generic and len(content_words) <= 1:
        return ["ВАЖНЫЙ МОМЕНТ", "СМОТРИ СЮДА", "КЛЮЧЕВАЯ МЫСЛЬ", "АКЦЕНТ"][variant_index % 4]
    return None


def _native_motion_cue_candidate(state, base: Path, payload: NativeMotionCuePreviewRequest) -> MotionSpec:
    if not state.source_video:
        raise HTTPException(status_code=400, detail="Upload a source video first")
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    source = base / state.source_video
    if not source.exists():
        raise HTTPException(status_code=400, detail="Source video is missing")
    canvas_width, canvas_height = _motion_canvas_size(state, base)
    try:
        source_duration = detect_duration(source)
    except Exception:
        source_duration = float(getattr(state.edit_plan, "estimated_duration", 0.0) or 0.0)
    start = max(0.0, float(payload.start or 0.0))
    if source_duration > 0:
        start = min(start, max(0.0, source_duration - 0.5))
    duration = max(0.5, min(20.0, float(payload.duration or DEFAULT_MOTION_BLOCK_DURATION)))
    if source_duration > 0:
        duration = min(duration, max(0.5, source_duration - start))

    variant_index = max(0, int(payload.variant_index or 0))
    requested_type = normalize_motion_type(payload.motion_type)
    type_cycle = ["callout", "text", "badge", "lower-third", "quote"]
    motion_type = type_cycle[variant_index % len(type_cycle)] if requested_type == "auto" else requested_type
    positions = ["top right", "bottom left", "top left", "bottom right", "center", "bottom", "top"]
    position = None if _prompt_has_spatial_position(prompt) else positions[variant_index % len(positions)]
    cue_text = _native_motion_cue_text(prompt, variant_index)

    motion = build_directed_motion(
        prompt=prompt,
        start=start,
        duration=duration,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        preset=_selected_style_preset_id(state),
        enhance=True,
        motion_type=motion_type,
        variant_index=variant_index,
        exact_text=cue_text,
        position=position,
        style_id=_selected_style_preset_id(state),
    )
    motion = _fit_motion_for_project(state, base, motion)
    variants = [
        {"enter_animation": "slide", "enter_from": "right", "exit_animation": "fade", "exit_to": "center", "easing": "expo"},
        {"enter_animation": "pop", "enter_from": "center", "exit_animation": "slide", "exit_to": "left", "easing": "power"},
        {"enter_animation": "rise", "enter_from": "bottom", "exit_animation": "fade", "exit_to": "center", "easing": "sine"},
        {"enter_animation": "slide", "enter_from": "left", "exit_animation": "slide", "exit_to": "right", "easing": "expo"},
        {"enter_animation": "fade", "enter_from": "center", "exit_animation": "drop", "exit_to": "bottom", "easing": "sine"},
    ]
    variant = variants[variant_index % len(variants)]
    plan = dict(motion.motion_plan or {})
    director = dict(plan.get("director") or {})
    director.update(
        {
            "variant_index": variant_index,
            "variant_seed": payload.variant_seed,
            "type": motion_type,
            "position": position or "prompt",
        }
    )
    plan["director"] = director
    plan["engine"] = "native-motion-cue"
    plan["native_motion_cue"] = {
        "prompt": prompt,
        "start": start,
        "duration": duration,
        "variant_index": variant_index,
        "variant_seed": payload.variant_seed,
        "status": "preview",
    }
    return motion.model_copy(
        update={
            "id": f"native-cue-{uuid.uuid4().hex[:10]}",
            "start": start,
            "duration": duration,
            "prompt": prompt,
            "motion_plan": plan,
            **variant,
        }
    )


def _native_motion_cue_preview_root(base: Path, preview_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(preview_id or "")).strip("-")
    if not safe_id:
        raise HTTPException(status_code=400, detail="Invalid preview id")
    root = base / "renders" / "native_motion_cue_previews" / safe_id
    resolved = root.resolve()
    previews_root = (base / "renders" / "native_motion_cue_previews").resolve()
    if previews_root not in resolved.parents and resolved != previews_root:
        raise HTTPException(status_code=400, detail="Invalid preview path")
    return root


def _render_native_motion_cue_preview(state, base: Path, motion: MotionSpec, preview_id: str) -> tuple[Path, Path]:
    preview_root = _native_motion_cue_preview_root(base, preview_id)
    work_root = preview_root / "work"
    if work_root.exists():
        shutil.rmtree(work_root)
    ensure_dirs = [
        work_root / "input",
        work_root / "assets",
        work_root / "renders",
        work_root / "transcripts",
        work_root / "analysis",
    ]
    for item in ensure_dirs:
        item.mkdir(parents=True, exist_ok=True)
    source_rel = Path(str(state.source_video or ""))
    source_src = base / source_rel
    source_dst = work_root / source_rel
    source_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_src, source_dst)
    if (base / "assets").exists():
        shutil.copytree(base / "assets", work_root / "assets", dirs_exist_ok=True)
    try:
        source_duration = detect_duration(source_src)
    except Exception:
        source_duration = float(getattr(state.edit_plan, "estimated_duration", 0.0) or 0.0)
    cue_start = max(0.0, float(motion.start or 0.0))
    cue_duration = max(0.5, float(motion.duration or DEFAULT_MOTION_BLOCK_DURATION))
    pre_roll = 0.35
    post_roll = 0.35
    clip_start = max(0.0, cue_start - pre_roll)
    clip_end = cue_start + cue_duration + post_roll
    if source_duration > 0:
        clip_end = min(source_duration, max(clip_start + 0.5, clip_end))
    if clip_end <= clip_start:
        clip_end = clip_start + cue_duration

    preview_motions: list[MotionSpec] = []
    for existing in state.motions or []:
        existing_start = max(0.0, float(existing.start or 0.0))
        existing_duration = max(0.0, float(existing.duration or 0.0))
        existing_end = existing_start + existing_duration
        if existing_end <= clip_start or existing_start >= clip_end:
            continue
        overlap_start = max(existing_start, clip_start)
        overlap_end = min(existing_end, clip_end)
        preview_motions.append(
            existing.model_copy(
                update={
                    "start": max(0.0, overlap_start - clip_start),
                    "duration": max(0.1, overlap_end - overlap_start),
                }
            )
        )
    preview_motions.append(motion.model_copy(update={"start": max(0.0, cue_start - clip_start)}))
    preview_plan = EditPlan(
        summary="Native motion cue preview",
        strategy="local-preview",
        estimated_duration=max(0.5, clip_end - clip_start),
        keep_ranges=[CutRange(start=clip_start, end=clip_end, reason="native motion cue preview")],
        suggestions=[],
        subtitle_style="none",
        motion_notes=["Temporary local preview; Apply keeps the original timeline time."],
    )
    preview_state = state.model_copy(
        update={
            "edit_plan": preview_plan,
            "motions": preview_motions,
            "outputs": {},
            "subtitles_enabled": False,
            "current_job_id": None,
        }
    )
    preview = render_project_preview(preview_state, work_root)
    output = preview_root / "preview.mp4"
    shutil.copy2(preview, output)
    candidate_path = preview_root / "candidate.json"
    candidate_path.write_text(json.dumps(motion.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return output, candidate_path


def _motion_canvas_size(state, base: Path) -> tuple[int, int]:
    if state.source_video:
        try:
            return detect_video_size(base / state.source_video)
        except Exception:
            pass
    return 1280, 720


def _ensure_motion_context(state, base: Path, *, allow_transcribe: bool = True):
    if not state.source_video:
        return state
    source = base / state.source_video
    if state.transcript is None:
        transcript_path = base / "transcripts" / "source.json"
        try:
            transcript = load_cached_transcript(transcript_path)
            if transcript is None and allow_transcribe:
                transcript = transcribe_with_faster_whisper(source, transcript_path)
            if transcript is not None:
                state.transcript = transcript
                state.status = "transcribed"
                add_note(state, "Transcript ready for motion director.")
            elif not allow_transcribe:
                add_note(state, "Fast auto motion used without transcript.")
        except Exception as exc:
            state.transcript = None
            add_note(state, f"Motion director transcript skipped: {exc}")
    if state.edit_plan is None and state.transcript is not None:
        try:
            state.edit_plan = generate_edit_plan(state.transcript, source, cleanup_only=False, use_llm=False)
            state.status = "planned"
            add_note(state, "Edit plan ready for motion director.")
        except Exception as exc:
            state.edit_plan = None
            add_note(state, f"Motion director edit plan skipped: {exc}")
    return state


def _analyze_worker(project_id: str, render_preview: bool, cleanup_only: bool):
    def worker(job_id: str) -> None:
        try:
            state = load_state(project_id)
            base = project_dir(project_id)
            source = base / state.source_video if state.source_video else None
            if source is None:
                raise RuntimeError("No source video in project")

            update_job(job_id, progress=10, message="Preparing transcription")
            transcript_path = base / "transcripts" / "source.json"
            transcript = load_cached_transcript(transcript_path)
            if transcript is None:
                transcript = transcribe_with_faster_whisper(source, transcript_path)
            state.transcript = transcript
            state.status = "transcribed"
            add_note(state, "Transcript ready.")
            save_state(state)

            update_job(job_id, progress=60, message="Building edit plan")
            state = load_state(project_id)
            state.mode = "cleanup" if cleanup_only else "full"
            state.edit_plan = generate_edit_plan(state.transcript, source, cleanup_only=cleanup_only)
            if cleanup_only:
                state.motions = []
                state.outputs.pop("preview", None)
            state.status = "planned"
            add_note(state, "Edit plan ready.")
            save_state(state)

            update_job(job_id, progress=72, message="Motion graphics are manual")
            state = load_state(project_id)
            if not cleanup_only:
                add_note(state, "Auto motion disabled: add motion layers manually or import them from Figma.")
                save_state(state)

            if render_preview:
                update_job(job_id, progress=85, message="Rendering preview")
                state = load_state(project_id)
                preview = render_project_preview(state, base)
                state.outputs["preview"] = str(preview.relative_to(base))
                state.status = "preview-rendered"
                add_note(state, "Preview rendered after analysis.")
                save_state(state)

            state = load_state(project_id)
            state.current_job_id = None
            state.last_error = None
            save_state(state)
            update_job(job_id, progress=100, message="Analysis complete")
        except Exception as exc:
            state = load_state(project_id)
            state.current_job_id = None
            state.last_error = str(exc)
            state.status = "failed"
            add_note(state, f"Analysis failed: {exc}")
            save_state(state)
            raise

    return worker


def _agent_edit_worker(project_id: str, payload: AgentEditRequest):
    def worker(job_id: str) -> None:
        try:
            state = load_state(project_id)
            base = project_dir(project_id)
            source = base / state.source_video if state.source_video else None
            if source is None:
                raise RuntimeError("No source video in project")

            update_job(job_id, progress=8, message="Preparing word-level transcript")
            transcript_path = base / "transcripts" / "source.json"
            transcript = load_cached_transcript(transcript_path)
            if transcript is None:
                transcript = transcribe_with_faster_whisper(source, transcript_path)
            state.transcript = transcript
            state.mode = "full"
            state.subtitles_enabled = bool(payload.subtitles_enabled)
            state.status = "agent-transcribed"
            add_note(state, "Agent edit transcript ready.")
            save_state(state)

            update_job(job_id, progress=32, message="Building edit decision list")
            state = load_state(project_id)
            if payload.cleanup_only:
                state.mode = "cleanup"
                state.edit_plan = generate_edit_plan(state.transcript, source, cleanup_only=True, use_llm=False)
                add_note(state, "Agent edit will remove pauses and filler words.")
            else:
                state.mode = "full"
                state.edit_plan = source_plan(state.transcript)
                add_note(state, "Agent edit keeps original timing. Cleanup is off.")
            state.subtitles_enabled = bool(payload.subtitles_enabled)
            state.outputs.pop("preview", None)
            state.status = "agent-planned"
            prompt_text = str(payload.prompt or "").strip()
            if prompt_text:
                add_note(state, f"Agent edit brief: {prompt_text}")
            add_note(state, "Agent edit plan ready.")
            save_state(state)

            update_job(job_id, progress=52, message="Writing agent edit artifacts")
            state = load_state(project_id)
            state, _artifacts = build_agent_edit_artifacts(
                state,
                base,
                create_motions=bool(payload.create_motion_blocks) and not bool(payload.plan_only),
                prompt=prompt_text,
                variant_seed=payload.variant_seed,
            )
            add_note(state, "Agent edit artifacts saved.")
            save_state(state)

            if payload.plan_only:
                update_job(job_id, progress=92, message="Motion plan ready for review")
                state = load_state(project_id)
                state.status = "agent-plan-ready"
                state.current_job_id = None
                state.last_error = None
                add_note(state, "Agent edit motion plan ready for review.")
                save_state(state)
                update_job(job_id, progress=100, message="Motion plan ready")
                return

            if payload.render_preview:
                update_job(job_id, progress=72, message="Rendering agent preview")
                state = load_state(project_id)
                state.subtitles_enabled = bool(payload.subtitles_enabled)
                preview = render_project_preview(state, base)
                state.outputs["preview"] = str(preview.relative_to(base))
                state.status = "agent-preview-rendered"
                add_note(state, "Agent edit preview rendered.")
                save_state(state)

                update_job(job_id, progress=90, message="Self-checking rendered preview")
                state = load_state(project_id)
                state, report = self_eval_preview(state, base)
                state.status = "agent-preview-ready" if report.get("status") == "pass" else "agent-preview-needs-review"
                add_note(state, f"Agent edit self-check: {report.get('status', 'unknown')}.")
                save_state(state)
            else:
                update_job(job_id, progress=90, message="Self-check skipped until preview render")

            state = load_state(project_id)
            state.current_job_id = None
            state.last_error = None
            save_state(state)
            update_job(job_id, progress=100, message="Agent edit complete")
        except Exception as exc:
            state = load_state(project_id)
            state.current_job_id = None
            state.last_error = str(exc)
            state.status = "failed"
            add_note(state, f"Agent edit failed: {exc}")
            save_state(state)
            raise

    return worker


def _render_signature(state) -> str:
    edit_plan = state.edit_plan
    motion_payloads = []
    for motion in state.motions:
        payload = motion.model_dump(mode="json")
        for key in HISTORY_VOLATILE_KEYS:
            payload.pop(key, None)
        motion_payloads.append(payload)
    payload = {
        "source_video": state.source_video,
        "ranges": [
            {
                "start": item.start,
                "end": item.end,
                "handle_start": item.handle_start,
                "handle_end": item.handle_end,
            }
            for item in (edit_plan.keep_ranges if edit_plan else [])
        ],
        "motions": motion_payloads,
        "subtitle_style": edit_plan.subtitle_style if edit_plan else None,
        "subtitles_enabled": bool(getattr(state, "subtitles_enabled", False)),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _render_worker(project_id: str):
    def worker(job_id: str) -> None:
        try:
            base = project_dir(project_id)
            for attempt in range(1, 6):
                state = load_state(project_id)
                if not state.source_video:
                    raise RuntimeError("Upload a video before rendering")
                signature = _render_signature(state)
                update_job(job_id, progress=15 + attempt * 12, message="Rendering latest timeline")
                preview = render_project_preview(state, base)
                latest = load_state(project_id)
                if _render_signature(latest) != signature:
                    add_note(latest, "Timeline changed during render; restarting preview render.")
                    save_state(latest)
                    continue
                latest.motions = state.motions
                latest.outputs["preview"] = str(preview.relative_to(base))
                latest.status = "preview-rendered"
                latest.current_job_id = None
                latest.last_error = None
                add_note(latest, "Preview rendered.")
                save_state(latest)
                update_job(job_id, progress=100, message="Preview ready")
                return
            latest = load_state(project_id)
            latest.current_job_id = None
            latest.status = "timeline-updated"
            latest.outputs.pop("preview", None)
            add_note(latest, "Preview render skipped: timeline kept changing.")
            save_state(latest)
            raise RuntimeError("Timeline changed too many times during render")
        except Exception as exc:
            state = load_state(project_id)
            state.current_job_id = None
            state.last_error = str(exc)
            state.status = "failed"
            add_note(state, f"Render failed: {exc}")
            save_state(state)
            raise

    return worker


@router.get("/projects")
def get_projects():
    return list_projects()


@router.post("/projects")
def create_empty_project(payload: ProjectNoteRequest):
    clear_projects()
    state = create_project(payload.note)
    return state


@router.post("/projects/upload")
async def upload_video(file: UploadFile = File(...)):
    clear_projects()
    title = Path(file.filename or "video").stem
    state = create_project(title)
    base = project_dir(state.project_id)
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        shutil.copyfileobj(file.file, temp)
        temp_path = Path(temp.name)

    source = base / "input" / f"source{suffix}"
    shutil.move(str(temp_path), source)

    state.source_video = str(source.relative_to(base))
    state.status = "uploaded"
    add_note(state, "Source video uploaded.")
    save_state(state)
    return state


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    return _require_project(project_id)


@router.get("/style-presets")
def get_style_presets():
    return {"presets": list_style_presets()}


@router.post("/style-presets")
async def upload_style_preset(name: str = Form(...), files: list[UploadFile] = File(...)):
    raw_files = [(file.filename or f"ref-{index}.png", await file.read()) for index, file in enumerate(files, start=1)]
    try:
        return create_style_preset(name, raw_files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/style-preset")
def set_project_style_preset(project_id: str, payload: StylePresetSelectRequest):
    state = _require_project(project_id)
    preset_id = str(payload.style_preset_id or SOFT_NEUMORPHISM_PRESET_ID)
    if preset_id and load_style_preset(preset_id) is None:
        raise HTTPException(status_code=404, detail="Style preset not found")
    state.style_preset_id = preset_id
    _lock_generated_motions_to_soft_neumorphism(state)
    state.outputs.pop("preview", None)
    state.status = "style-updated"
    preset = load_style_preset(preset_id) or {}
    add_note(state, f"Style preset selected: {preset.get('name') or preset_id}. Preview invalidated.")
    save_state(state)
    return state


@router.get("/projects/{project_id}/timeline")
def get_project_timeline(project_id: str):
    state = _require_project(project_id)
    timeline = build_timeline(project_id, state, project_dir(project_id))
    if state.edit_plan:
        save_state(state)
    return timeline


@router.post("/projects/{project_id}/analyze")
def analyze_project(project_id: str, payload: ProjectActionRequest):
    state = _require_project(project_id)
    if not state.source_video:
        raise HTTPException(status_code=400, detail="No source video")
    if not payload.run_analysis:
        state.current_job_id = None
        state.last_error = None
        state.status = "uploaded"
        add_note(state, "Analysis skipped: upload only mode.")
        save_state(state)
        job = create_job(project_id=project_id, kind="analyze", message="Analysis skipped")
        return update_job(job.job_id, status="completed", progress=100, message="Video placed on timeline")
    if state.current_job_id:
        job = get_job(state.current_job_id)
        if job and job.status in {"queued", "running"}:
            return job

    job = create_job(project_id=project_id, kind="analyze", message="Queued analysis")
    state.current_job_id = job.job_id
    state.status = "processing"
    state.mode = "cleanup" if payload.cleanup_only else "full"
    state.last_error = None
    add_note(state, "Analysis started.")
    save_state(state)
    return submit_job(job, _analyze_worker(project_id, True, payload.cleanup_only))


@router.post("/projects/{project_id}/agent-edit")
def agent_edit_project(project_id: str, payload: AgentEditRequest):
    state = _require_project(project_id)
    if not state.source_video:
        raise HTTPException(status_code=400, detail="Upload a video first")
    if state.current_job_id:
        job = get_job(state.current_job_id)
        if job and job.status in {"queued", "running"}:
            return job
    preset_id = str(payload.style_preset_id or getattr(state, "style_preset_id", None) or SOFT_NEUMORPHISM_PRESET_ID)
    if preset_id and load_style_preset(preset_id) is None:
        raise HTTPException(status_code=404, detail="Style preset not found")

    job = create_job(
        project_id=project_id,
        kind="agent-edit",
        message="Queued motion plan" if payload.plan_only else "Queued agent edit",
    )
    state.current_job_id = job.job_id
    state.status = "agent-processing"
    state.mode = "full"
    state.subtitles_enabled = bool(payload.subtitles_enabled)
    state.last_error = None
    state.style_preset_id = preset_id
    preset = load_style_preset(preset_id) or {}
    add_note(state, f"Agent edit style preset: {preset.get('name') or preset_id}")
    add_note(state, "Agent edit started.")
    save_state(state)
    return submit_job(job, _agent_edit_worker(project_id, payload))


@router.post("/projects/{project_id}/render")
def render_preview(project_id: str):
    state = _require_project(project_id)
    if not state.source_video:
        raise HTTPException(status_code=400, detail="Upload a video first")
    if state.current_job_id:
        job = get_job(state.current_job_id)
        if job and job.status in {"queued", "running"}:
            return job
    job = create_job(project_id=project_id, kind="render", message="Queued preview render")
    state.current_job_id = job.job_id
    state.status = "rendering"
    state.last_error = None
    add_note(state, "Preview render started.")
    save_state(state)
    return submit_job(job, _render_worker(project_id))


@router.post("/projects/{project_id}/timeline/undo")
def undo_timeline(project_id: str):
    state = _require_project(project_id)
    if not state.undo_stack:
        return state
    state.redo_stack.append(_timeline_snapshot(state))
    snapshot = state.undo_stack.pop()
    _restore_timeline_snapshot(state, snapshot)
    add_note(state, "Timeline undo.")
    _render_motion_assets(state, project_dir(project_id))
    save_state(state)
    return state


@router.post("/projects/{project_id}/timeline/redo")
def redo_timeline(project_id: str):
    state = _require_project(project_id)
    if not state.redo_stack:
        return state
    state.undo_stack.append(_timeline_snapshot(state))
    snapshot = state.redo_stack.pop()
    _restore_timeline_snapshot(state, snapshot)
    add_note(state, "Timeline redo.")
    _render_motion_assets(state, project_dir(project_id))
    save_state(state)
    return state


@router.post("/projects/{project_id}/timeline/split")
def split_timeline_clip(project_id: str, payload: TimelineSplitRequest):
    state = _require_project(project_id)
    base = project_dir(project_id)
    if not state.edit_plan and state.source_video:
        source = base / state.source_video
        source_duration = detect_duration(source) if source.exists() else 0.0
        if source_duration <= 0:
            raise HTTPException(status_code=400, detail="No clips to split")
        state.edit_plan = EditPlan(
            summary="Manual timeline",
            strategy="Manual split from full source",
            estimated_duration=round(source_duration, 3),
            keep_ranges=[
                CutRange(
                    start=0.0,
                    end=round(source_duration, 3),
                    reason="Full source",
                    source="source",
                    handle_start=0.0,
                    handle_end=round(source_duration, 3),
                )
            ],
            subtitle_style="none",
        )
    if not state.edit_plan or not state.edit_plan.keep_ranges:
        raise HTTPException(status_code=400, detail="No clips to split")

    split_time = max(0.0, float(payload.time))
    cursor = 0.0
    min_piece = 0.1
    for index, keep_range in enumerate(state.edit_plan.keep_ranges):
        duration = max(0.0, keep_range.end - keep_range.start)
        clip_start = cursor
        clip_end = cursor + duration
        if clip_start + min_piece < split_time < clip_end - min_piece:
            source_time = keep_range.start + (split_time - clip_start)
            _push_history(state)
            left = CutRange(
                start=round(keep_range.start, 3),
                end=round(source_time, 3),
                reason=keep_range.reason,
                source=keep_range.source,
                handle_start=keep_range.handle_start,
                handle_end=keep_range.handle_end,
            )
            right = CutRange(
                start=round(source_time, 3),
                end=round(keep_range.end, 3),
                reason=keep_range.reason,
                source=keep_range.source,
                handle_start=keep_range.handle_start,
                handle_end=keep_range.handle_end,
            )
            state.edit_plan.keep_ranges[index : index + 1] = [left, right]
            _estimate_duration(state)
            state.status = "timeline-updated"
            state.outputs.pop("preview", None)
            add_note(state, f"Clip split at {split_time:.2f}s")
            save_state(state)
            return state
        cursor = clip_end

    raise HTTPException(status_code=400, detail="Playhead must be inside a clip, away from its edges")


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "failed":
        state = _require_project(job.project_id)
        state.current_job_id = None
        state.last_error = job.error
        state.status = "failed"
        add_note(state, f"Job failed: {job.message}")
        save_state(state)
    elif job.status == "cancelled":
        state = _require_project(job.project_id)
        if state.current_job_id == job.job_id:
            state.current_job_id = None
            state.last_error = None
            add_note(state, f"Job cancelled: {job.message}")
            save_state(state)
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    updated = cancel_job(job_id, "Cancelled")
    if job.kind == "ltx" and job.status in {"queued", "running"}:
        cancel_ltx_generation()
    state = _require_project(job.project_id)
    if state.current_job_id == job_id:
        state.current_job_id = None
        state.last_error = None
        add_note(state, f"Job cancelled: {job.kind}")
        save_state(state)
    return updated or job


@router.post("/projects/{project_id}/motion")
def add_motion(project_id: str, payload: MotionPromptRequest):
    state = _require_project(project_id)
    base = project_dir(project_id)
    prompt = payload.prompt.strip()
    target_motion_id = str(payload.target_motion_id or "").strip()
    motion_type = normalize_motion_type(payload.motion_type)

    if target_motion_id:
        target = next((motion for motion in state.motions if motion.id == target_motion_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Motion not found")
        if not prompt:
            prompt = _strip_stored_motion_prompt(getattr(target, "prompt", "") or "") or "Create another tasteful motion variant"
        _push_history(state)
        canvas_width, canvas_height = _motion_canvas_size(state, base)
        target_ids = {target_motion_id}
        if payload.apply_to_all:
            target_ids = {
                motion.id
                for motion in state.motions
                if getattr(motion, "source_type", "generated") != "figma"
            }
            if not target_ids:
                target_ids = {target_motion_id}
        updated = []
        changed = 0
        for motion in state.motions:
            if motion.id not in target_ids:
                updated.append(motion)
                continue
            changed += 1
            if getattr(motion, "source_type", "generated") == "figma":
                new_motion = apply_animation_prompt(motion, prompt)
            else:
                new_motion = variant_motion(
                    motion,
                    prompt=prompt,
                    canvas_width=canvas_width,
                    canvas_height=canvas_height,
                    enhance=payload.enhance,
                    motion_type=motion_type,
                )
                if state.source_video:
                    new_motion = place_motion_on_quiet_area(new_motion, base / state.source_video, base / "assets")
                new_motion = _apply_project_style_to_motion(state, new_motion)
            new_motion = _render_motion_with_version(new_motion, base / "assets")
            updated.append(new_motion)
        state.motions = updated
        state.status = "motion-updated"
        state.outputs.pop("preview", None)
        add_note(state, f"Motion variant applied to {changed} block(s).")
        save_state(state)
        return state

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")

    _push_history(state)
    duration_hint = max(0.25, float(payload.duration)) if payload.duration is not None else DEFAULT_MOTION_BLOCK_DURATION
    start = max(0.0, float(payload.start)) if payload.start is not None else 0.0
    new_motions: list[MotionSpec] = []
    source = base / state.source_video if state.source_video else None
    if (
        source is not None
        and source.exists()
        and payload.whole_video
        and (payload.auto_director or is_general_motion_request(prompt))
        and extract_user_motion_text(prompt) is None
    ):
        state = _ensure_motion_context(state, base, allow_transcribe=not payload.auto_director)
        new_motions = build_context_motions(
            prompt=prompt,
            transcript=state.transcript,
            edit_plan=state.edit_plan,
            source_video=source,
            project_root=base,
            enhance=payload.enhance,
            motion_type=motion_type,
        )

    if not new_motions:
        canvas_width, canvas_height = _motion_canvas_size(state, base)
        motion = build_directed_motion(
            prompt=prompt,
            start=start,
            duration=duration_hint,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            preset=payload.preset,
            enhance=payload.enhance,
            motion_type=motion_type,
            exact_text=extract_user_motion_text(prompt),
            style_id=_selected_style_preset_id(state),
        )
        motion = _fit_motion_for_project(state, base, motion)
        new_motions = [motion]

    rendered = []
    for motion in new_motions:
        motion = _apply_project_style_to_motion(state, motion)
        rendered.append(_render_motion_with_version(motion, base / "assets"))
    state.motions.extend(rendered)
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    if len(rendered) == 1:
        add_note(state, f"Motion added: {rendered[0].text}")
    else:
        add_note(state, f"Motion director added {len(rendered)} blocks.")
    save_state(state)
    return state


@router.post("/projects/{project_id}/native-motion-cue/preview")
def preview_native_motion_cue(project_id: str, payload: NativeMotionCuePreviewRequest):
    state = _require_project(project_id)
    base = project_dir(project_id)
    motion = _native_motion_cue_candidate(state, base, payload)
    preview_id = f"cue-{uuid.uuid4().hex[:12]}"
    preview, candidate_path = _render_native_motion_cue_preview(state, base, motion, preview_id)
    rel_preview = str(preview.relative_to(base))
    rel_candidate = str(candidate_path.relative_to(base))
    signature = {
        "text": motion.text,
        "preset": motion.design_preset,
        "x": motion.x,
        "y": motion.y,
        "width": motion.width,
        "height": motion.height,
        "enter": motion.enter_animation,
        "enter_from": motion.enter_from,
        "exit": motion.exit_animation,
        "exit_to": motion.exit_to,
    }
    return {
        "preview_id": preview_id,
        "preview_path": rel_preview,
        "candidate_path": rel_candidate,
        "motion": motion.model_dump(mode="json"),
        "signature": signature,
    }


@router.post("/projects/{project_id}/native-motion-cue/apply")
def apply_native_motion_cue(project_id: str, payload: NativeMotionCueApplyRequest):
    state = _require_project(project_id)
    base = project_dir(project_id)
    preview_root = _native_motion_cue_preview_root(base, payload.preview_id)
    candidate_path = preview_root / "candidate.json"
    if not candidate_path.exists():
        raise HTTPException(status_code=404, detail="Native motion cue preview not found")
    motion = MotionSpec.model_validate_json(candidate_path.read_text(encoding="utf-8-sig"))
    existing_ids = {item.id for item in state.motions}
    if motion.id in existing_ids:
        motion = motion.model_copy(update={"id": f"native-cue-{uuid.uuid4().hex[:10]}"})
    plan = dict(motion.motion_plan or {})
    cue = dict(plan.get("native_motion_cue") or {})
    cue["status"] = "applied"
    plan["native_motion_cue"] = cue
    plan["engine"] = "native-motion-cue"
    motion = motion.model_copy(update={"motion_plan": plan})
    motion = _apply_project_style_to_motion(state, motion)
    motion = _render_motion_with_version(motion, base / "assets")
    _push_history(state)
    state.motions.append(motion)
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Native motion cue applied at {motion.start:.2f}s: {motion.text}")
    save_state(state)
    return {"project": state.model_dump(mode="json"), "motion_id": motion.id}


@router.post("/projects/{project_id}/figma/import")
def import_figma_motion(project_id: str, payload: FigmaImportRequest):
    state = _require_project(project_id)
    _push_history(state)
    base = project_dir(project_id)
    try:
        motion = import_figma_node(
            figma_url=payload.figma_url,
            token=payload.access_token,
            node_id=payload.node_id,
            project_root=base,
            start=max(0.0, float(payload.start or 0.0)),
            duration=max(0.25, float(payload.duration or 4.0)),
        )
    except FigmaImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    motion = _fit_motion_for_project(state, base, motion)
    motion = _render_motion_with_version(motion, base / "assets")
    state.motions.append(motion)
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Figma layer imported: {motion.figma_node_name or motion.text}")
    save_state(state)
    return state


@router.post("/projects/{project_id}/figma/assets")
def figma_assets(project_id: str, payload: FigmaAssetsRequest):
    _require_project(project_id)
    try:
        return list_figma_assets(
            figma_url=payload.figma_url,
            token=payload.access_token,
            node_id=payload.node_id,
        )
    except FigmaImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/figma/sync")
def sync_figma_motions(project_id: str, payload: FigmaAssetsRequest):
    state = _require_project(project_id)
    base = project_dir(project_id)
    updated = []
    synced = 0
    try:
        for motion in state.motions:
            refreshed = refresh_motion_from_figma(
                figma_url=payload.figma_url,
                token=payload.access_token,
                project_root=base,
                motion=motion,
            )
            if refreshed:
                updated.append(_render_motion_with_version(refreshed, base / "assets"))
                synced += 1
            else:
                updated.append(motion)
    except FigmaImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if synced <= 0:
        data = state.model_dump()
        data["figma_synced"] = 0
        return data
    _push_history(state)
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Figma URL sync refreshed {synced} imported frame(s).")
    save_state(state)
    data = state.model_dump()
    data["figma_synced"] = synced
    return data


@router.post("/figma/assets")
def figma_assets_global(payload: FigmaAssetsRequest):
    try:
        return list_figma_assets(
            figma_url=payload.figma_url,
            token=payload.access_token,
            node_id=payload.node_id,
        )
    except FigmaImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/figma/plugin/assets")
def receive_figma_plugin_assets(payload: FigmaPluginAssetsRequest):
    try:
        return save_plugin_assets(
            payload.assets,
            scope=payload.scope,
            page=payload.page,
            session_id=payload.session_id,
            total=payload.total,
            complete=payload.complete,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/figma/plugin/assets")
def get_figma_plugin_assets():
    return list_plugin_assets()


@router.post("/figma/bridge/start")
def start_figma_bridge(payload: FigmaAssetsRequest | None = None):
    open_uri = ""
    figma_url = (payload.figma_url if payload else "") or ""
    match = re.search(r"figma\.com/(?:file|design)/([^/?#]+)", figma_url)
    if match:
        open_uri = f"figma://file/{match.group(1)}"
    register_script = settings.project_root / "scripts" / "register_figma_plugin.py"
    try:
        subprocess.run(
            [sys.executable, str(register_script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()[:1000]
        raise HTTPException(status_code=400, detail=detail or "Failed to register Figma plugin") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=408, detail="Figma plugin registration timed out") from exc
    script = r'''
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@
function PressKey([byte]$vk) {
  [Win32]::keybd_event($vk,0,0,[UIntPtr]::Zero)
  [Win32]::keybd_event($vk,0,2,[UIntPtr]::Zero)
}
function PressCtrlKey([byte]$vk) {
  [Win32]::keybd_event(0x11,0,0,[UIntPtr]::Zero)
  Start-Sleep -Milliseconds 20
  [Win32]::keybd_event($vk,0,0,[UIntPtr]::Zero)
  [Win32]::keybd_event($vk,0,2,[UIntPtr]::Zero)
  [Win32]::keybd_event(0x11,0,2,[UIntPtr]::Zero)
}
$openUri = "__OPEN_URI__"
if ($openUri) {
  Start-Process $openUri
  Start-Sleep -Milliseconds 2500
}
$figma = Get-Process Figma -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $figma) { throw "Figma desktop window is not open." }
[Win32]::ShowWindow($figma.MainWindowHandle, 9) | Out-Null
[Win32]::SetForegroundWindow($figma.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 700
$wshell = New-Object -ComObject WScript.Shell
$wshell.AppActivate($figma.Id) | Out-Null
Start-Sleep -Milliseconds 250
# Close an already-open plugin dialog if Figma has one focused. The plugin auto-exports on open,
# so the bridge never needs screen-size-dependent clicks inside the plugin UI.
$wshell.SendKeys("{ESC}")
Start-Sleep -Milliseconds 200
$wshell.SendKeys("{ESC}")
Start-Sleep -Milliseconds 250
# Open Figma Quick Actions with Ctrl+/ using the slash virtual key. This is keyboard-only and
# independent of monitor size, zoom, DPI, window position, or where the mouse cursor is.
PressCtrlKey 0xBF
Start-Sleep -Milliseconds 700
Set-Clipboard -Value "VibeMotion Export"
PressCtrlKey 0x41
Start-Sleep -Milliseconds 150
PressCtrlKey 0x56
Start-Sleep -Milliseconds 600
PressKey 0x0D
'''.replace("__OPEN_URI__", open_uri)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()[:1000]
        raise HTTPException(status_code=400, detail=detail or "Failed to start Figma bridge") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=408, detail="Figma bridge start timed out") from exc
    return {"status": "started", "plugin": "VibeMotion Export"}


@router.post("/projects/{project_id}/figma/plugin/import")
def import_figma_plugin_motion(project_id: str, payload: FigmaPluginAssetImportRequest):
    state = _require_project(project_id)
    _push_history(state)
    base = project_dir(project_id)
    try:
        motion = motion_from_plugin_asset(
            project_root=base,
            asset_id=payload.asset_id,
            start=max(0.0, float(payload.start or 0.0)),
            duration=max(0.25, float(payload.duration or 4.0)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    motion = _fit_motion_for_project(state, base, motion)
    motion = _render_motion_with_version(motion, base / "assets")
    diff = compare_plugin_asset_to_render(base, motion)
    state.motions.append(motion)
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Figma plugin asset imported: {motion.figma_node_name or motion.text}")
    if diff.get("available"):
        add_note(state, f"Figma visual check: mean delta {diff['mean_delta']}, layers {diff['layer_count']}.")
    save_state(state)
    return state


@router.post("/projects/{project_id}/figma/plugin/sync")
def sync_figma_plugin_motions(project_id: str):
    state = _require_project(project_id)
    base = project_dir(project_id)
    updated = []
    synced = 0
    for motion in state.motions:
        if getattr(motion, "source_type", "generated") != "figma" or not motion.figma_node_id:
            updated.append(motion)
            continue
        refreshed = refresh_motion_from_plugin_asset(base, motion)
        if not refreshed:
            updated.append(motion)
            continue
        if refreshed.model_dump(mode="json") == motion.model_dump(mode="json"):
            updated.append(motion)
            continue
        updated.append(_render_motion_with_version(refreshed, base / "assets"))
        synced += 1
    if synced <= 0:
        data = state.model_dump()
        data["figma_synced"] = 0
        return data
    _push_history(state)
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Figma plugin sync refreshed {synced} imported frame(s).")
    save_state(state)
    data = state.model_dump()
    data["figma_synced"] = synced
    return data


@router.put("/projects/{project_id}/motion/{motion_id}")
def update_motion(project_id: str, motion_id: str, payload: MotionUpdateRequest):
    state = _require_project(project_id)
    _push_history(state)
    base = project_dir(project_id)
    updated = []
    found = False
    for motion in state.motions:
        if motion.id != motion_id:
            updated.append(motion)
            continue
        found = True
        frame_prompt = _stored_frame_choreography_prompt(motion) if getattr(motion, "source_type", "generated") == "figma" else ""
        requested_duration = max(0.05, float(payload.duration))
        if frame_prompt:
            safe_duration = max(requested_duration, frame_choreography_required_duration(frame_prompt, requested_duration))
        else:
            safe_duration = max(requested_duration, _motion_minimum_duration(motion))
        new_motion = motion.model_copy(
            update={
                "text": payload.text,
                "start": payload.start,
                "duration": safe_duration,
                "design_preset": payload.preset if getattr(motion, "source_type", "generated") == "figma" else _selected_style_preset_id(state),
                "x": payload.x,
                "y": payload.y,
                "width": payload.width,
                "height": payload.height,
                "text_scale": payload.text_scale,
                "accent": payload.accent,
                "background": payload.background,
                "animation": payload.animation,
                "enter_animation": payload.enter_animation,
                "exit_animation": payload.exit_animation,
                "enter_from": payload.enter_from,
                "exit_to": payload.exit_to,
                "enter_duration": payload.enter_duration,
                "exit_duration": payload.exit_duration,
                "easing": payload.easing,
                "prompt": motion.prompt if frame_prompt else "",
            }
        )
        if payload.sync_text:
            new_motion = _sync_figma_text_layer(new_motion, payload.text)
        if frame_prompt:
            new_motion = _apply_frame_choreography_plan(new_motion, frame_prompt)
        else:
            new_motion = _reanchor_layer_end_actions(new_motion, float(motion.duration or 0), safe_duration)
        if getattr(new_motion, "source_type", "generated") != "figma":
            new_motion = _fit_motion_for_project(state, base, new_motion)
        should_render_asset = bool(payload.render_asset)
        if should_render_asset:
            new_motion = _render_motion_with_version(new_motion, base / "assets")
        updated.append(new_motion)
    if not found:
        raise HTTPException(status_code=404, detail="Motion not found")
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Motion updated: {motion_id}")
    save_state(state)
    return state


@router.post("/projects/{project_id}/motion/{motion_id}/prompt")
def apply_motion_prompt(project_id: str, motion_id: str, payload: MotionAnimationPromptRequest):
    state = _require_project(project_id)
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    mode = _motion_prompt_mode(payload.mode)
    _push_history(state)
    base = project_dir(project_id)
    updated = []
    found = False
    for motion in state.motions:
        if motion.id != motion_id:
            updated.append(motion)
            continue
        found = True
        incoming_prompt = _strip_stored_motion_prompt(prompt)
        if getattr(motion, "source_type", "generated") == "figma" and getattr(motion, "figma_layers", None):
            existing_prompt = _strip_stored_motion_prompt(getattr(motion, "prompt", "") or "")
            planning_prompt = _compose_motion_prompt(existing_prompt, incoming_prompt, mode)
            new_motion = _apply_frame_choreography_plan(motion, planning_prompt)
        else:
            planning_prompt = _compose_motion_prompt(getattr(motion, "prompt", ""), incoming_prompt, mode)
            prompt_base_motion = motion
            if mode == "replace" and getattr(motion, "source_type", "generated") == "figma" and getattr(motion, "figma_layers", None):
                prompt_base_motion = motion.model_copy(update={"figma_layers": _reset_figma_layer_motion(motion.figma_layers)})
            new_motion = apply_animation_prompt(prompt_base_motion, planning_prompt)
            new_motion = _fit_motion_for_project(state, base, new_motion)
        new_motion = _render_motion_with_version(new_motion, base / "assets")
        updated.append(new_motion)
    if not found:
        raise HTTPException(status_code=404, detail="Motion not found")
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Motion prompt applied ({mode}): {motion_id}")
    save_state(state)
    return state


@router.post("/projects/{project_id}/motion/{motion_id}/figma-layer")
def update_figma_motion_layer(project_id: str, motion_id: str, payload: FigmaLayerUpdateRequest):
    state = _require_project(project_id)
    layer_id = payload.layer_id.strip()
    if not layer_id:
        raise HTTPException(status_code=400, detail="Layer id is missing")
    _push_history(state)
    base = project_dir(project_id)
    numeric_fields = {"x", "y", "width", "height", "opacity", "font_size", "line_height", "radius", "stroke_weight"}
    geometry_fields = {"x", "y", "width", "height"}
    text_fields = {"text", "fill", "stroke", "color", "font_family", "text_align", "name", "visual_mask_id"}
    boolean_fields = {"visible", "manual_transform"}
    object_fields = {"motion_recipe", "original_geometry", "original_visual_rect"}

    def _num(value, fallback: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _normalize_rect(value) -> dict | None:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        text = value.strip()
        if text.startswith("@{") and text.endswith("}"):
            text = text[2:-1]
        result = {}
        for part in text.split(";"):
            if "=" not in part:
                continue
            key, raw_value = part.split("=", 1)
            key = key.strip()
            if not key:
                continue
            raw_value = raw_value.strip()
            try:
                result[key] = float(raw_value)
            except ValueError:
                result[key] = raw_value
        return result if "width" in result and "height" in result else None

    def _overlap_area(a: dict, b: dict) -> float:
        left = max(_num(a.get("x")), _num(b.get("x")))
        top = max(_num(a.get("y")), _num(b.get("y")))
        right = min(_num(a.get("x")) + _num(a.get("width")), _num(b.get("x")) + _num(b.get("width")))
        bottom = min(_num(a.get("y")) + _num(a.get("height")), _num(b.get("y")) + _num(b.get("height")))
        return max(0.0, right - left) * max(0.0, bottom - top)

    def _visual_mask_id(target: dict, layer_items: list[dict]) -> str | None:
        if target.get("kind") != "image":
            return None
        if target.get("ltx_video_path"):
            return None
        explicit_mask_id = str(target.get("visual_mask_id") or "")
        target_area = max(1.0, _num(target.get("width"), 1.0) * _num(target.get("height"), 1.0))
        try:
            target_index = layer_items.index(target)
        except ValueError:
            target_index = len(layer_items)

        def _has_image_between(mask_index: int) -> bool:
            if mask_index < 0 or target_index < 0:
                return False
            return any(
                item.get("kind") == "image" and item.get("visible") is not False
                for item in layer_items[mask_index + 1 : target_index]
            )

        if explicit_mask_id:
            for index, item in enumerate(layer_items):
                if (
                    str(item.get("id") or "") == explicit_mask_id
                    and item.get("kind") == "shape"
                    and item.get("visible") is not False
                    and not _has_image_between(index)
                ):
                    return explicit_mask_id
        candidates = []
        for index, item in enumerate(layer_items):
            if item is target or item.get("kind") != "shape":
                continue
            if index >= target_index:
                continue
            if _has_image_between(index):
                continue
            area = max(1.0, _num(item.get("width"), 1.0) * _num(item.get("height"), 1.0))
            overlap = _overlap_area(target, item)
            coverage = overlap / max(1.0, min(target_area, area))
            layer_coverage = overlap / target_area
            if coverage <= 0.62 or layer_coverage <= 0.12 or area > target_area * 1.18:
                continue
            x_delta = abs(_num(item.get("x")) - _num(target.get("x")))
            candidates.append((target_index - index, -coverage, x_delta, -area, str(item.get("id") or ""), item))
        if not candidates:
            return None
        candidates.sort()
        return str(candidates[0][5].get("id") or "") or None

    def _image_layer_for_visual_mask(mask_id: str, layer_items: list[dict]) -> dict | None:
        if not mask_id:
            return None
        for item in layer_items:
            if item.get("kind") != "image" or item.get("visible") is False:
                continue
            if _visual_mask_id(item, layer_items) == mask_id:
                return item
        return None

    def _rect_from_patch(patch: dict, fallback: dict) -> dict:
        raw_rect = patch.get("visual_rect")
        rect = raw_rect if isinstance(raw_rect, dict) else patch
        return {
            "x": _num(rect.get("x"), _num(fallback.get("x"))),
            "y": _num(rect.get("y"), _num(fallback.get("y"))),
            "width": max(1.0, _num(rect.get("width"), _num(fallback.get("width"), 1.0))),
            "height": max(1.0, _num(rect.get("height"), _num(fallback.get("height"), 1.0))),
        }

    def _redirect_visual_mask_patch(mask_layer: dict, owner_layer: dict, patch: dict) -> dict:
        new_visual = _rect_from_patch(patch, mask_layer)
        sx = new_visual["width"] / max(1.0, _num(mask_layer.get("width"), 1.0))
        sy = new_visual["height"] / max(1.0, _num(mask_layer.get("height"), 1.0))
        owner_x = _num(owner_layer.get("x"))
        owner_y = _num(owner_layer.get("y"))
        mask_x = _num(mask_layer.get("x"))
        mask_y = _num(mask_layer.get("y"))
        redirected = dict(patch)
        redirected.update(
            {
                "x": new_visual["x"] + (owner_x - mask_x) * sx,
                "y": new_visual["y"] + (owner_y - mask_y) * sy,
                "width": max(1.0, _num(owner_layer.get("width"), 1.0) * sx),
                "height": max(1.0, _num(owner_layer.get("height"), 1.0) * sy),
                "manual_transform": True,
                "visual_mask_id": str(mask_layer.get("id") or ""),
                "visual_rect": new_visual,
                "original_geometry": owner_layer.get("original_geometry") or {
                    "x": owner_x,
                    "y": owner_y,
                    "width": max(1.0, _num(owner_layer.get("width"), 1.0)),
                    "height": max(1.0, _num(owner_layer.get("height"), 1.0)),
                    "radius": _num(owner_layer.get("radius")),
                    "fill": owner_layer.get("fill"),
                },
                "original_visual_rect": owner_layer.get("original_visual_rect") or {
                    "x": mask_x,
                    "y": mask_y,
                    "width": max(1.0, _num(mask_layer.get("width"), 1.0)),
                    "height": max(1.0, _num(mask_layer.get("height"), 1.0)),
                    "radius": _num(mask_layer.get("radius")),
                    "fill": mask_layer.get("fill"),
                },
            }
        )
        return redirected

    def _patched_layer(layer: dict, patch: dict, allowed: set[str] | None = None) -> dict:
        next_layer = dict(layer)
        for key, value in patch.items():
            if key == "visual_rect":
                continue
            if allowed is not None and key not in allowed:
                continue
            if key in numeric_fields:
                try:
                    next_layer[key] = float(value)
                except (TypeError, ValueError):
                    continue
            elif key in text_fields:
                if key == "visual_mask_id" and value in (None, "", "None", "null"):
                    next_layer.pop(key, None)
                else:
                    next_layer[key] = str(value)
            elif key in boolean_fields:
                next_layer[key] = bool(value)
            elif key in object_fields:
                if value is None:
                    next_layer.pop(key, None)
                else:
                    rect = _normalize_rect(value)
                    if rect is not None:
                        next_layer[key] = rect
                    elif isinstance(value, dict):
                        next_layer[key] = value
        if "width" in next_layer:
            next_layer["width"] = max(1.0, float(next_layer["width"]))
        if "height" in next_layer:
            next_layer["height"] = max(1.0, float(next_layer["height"]))
        if "opacity" in next_layer:
            next_layer["opacity"] = max(0.0, min(1.0, float(next_layer["opacity"])))
        return next_layer

    updated = []
    found_motion = False
    found_layer = False
    for motion in state.motions:
        if motion.id != motion_id:
            updated.append(motion)
            continue
        found_motion = True
        request_layer_id = layer_id
        request_patch = dict(payload.patch or {})
        layer_items = list(motion.figma_layers or [])
        target_layer = next((layer for layer in layer_items if str(layer.get("id") or "") == request_layer_id), None)
        linked_mask_id = None
        patch_keys = set(request_patch)
        visual_rect_patch = request_patch.get("visual_rect") if isinstance(request_patch.get("visual_rect"), dict) else None
        visible_only_patch = patch_keys <= {"visible"}
        if (
            target_layer
            and target_layer.get("kind") == "shape"
            and not visible_only_patch
            and (patch_keys & (geometry_fields | boolean_fields) or visual_rect_patch)
        ):
            owner_layer = _image_layer_for_visual_mask(request_layer_id, layer_items)
            if owner_layer:
                request_patch = _redirect_visual_mask_patch(target_layer, owner_layer, request_patch)
                request_layer_id = str(owner_layer.get("id") or request_layer_id)
                target_layer = owner_layer
                patch_keys = set(request_patch)
                visual_rect_patch = request_patch.get("visual_rect") if isinstance(request_patch.get("visual_rect"), dict) else None
                visible_only_patch = patch_keys <= {"visible"}
        if target_layer and not visible_only_patch and (patch_keys & (geometry_fields | boolean_fields) or visual_rect_patch):
            linked_mask_id = _visual_mask_id(target_layer, layer_items)
        layers = []
        for layer in motion.figma_layers:
            current_id = str(layer.get("id") or "")
            if current_id != request_layer_id and current_id != linked_mask_id:
                layers.append(layer)
                continue
            if current_id == request_layer_id:
                found_layer = True
                target_patch = dict(request_patch)
                if linked_mask_id and "visual_mask_id" not in target_patch:
                    target_patch["visual_mask_id"] = linked_mask_id
                layers.append(_patched_layer(layer, target_patch))
            else:
                linked_patch = {key: value for key, value in request_patch.items() if key == "visible"}
                if visual_rect_patch:
                    linked_patch.update({key: value for key, value in visual_rect_patch.items() if key in geometry_fields})
                else:
                    linked_patch.update({key: value for key, value in request_patch.items() if key in geometry_fields})
                layers.append(_patched_layer(layer, linked_patch, geometry_fields | boolean_fields | object_fields))
        new_motion = _neutralize_figma_outer_motion(motion.model_copy(update={"figma_layers": layers}))
        new_motion = _render_motion_with_version(new_motion, base / "assets")
        updated.append(new_motion)
    if not found_motion:
        raise HTTPException(status_code=404, detail="Motion not found")
    if not found_layer:
        raise HTTPException(status_code=404, detail="Figma layer not found")
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Figma layer updated: {layer_id}")
    save_state(state)
    return state


@router.post("/projects/{project_id}/motion/{motion_id}/figma-layer/prompt")
def apply_figma_layer_motion_prompt(project_id: str, motion_id: str, payload: FigmaLayerMotionPromptRequest):
    state = _require_project(project_id)
    prompt = payload.prompt.strip()
    layer_id = payload.layer_id.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    if not layer_id:
        raise HTTPException(status_code=400, detail="Layer id is missing")
    mode = _motion_prompt_mode(payload.mode)
    _push_history(state)
    base = project_dir(project_id)
    updated = []
    found_motion = False
    found_layer = False
    recipe: dict | None = None
    for motion in state.motions:
        if motion.id != motion_id:
            updated.append(motion)
            continue
        found_motion = True
        layers = []
        target_layer = next((layer for layer in motion.figma_layers if str(layer.get("id") or "") == layer_id), None)
        if not target_layer:
            updated.append(motion)
            continue
        found_layer = True
        incoming_prompt = _strip_stored_motion_prompt(prompt)
        if should_use_frame_choreography_prompt(incoming_prompt):
            existing_prompt = _strip_stored_motion_prompt(getattr(motion, "prompt", "") or "")
            planning_prompt = _compose_motion_prompt(existing_prompt, incoming_prompt, mode)
            new_motion = _apply_frame_choreography_plan(motion, planning_prompt)
            new_motion = _render_motion_with_version(new_motion, base / "assets")
            updated.append(new_motion)
            continue
        recipe = build_layer_motion_recipe_from_prompt(
            prompt=prompt,
            mode=mode,
            target_layer=target_layer,
            all_layers=list(motion.figma_layers or []),
            timeline_duration=float(motion.duration or 0),
        )
        for layer in motion.figma_layers:
            if str(layer.get("id") or "") == layer_id:
                next_layer = dict(layer)
                if recipe:
                    next_layer["motion_recipe"] = recipe
                else:
                    next_layer.pop("motion_recipe", None)
                layers.append(next_layer)
            else:
                layers.append(layer)
        new_motion = _neutralize_figma_outer_motion(motion.model_copy(update={"figma_layers": layers}))
        new_motion = _render_motion_with_version(new_motion, base / "assets")
        updated.append(new_motion)
    if not found_motion:
        raise HTTPException(status_code=404, detail="Motion not found")
    if not found_layer:
        raise HTTPException(status_code=404, detail="Figma layer not found")
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    operation = recipe.get("motion_operation") if isinstance(recipe, dict) else {}
    qa = recipe.get("visual_qa") if isinstance(recipe, dict) else {}
    operation_type = str(operation.get("type") or mode)
    qa_status = str(qa.get("status") or "unknown")
    add_note(state, f"Figma layer motion prompt applied ({operation_type}, qa={qa_status}): {layer_id}")
    save_state(state)
    return state


@router.delete("/projects/{project_id}/motion/{motion_id}/figma-layer/{layer_id}/motion-action/{action_id}")
def delete_figma_layer_motion_action(project_id: str, motion_id: str, layer_id: str, action_id: str):
    state = _require_project(project_id)
    _push_history(state)
    base = project_dir(project_id)
    updated = []
    found_motion = False
    found_layer = False
    found_action = False
    for motion in state.motions:
        if motion.id != motion_id:
            updated.append(motion)
            continue
        found_motion = True
        layers = []
        for layer in motion.figma_layers:
            if str(layer.get("id") or "") != str(layer_id):
                layers.append(layer)
                continue
            found_layer = True
            next_layer = dict(layer)
            recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else {}
            next_recipe, _operation, found_action = build_layer_motion_recipe_after_delete(recipe, action_id, layer)
            if next_recipe:
                next_layer["motion_recipe"] = next_recipe
            else:
                next_layer.pop("motion_recipe", None)
            layers.append(next_layer)
        new_motion = motion.model_copy(update={"figma_layers": layers})
        new_motion = _render_motion_with_version(new_motion, base / "assets")
        updated.append(new_motion)
    if not found_motion:
        raise HTTPException(status_code=404, detail="Motion not found")
    if not found_layer:
        raise HTTPException(status_code=404, detail="Figma layer not found")
    if not found_action:
        raise HTTPException(status_code=404, detail="Motion action not found")
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Figma layer motion action deleted: {layer_id}/{action_id}")
    save_state(state)
    return state


def _ltx_layer_video_worker(project_id: str, motion_id: str, layer_id: str, payload: LtxLayerVideoRequest):
    def worker(job_id: str) -> None:
        cancel_event = get_job_cancel_event(job_id)
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("LTX generation cancelled")
            state = load_state(project_id)
            base = project_dir(project_id)
            motion = next((item for item in state.motions if item.id == motion_id), None)
            if not motion:
                raise RuntimeError("Motion not found")
            target_layer = next((layer for layer in motion.figma_layers if str(layer.get("id") or "") == layer_id), None)
            if not target_layer:
                raise RuntimeError("Figma layer not found")
            update_job(job_id, progress=8, message="Preparing selected layer PNG")
            update_job(job_id, progress=12, message="Running LTX 2.3 generation")
            preview = generate_ltx_layer_preview(
                project_root=base,
                motion=motion,
                layer_id=layer_id,
                prompt=payload.prompt,
                duration=payload.duration,
                fps=payload.fps,
                max_side=payload.max_side,
                seed=payload.seed,
                cancel_event=cancel_event,
            )
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("LTX generation cancelled")
            update_job(job_id, progress=94, message="Saving LTX preview")
            latest = load_state(project_id)
            updated_motions = []
            for current_motion in latest.motions:
                if current_motion.id != motion_id:
                    updated_motions.append(current_motion)
                    continue
                layers = []
                for layer in current_motion.figma_layers:
                    next_layer = dict(layer)
                    if str(next_layer.get("id") or "") == layer_id:
                        next_layer["ltx_prompt"] = preview["prompt"]
                        next_layer["ltx_preview"] = {key: value for key, value in preview.items() if key != "prompt"}
                    layers.append(next_layer)
                updated_motions.append(current_motion.model_copy(update={"figma_layers": layers}))
            latest.motions = updated_motions
            latest.current_job_id = None
            latest.last_error = None
            latest.status = "motion-updated"
            add_note(latest, f"LTX preview generated for layer: {layer_id}")
            save_state(latest)
            update_job(job_id, progress=100, message="LTX preview ready")
        except (JobCancelled, LtxGenerationCancelled) as exc:
            latest = load_state(project_id)
            if latest.current_job_id == job_id:
                latest.current_job_id = None
            latest.last_error = None
            latest.status = "motion-updated"
            add_note(latest, f"LTX preview cancelled for layer: {layer_id}")
            save_state(latest)
            raise JobCancelled(str(exc) or "LTX generation cancelled") from exc
        except Exception as exc:
            latest = load_state(project_id)
            if latest.current_job_id == job_id:
                latest.current_job_id = None
            latest.last_error = str(exc)
            add_note(latest, f"LTX preview failed for layer {layer_id}: {exc}")
            save_state(latest)
            raise

    return worker


@router.post("/projects/{project_id}/motion/{motion_id}/figma-layer/ltx")
def generate_figma_layer_ltx_video(project_id: str, motion_id: str, payload: LtxLayerVideoRequest):
    state = _require_project(project_id)
    layer_id = payload.layer_id.strip()
    if not layer_id:
        raise HTTPException(status_code=400, detail="Layer id is missing")
    if not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is empty")
    if state.current_job_id:
        job = get_job(state.current_job_id)
        if job and job.status in {"queued", "running"}:
            return job
    motion = next((item for item in state.motions if item.id == motion_id), None)
    if not motion:
        raise HTTPException(status_code=404, detail="Motion not found")
    if not any(str(layer.get("id") or "") == layer_id for layer in motion.figma_layers):
        raise HTTPException(status_code=404, detail="Figma layer not found")
    job = create_job(project_id=project_id, kind="ltx", message="Queued LTX 2.3 layer animation")
    state.current_job_id = job.job_id
    state.last_error = None
    save_state(state)
    return submit_job(job, _ltx_layer_video_worker(project_id, motion_id, layer_id, payload))


@router.post("/projects/{project_id}/motion/{motion_id}/figma-layer/ltx/apply")
def apply_figma_layer_ltx_video(project_id: str, motion_id: str, payload: LtxLayerVideoApplyRequest):
    state = _require_project(project_id)
    layer_id = payload.layer_id.strip()
    if not layer_id:
        raise HTTPException(status_code=400, detail="Layer id is missing")
    _push_history(state)
    base = project_dir(project_id)
    updated = []
    found_motion = False
    found_layer = False
    for motion in state.motions:
        if motion.id != motion_id:
            updated.append(motion)
            continue
        found_motion = True
        layers = []
        for layer in motion.figma_layers:
            next_layer = dict(layer)
            if str(next_layer.get("id") or "") == layer_id:
                found_layer = True
                preview = next_layer.get("ltx_preview")
                if not isinstance(preview, dict) or not preview.get("preview_path"):
                    raise HTTPException(status_code=400, detail="No LTX preview to apply")
                next_layer["ltx_video_path"] = preview["preview_path"]
                next_layer["ltx_prompt"] = next_layer.get("ltx_prompt") or ""
                next_layer["ltx_duration"] = preview.get("duration")
                next_layer["ltx_fps"] = preview.get("fps")
                next_layer["ltx_max_side"] = preview.get("max_side")
            layers.append(next_layer)
        new_motion = motion.model_copy(update={"figma_layers": layers})
        new_motion = _render_motion_with_version(new_motion, base / "assets")
        updated.append(new_motion)
    if not found_motion:
        raise HTTPException(status_code=404, detail="Motion not found")
    if not found_layer:
        raise HTTPException(status_code=404, detail="Figma layer not found")
    state.motions = updated
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"LTX preview applied to layer: {layer_id}")
    save_state(state)
    return state


@router.delete("/projects/{project_id}/motion/{motion_id}")
def delete_motion(project_id: str, motion_id: str):
    state = _require_project(project_id)
    _push_history(state)
    base = project_dir(project_id)
    remaining = [motion for motion in state.motions if motion.id != motion_id]
    if len(remaining) == len(state.motions):
        raise HTTPException(status_code=404, detail="Motion not found")
    asset = base / "assets" / f"{motion_id}.png"
    if asset.exists():
        asset.unlink()
    state.motions = remaining
    state.status = "motion-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Motion deleted: {motion_id}")
    save_state(state)
    return state


@router.put("/projects/{project_id}/clips/{clip_id}")
def update_clip(project_id: str, clip_id: str, payload: ClipUpdateRequest):
    state = _require_project(project_id)
    base = project_dir(project_id)
    source = base / state.source_video if state.source_video else None
    source_duration = state.transcript.duration if state.transcript and state.transcript.duration else (
        detect_duration(source) if source and source.exists() else float(payload.source_end)
    )
    if not state.edit_plan:
        if not state.source_video or source_duration <= 0:
            raise HTTPException(status_code=400, detail="No clips to edit")
        _push_history(state)
        state.edit_plan = EditPlan(
            summary="Manual timeline",
            strategy="Manual clip edit from full source",
            estimated_duration=round(source_duration, 3),
            keep_ranges=[
                CutRange(
                    start=0.0,
                    end=round(source_duration, 3),
                    reason="Full source",
                    source="source",
                    handle_start=0.0,
                    handle_end=round(source_duration, 3),
                )
            ],
            subtitle_style="none",
        )
    else:
        _push_history(state)
    normalize_clip_handles(state, source_duration)
    try:
        index = int(clip_id.removeprefix("b")) - 1
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid clip id") from exc
    if index < 0 or index >= len(state.edit_plan.keep_ranges):
        raise HTTPException(status_code=404, detail="Clip not found")
    old = state.edit_plan.keep_ranges[index]
    min_start = 0.0
    max_end = source_duration
    start = max(min_start, min(float(payload.source_start), old.end - 0.1))
    end = min(max_end, max(start + 0.1, float(payload.source_end)))
    state.edit_plan.keep_ranges[index] = CutRange(
        start=start,
        end=end,
        reason=old.reason,
        source=old.source,
        handle_start=min_start,
        handle_end=max_end,
    )
    if payload.start is not None and len(state.edit_plan.keep_ranges) > 1:
        moved = state.edit_plan.keep_ranges.pop(index)
        target_start = max(0.0, float(payload.start))
        cursor = 0.0
        insert_index = len(state.edit_plan.keep_ranges)
        for candidate_index, keep_range in enumerate(state.edit_plan.keep_ranges):
            duration = max(0.0, keep_range.end - keep_range.start)
            midpoint = cursor + duration / 2
            if target_start < midpoint:
                insert_index = candidate_index
                break
            cursor += duration
        state.edit_plan.keep_ranges.insert(insert_index, moved)
    _estimate_duration(state)
    state.status = "timeline-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Clip updated: {clip_id}")
    save_state(state)
    return state


@router.delete("/projects/{project_id}/clips/{clip_id}")
def delete_clip(project_id: str, clip_id: str):
    state = _require_project(project_id)
    if not state.edit_plan:
        raise HTTPException(status_code=400, detail="Analyze project first")
    _push_history(state)
    try:
        index = int(clip_id.removeprefix("b")) - 1
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid clip id") from exc
    if index < 0 or index >= len(state.edit_plan.keep_ranges):
        raise HTTPException(status_code=404, detail="Clip not found")
    removed = state.edit_plan.keep_ranges.pop(index)
    _estimate_duration(state)
    state.status = "timeline-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Clip deleted: {clip_id} ({removed.start:.2f}-{removed.end:.2f})")
    save_state(state)
    return state


@router.post("/projects/{project_id}/timeline/delete")
def delete_timeline_items(project_id: str, payload: TimelineDeleteRequest):
    state = _require_project(project_id)
    _push_history(state)
    clip_indexes: set[int] = set()
    motion_ids: set[str] = set()

    for item in payload.items:
        item_type = item.get("type")
        item_id = item.get("id", "")
        if item_type == "clip":
            try:
                clip_indexes.add(int(item_id.removeprefix("b")) - 1)
            except ValueError:
                continue
        elif item_type == "motion":
            motion_ids.add(item_id)

    if state.edit_plan and clip_indexes:
        state.edit_plan.keep_ranges = [
            keep_range
            for index, keep_range in enumerate(state.edit_plan.keep_ranges)
            if index not in clip_indexes
        ]
        _estimate_duration(state)

    if motion_ids:
        base = project_dir(project_id)
        state.motions = [motion for motion in state.motions if motion.id not in motion_ids]
        for motion_id in motion_ids:
            asset = base / "assets" / f"{motion_id}.png"
            if asset.exists():
                asset.unlink()

    state.status = "timeline-updated"
    state.outputs.pop("preview", None)
    add_note(state, f"Timeline items deleted: {len(clip_indexes) + len(motion_ids)}")
    save_state(state)
    return state
