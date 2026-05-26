from __future__ import annotations

import math
import subprocess
from pathlib import Path

from app.models.schemas import ProjectState
from app.services.media import detect_duration


def _run_quiet(command: list[str]) -> bool:
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def _ensure_thumbnails(video: Path, project_root: Path, project_id: str, duration: float, count: int = 18) -> list[dict]:
    thumbs_dir = project_root / "assets" / "timeline_thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    if duration <= 0:
        return []

    thumbs: list[dict] = []
    for index in range(count):
        t = min(duration, (duration * index) / max(1, count - 1))
        output = thumbs_dir / f"thumb_{index:02d}.jpg"
        if not output.exists():
            _run_quiet(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=220:-2",
                    "-q:v",
                    "4",
                    str(output),
                ]
            )
        if output.exists():
            thumbs.append(
                {
                    "time": round(t, 3),
                    "url": f"/projects/{project_id}/assets/timeline_thumbs/{output.name}",
                }
            )
    return thumbs


def _ensure_clip_thumbnails(
    video: Path,
    project_root: Path,
    project_id: str,
    clip_id: str,
    source_start: float,
    source_end: float,
) -> list[dict]:
    duration = max(0.0, source_end - source_start)
    if duration <= 0:
        return []

    thumbs_dir = project_root / "assets" / "timeline_clip_thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, min(10, math.ceil(duration / 0.75)))
    thumbs: list[dict] = []
    for index in range(count):
        offset = duration * ((index + 0.5) / count)
        t = min(source_end, source_start + offset)
        output = thumbs_dir / f"{clip_id}_{index:02d}_{int(source_start * 1000)}_{int(source_end * 1000)}.jpg"
        if not output.exists():
            _run_quiet(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=180:-2",
                    "-q:v",
                    "4",
                    str(output),
                ]
            )
        if output.exists():
            thumbs.append(
                {
                    "time": round(offset, 3),
                    "url": f"/projects/{project_id}/assets/timeline_clip_thumbs/{output.name}",
                }
            )
    return thumbs


def _audio_bars(state: ProjectState, duration: float, count: int = 180) -> list[float]:
    if duration <= 0 or not state.transcript or not state.transcript.words:
        return [0.08 for _ in range(count)]

    bars = [0.04 for _ in range(count)]
    for word in state.transcript.words:
        start_index = max(0, min(count - 1, int((word.start / duration) * count)))
        end_index = max(start_index, min(count - 1, int((word.end / duration) * count)))
        value = min(1.0, 0.24 + len(word.text) / 16)
        for index in range(start_index, end_index + 1):
            bars[index] = max(bars[index], value)

    # Slight smoothing so the row reads like a waveform instead of a barcode.
    smoothed: list[float] = []
    for index, value in enumerate(bars):
        prev_value = bars[index - 1] if index > 0 else value
        next_value = bars[index + 1] if index < len(bars) - 1 else value
        smoothed.append(round((prev_value + value * 1.8 + next_value) / 3.8, 3))
    return smoothed


def normalize_clip_handles(state: ProjectState, source_duration: float) -> bool:
    """Let every clip trim back to the full original source media."""
    if not state.edit_plan:
        return False

    changed = False
    ranges = state.edit_plan.keep_ranges or []
    rounded_start = 0.0
    rounded_end = round(max(0.0, source_duration), 3)
    for item in ranges:
        if item.handle_start != rounded_start or item.handle_end != rounded_end:
            item.handle_start = rounded_start
            item.handle_end = rounded_end
            changed = True

    return changed


def build_timeline(project_id: str, state: ProjectState, project_root: Path) -> dict:
    source = project_root / state.source_video if state.source_video else None
    source_duration = detect_duration(source) if source and source.exists() else 0.0
    normalize_clip_handles(state, source_duration)

    clips = []
    cursor = 0.0
    if state.edit_plan:
        keep_ranges = state.edit_plan.keep_ranges or []
        for index, item in enumerate(keep_ranges, start=1):
            duration = max(0.0, item.end - item.start)
            clip_id = f"b{index:02d}"
            clips.append(
                {
                    "id": clip_id,
                    "label": clip_id,
                    "start": round(cursor, 3),
                    "duration": round(duration, 3),
                    "source_start": item.start,
                    "source_end": item.end,
                    "source_video": state.source_video,
                    "min_source_start": item.handle_start if item.handle_start is not None else 0.0,
                    "max_source_end": item.handle_end if item.handle_end is not None else round(source_duration, 3),
                    "full_source_start": 0.0,
                    "full_source_end": round(source_duration, 3),
                    "reason": item.reason,
                    "thumbnails": _ensure_clip_thumbnails(source, project_root, project_id, clip_id, item.start, item.end)
                    if source and source.exists()
                    else [],
                }
            )
            cursor += duration
    elif source_duration:
        clips.append(
            {
                "id": "b01",
                "label": "b01",
                "start": 0,
                "duration": round(source_duration, 3),
                "source_start": 0,
                "source_end": round(source_duration, 3),
                "min_source_start": 0,
                "max_source_end": round(source_duration, 3),
                "reason": "Full source",
                "thumbnails": _ensure_clip_thumbnails(source, project_root, project_id, "b01", 0, source_duration)
                if source and source.exists()
                else [],
            }
        )
        cursor = source_duration

    output_duration = cursor if state.edit_plan else source_duration
    motions = []
    for motion in state.motions:
        plan = motion.motion_plan if isinstance(motion.motion_plan, dict) else {}
        beat = plan.get("agent_beat") if isinstance(plan.get("agent_beat"), dict) else {}
        beat_id = str(beat.get("id") or beat.get("beat_id") or "").strip()
        motions.append(
            {
                "id": motion.id,
                "label": f"{beat_id} · {motion.text}" if beat_id else motion.text,
                "preset": beat_id or motion.design_preset,
                "start": motion.start,
                "duration": motion.duration,
                "source_type": motion.source_type,
                "track": "frames" if motion.source_type == "figma" else "graphics",
            }
        )

    time_marks = []
    if output_duration:
        step = max(1, math.ceil(output_duration / 6))
        current = 0
        while current <= output_duration + 0.01:
            time_marks.append(round(current, 2))
            current += step
        if not time_marks or abs(float(time_marks[-1]) - output_duration) > 0.05:
            time_marks.append(round(output_duration, 2))

    return {
        "project_id": project_id,
        "source_duration": round(source_duration, 3),
        "duration": round(output_duration, 3),
        "clips": clips,
        "motions": motions,
        "audio_bars": _audio_bars(state, output_duration),
        "thumbnails": _ensure_thumbnails(source, project_root, project_id, source_duration) if source else [],
        "time_marks": time_marks,
    }
