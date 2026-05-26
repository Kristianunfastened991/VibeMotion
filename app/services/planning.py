from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import CutRange, CutSuggestion, EditPlan, TranscriptData
from app.services.analysis import build_keep_ranges, detect_audio_silences, find_suggestions, merge_suggestions
from app.services.ollama import OllamaError, chat_json


SYSTEM_PROMPT = """You are a video editing planner.
Return valid JSON only.
You receive a transcript and heuristic cut suggestions.
Your task is to propose a rough-cut edit plan for a talking-head video.
Preserve coherent speech. Remove pauses and filler words when safe.
Do not invent timestamps outside the source duration.
"""


def heuristic_plan(transcript: TranscriptData, source_video: Path | None = None) -> EditPlan:
    suggestions = find_suggestions(transcript)
    if source_video is not None and source_video.exists():
        suggestions = merge_suggestions([*suggestions, *detect_audio_silences(source_video)])
    keep_ranges = build_keep_ranges(transcript.duration or 0.0, suggestions)
    if not keep_ranges and transcript.duration:
        keep_ranges = [CutRange(start=0.0, end=round(transcript.duration, 3), reason="Keep full source")]
    estimated_duration = round(sum(item.end - item.start for item in keep_ranges), 3)
    return EditPlan(
        summary="Heuristic rough cut based on pauses, filler words, and safe repeated starts.",
        strategy="Remove long pauses, clear filler sounds, isolated filler phrases, and conservative retakes while preserving contiguous speech.",
        estimated_duration=estimated_duration,
        keep_ranges=keep_ranges,
        suggestions=suggestions,
        motion_notes=["Use clean motion cards for key claims or section transitions."],
    )


def source_plan(transcript: TranscriptData) -> EditPlan:
    duration = round(float(transcript.duration or 0.0), 3)
    keep_ranges = [CutRange(start=0.0, end=duration, reason="Keep full source")] if duration > 0 else []
    return EditPlan(
        summary="Full-source edit plan: no pause or filler-word cleanup was requested.",
        strategy="Keep the original video timing. Add requested graphics or subtitles without trimming the source.",
        estimated_duration=duration,
        keep_ranges=keep_ranges,
        suggestions=[],
        motion_notes=["Use clean motion cards for key claims or section transitions."],
    )


def cleanup_plan(transcript: TranscriptData, source_video: Path | None = None) -> EditPlan:
    plan = heuristic_plan(transcript, source_video)
    return plan.model_copy(
        update={
            "summary": "Cleanup-only rough cut: pauses, clear filler sounds, and safe repeated starts are removed as editable clip boundaries.",
            "strategy": "Cut pauses, isolated filler phrases, and conservative retakes into separate editable clips. Do not add subtitles or motion graphics.",
            "subtitle_style": "none",
            "motion_notes": [],
        }
    )


def llm_plan(transcript: TranscriptData, suggestions: list[CutSuggestion]) -> EditPlan:
    prompt = {
        "duration": transcript.duration,
        "transcript_excerpt": transcript.text[:6000],
        "heuristic_suggestions": [item.model_dump(mode="json") for item in suggestions[:120]],
        "required_response_shape": {
            "summary": "string",
            "strategy": "string",
            "estimated_duration": "number",
            "keep_ranges": [
                {"start": "number", "end": "number", "reason": "string", "source": "source"}
            ],
            "motion_notes": ["string"],
            "subtitle_style": "string",
        },
    }
    response = chat_json(SYSTEM_PROMPT, json.dumps(prompt, ensure_ascii=False))
    keep_ranges = [
        CutRange(
            start=round(float(item["start"]), 3),
            end=round(float(item["end"]), 3),
            reason=str(item.get("reason", "Keep spoken content")),
            source=str(item.get("source", "source")),
        )
        for item in response.get("keep_ranges", [])
        if float(item["end"]) > float(item["start"])
    ]
    estimated_duration = round(sum(item.end - item.start for item in keep_ranges), 3)
    return EditPlan(
        summary=str(response.get("summary", "LLM-generated rough cut.")),
        strategy=str(response.get("strategy", "Trim pauses and filler words.")),
        estimated_duration=estimated_duration,
        keep_ranges=keep_ranges,
        suggestions=suggestions,
        subtitle_style=str(response.get("subtitle_style", "bold-overlay")),
        motion_notes=[str(item) for item in response.get("motion_notes", [])],
    )


def generate_edit_plan(
    transcript: TranscriptData,
    source_video: Path | None = None,
    cleanup_only: bool = False,
    use_llm: bool = True,
) -> EditPlan:
    if cleanup_only:
        return cleanup_plan(transcript, source_video)
    base = heuristic_plan(transcript, source_video)
    if not use_llm or not transcript.text.strip():
        return base
    try:
        llm = llm_plan(transcript, base.suggestions)
    except OllamaError:
        return base
    return base.model_copy(
        update={
            "summary": llm.summary or base.summary,
            "strategy": f"{base.strategy} LLM is used for design notes only; heuristic cuts remain mandatory.",
            "subtitle_style": llm.subtitle_style or base.subtitle_style,
            "motion_notes": llm.motion_notes or base.motion_notes,
        }
    )
