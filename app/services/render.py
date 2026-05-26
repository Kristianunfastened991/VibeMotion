from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from app.models.schemas import MotionSpec, ProjectState, TranscriptData
from app.services.hardware import preferred_ffmpeg_encoder
from app.services.hyperframes_render import HyperframesRenderError, render_hyperframes_preview
from app.services.media import detect_duration, detect_video_size
from app.services.motion import fit_motion_to_canvas, motion_asset_signature, render_motion_asset


PRESERVE_SOURCE_SIZE_FILTER = "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def _run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(
        command,
        check=True,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _video_encode_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-cq",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
    ]


def extract_segments(source: Path, keep_ranges: list[tuple[float, float]], work_dir: Path) -> list[Path]:
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    encoder = preferred_ffmpeg_encoder()
    for index, (start, end) in enumerate(keep_ranges):
        duration = max(0.05, end - start)
        output = clips_dir / f"seg_{index:03d}.mp4"
        fade_out_start = max(0.0, duration - 0.03)
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-vf",
            PRESERVE_SOURCE_SIZE_FILTER,
            "-af",
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03",
            *_video_encode_args(encoder),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
        try:
            _run(command)
        except subprocess.CalledProcessError:
            if encoder == "h264_nvenc":
                fallback = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(source),
                    "-t",
                    f"{duration:.3f}",
                    "-vf",
                    PRESERVE_SOURCE_SIZE_FILTER,
                    "-af",
                    f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03",
                    *_video_encode_args("libx264"),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    str(output),
                ]
                _run(fallback)
            else:
                raise
        outputs.append(output)
    return outputs


def concat_segments(segments: list[Path], work_dir: Path) -> Path:
    concat_file = work_dir / "concat.txt"
    concat_file.write_text("".join(f"file '{item.resolve()}'\n" for item in segments), encoding="utf-8")
    output = work_dir / "base.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output),
    ]
    try:
        _run(command)
    except subprocess.CalledProcessError:
        # Some generated/project clips differ in encoder parameters enough that
        # stream-copy concat is rejected. Re-encode as a compatibility fallback
        # instead of failing the whole render.
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-vf",
                PRESERVE_SOURCE_SIZE_FILTER,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(output),
            ]
        )
    return output


def build_srt(transcript: TranscriptData, keep_ranges: list[tuple[float, float]], output: Path) -> Path | None:
    entries: list[tuple[float, float, str]] = []
    timeline_offset = 0.0
    words = transcript.words
    for start, end in keep_ranges:
        in_range = [word for word in words if word.end > start and word.start < end]
        chunk: list = []
        for word in in_range:
            chunk.append(word)
            normalized = word.text.strip()
            if len(chunk) >= 3 or normalized.endswith((".", "!", "?")):
                first = chunk[0]
                last = chunk[-1]
                entries.append(
                    (
                        max(0.0, first.start - start + timeline_offset),
                        max(0.05, last.end - start + timeline_offset),
                        " ".join(item.text for item in chunk).upper(),
                    )
                )
                chunk = []
        if chunk:
            first = chunk[0]
            last = chunk[-1]
            entries.append(
                (
                    max(0.0, first.start - start + timeline_offset),
                    max(0.05, last.end - start + timeline_offset),
                    " ".join(item.text for item in chunk).upper(),
                )
            )
        timeline_offset += end - start

    def stamp(value: float) -> str:
        milliseconds = int(round(value * 1000))
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        lines.extend([str(index), f"{stamp(start)} --> {stamp(end)}", text, ""])
    if not lines:
        return None
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def burn_subtitles(base_video: Path, subtitles: Path, output: Path) -> Path:
    encoder = preferred_ffmpeg_encoder()
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(base_video),
        "-vf",
        f"subtitles='{subtitles.name}'",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        *_video_encode_args(encoder),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output),
    ]
    try:
        _run(command, cwd=output.parent)
    except subprocess.CalledProcessError:
        if encoder != "h264_nvenc":
            raise
        fallback = [
            "ffmpeg",
            "-y",
            "-i",
            str(base_video),
            "-vf",
            f"subtitles='{subtitles.name}'",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            *_video_encode_args("libx264"),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
        _run(fallback, cwd=output.parent)
    return output


def _overlay_position_expressions(motion: MotionSpec, duration: float) -> tuple[str, str]:
    start = float(motion.start)
    end = start + duration
    x_final = str(int(motion.x))
    y_final = str(int(motion.y))
    x_expr = x_final
    y_expr = y_final
    frame_choreography = _is_frame_choreography_motion(motion)
    outro_phase = _frame_choreography_phase(motion, "outro") if frame_choreography else None

    def entry_target(direction: str) -> tuple[str, str]:
        if direction == "left":
            return f"-{int(motion.width) + 8}", y_final
        if direction == "right":
            return "main_w+8", y_final
        if direction == "top":
            return x_final, f"-{int(motion.height) + 8}"
        if direction == "bottom":
            return x_final, "main_h+8"
        return x_final, y_final

    enter_duration = max(0.05, min(duration, float(getattr(motion, "enter_duration", 0.35) or 0.35)))
    exit_duration = max(0.05, min(duration, float(getattr(motion, "exit_duration", 0.35) or 0.35)))

    enter_animation = "none" if frame_choreography else getattr(motion, "enter_animation", "slide")
    if enter_animation in {"slide", "drop", "rise"}:
        enter_direction = "top" if enter_animation == "drop" else "bottom" if enter_animation == "rise" else getattr(motion, "enter_from", "right")
        x_from, y_from = entry_target(enter_direction)
        x_expr = f"if(lt(t,{start + enter_duration:.3f}), {x_from}+({x_final}-({x_from}))*((t-{start:.3f})/{enter_duration:.3f}), {x_expr})"
        y_expr = f"if(lt(t,{start + enter_duration:.3f}), {y_from}+({y_final}-({y_from}))*((t-{start:.3f})/{enter_duration:.3f}), {y_expr})"

    exit_animation = getattr(motion, "exit_animation", "slide")
    exit_start = max(start, end - exit_duration)
    if frame_choreography:
        exit_animation = "none"
        if str((outro_phase or {}).get("preset") or "").strip().casefold() in {
            "gravity-drop-fade",
            "full-frame-drop",
            "basic-frame-drop",
            "basic-frame-drop-out",
        }:
            exit_animation = "drop"
            try:
                exit_duration = max(0.05, min(duration, float(outro_phase.get("duration") or exit_duration)))
                exit_start = start + max(0.0, float(outro_phase.get("start") or (duration - exit_duration)))
            except (TypeError, ValueError):
                exit_start = max(start, end - exit_duration)
    if exit_animation in {"slide", "drop", "rise"}:
        exit_direction = "bottom" if exit_animation == "drop" else "top" if exit_animation == "rise" else getattr(motion, "exit_to", "left")
        x_to, y_to = entry_target(exit_direction)
        x_expr = f"if(gt(t,{exit_start:.3f}), {x_final}+(({x_to})-{x_final})*((t-{exit_start:.3f})/{exit_duration:.3f}), {x_expr})"
        y_expr = f"if(gt(t,{exit_start:.3f}), {y_final}+(({y_to})-{y_final})*((t-{exit_start:.3f})/{exit_duration:.3f}), {y_expr})"

    return x_expr, y_expr


def _figma_video_fade_in_duration(motion: MotionSpec) -> float:
    if getattr(motion, "source_type", "generated") != "figma":
        return 0.0
    for layer in list(getattr(motion, "figma_layers", []) or []):
        try:
            duration = float(layer.get("choreo_video_fade_in") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            return duration
    return 0.0


def _figma_video_fade_out_duration(motion: MotionSpec) -> float:
    if getattr(motion, "source_type", "generated") != "figma":
        return 0.0
    outro_phase = _frame_choreography_phase(motion, "outro")
    if str((outro_phase or {}).get("preset") or "").strip().casefold() == "full-frame-shatter":
        return 0.0
    if str((outro_phase or {}).get("preset") or "").strip().casefold() in {
        "gravity-drop-fade",
        "full-frame-fade-out",
        "basic-frame-fade-out",
        "basic-frame-drop-out",
    }:
        try:
            return max(0.0, min(float(getattr(motion, "duration", 0.0) or 0.0), float(outro_phase.get("duration") or 0.0)))
        except (TypeError, ValueError):
            return 0.0
    total_duration = max(0.1, float(getattr(motion, "duration", 0.1) or 0.1))
    best_duration = 0.0
    for layer in list(getattr(motion, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") or {}
        dsl = recipe.get("motion_dsl") if isinstance(recipe, dict) else None
        keyframes = dsl.get("keyframes") if isinstance(dsl, dict) else None
        if not isinstance(keyframes, list) or len(keyframes) < 2:
            continue
        frames = []
        for frame in keyframes:
            if not isinstance(frame, dict):
                continue
            try:
                frames.append((float(frame.get("time") or 0), float(frame.get("opacity", 1))))
            except (TypeError, ValueError):
                continue
        if len(frames) < 2:
            continue
        frames.sort(key=lambda item: item[0])
        end_time, end_opacity = frames[-1]
        if end_time < total_duration - 0.05 or end_opacity > 0.05:
            continue
        fade_start = None
        for time_value, opacity in reversed(frames[:-1]):
            if opacity >= 0.98:
                fade_start = time_value
                break
        if fade_start is None:
            continue
        best_duration = max(best_duration, max(0.0, total_duration - fade_start))
    return min(total_duration, best_duration)


def _frame_choreography_phase(motion: MotionSpec, phase_id: str) -> dict | None:
    candidates: list[dict] = []
    plan = getattr(motion, "motion_plan", None)
    if isinstance(plan, dict):
        candidates.append(plan)
    for layer in list(getattr(motion, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
        phase_plan = recipe.get("phase_plan") if isinstance(recipe, dict) and isinstance(recipe.get("phase_plan"), dict) else None
        if phase_plan:
            candidates.append(phase_plan)
    for candidate in candidates:
        for phase in list(candidate.get("phases") or []):
            if isinstance(phase, dict) and str(phase.get("id") or "") == phase_id:
                return dict(phase)
    return None


def _is_frame_choreography_motion(motion: MotionSpec) -> bool:
    for layer in list(getattr(motion, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
        tags = recipe.get("tags") if isinstance(recipe, dict) and isinstance(recipe.get("tags"), list) else []
        if any(str(tag).strip().casefold() == "frame" for tag in tags):
            return True
    plan = getattr(motion, "motion_plan", None)
    return isinstance(plan, dict) and plan.get("scope") == "whole-frame"


def _motion_video_asset_path(assets_dir: Path, motion: MotionSpec) -> Path:
    raw_path = getattr(motion, "video_asset_path", None)
    if not raw_path:
        return assets_dir / f"{motion.id}.mp4"
    path = Path(raw_path)
    if path.is_absolute():
        return path
    project_relative = assets_dir.parent / path
    if project_relative.exists():
        return project_relative
    return assets_dir / path


def _latest_motion_video_relpath(project_root: Path, motion_id: str) -> str | None:
    assets_dir = project_root / "assets"
    videos = sorted(assets_dir.glob(f"{motion_id}-*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if videos:
        return str(videos[0].relative_to(project_root))
    legacy = assets_dir / f"{motion_id}.mp4"
    return str(legacy.relative_to(project_root)) if legacy.exists() else None


def _even_video_dimension(value: int) -> int:
    value = max(1, int(value))
    if value <= 2:
        return value
    return value - (value % 2)


def _expected_motion_video_size(assets_dir: Path, motion: MotionSpec) -> tuple[int, int]:
    if getattr(motion, "source_type", "generated") == "figma" and getattr(motion, "asset_path", None):
        source = assets_dir.parent / str(motion.asset_path)
        if source.exists():
            try:
                with Image.open(source) as image:
                    return _even_video_dimension(image.width), _even_video_dimension(image.height)
            except Exception:
                pass
    return (
        _even_video_dimension(int(round(float(motion.width)))),
        _even_video_dimension(int(round(float(motion.height)))),
    )


def _motion_video_matches_motion_size(path: Path, motion: MotionSpec, assets_dir: Path) -> bool:
    if not path.exists():
        return False
    try:
        width, height = detect_video_size(path)
    except Exception:
        return False
    target_width, target_height = _expected_motion_video_size(assets_dir, motion)
    return abs(width - target_width) <= 2 and abs(height - target_height) <= 2


def _motion_video_matches_current_dsl(path: Path, motion: MotionSpec, assets_dir: Path) -> bool:
    if not path.exists():
        return False
    expected_signature = motion_asset_signature(motion)
    if getattr(motion, "source_type", "generated") == "figma":
        visual_report = assets_dir / f"{motion.id}.visual-self-check.json"
        if not visual_report.exists():
            return False
        try:
            report = json.loads(visual_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if report.get("status") != "pass" or report.get("asset_signature") != expected_signature:
            return False
    return (
        getattr(motion, "asset_signature", None) == expected_signature
        and _motion_video_matches_motion_size(path, motion, assets_dir)
    )


def _recipe_motion_actions(recipe: dict) -> list[dict]:
    if not isinstance(recipe, dict) or not isinstance(recipe.get("motion_actions"), list):
        return []
    return [dict(action) for action in recipe.get("motion_actions") or [] if isinstance(action, dict)]


def _recipe_effects(recipe: dict) -> list[dict]:
    if not isinstance(recipe, dict):
        return []
    actions = _recipe_motion_actions(recipe)
    if actions:
        effects: list[dict] = []
        for action in actions:
            effects.extend(_recipe_effects(action))
        return effects
    dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
    return [effect for effect in list(dsl.get("effects") or []) if isinstance(effect, dict)]


def _figma_video_reveal_effect(motion: MotionSpec) -> dict | None:
    if getattr(motion, "source_type", "generated") != "figma":
        return None
    candidates: list[dict] = []
    for layer in list(getattr(motion, "figma_layers", []) or []):
        recipe = layer.get("motion_recipe") if isinstance(layer, dict) else None
        tags = {
            str(tag).strip().casefold()
            for tag in list((recipe or {}).get("tags") or [])
            if str(tag).strip()
        }
        for effect in _recipe_effects(recipe or {}):
            if str(effect.get("type") or "").strip().casefold() != "venetian-blinds":
                continue
            try:
                start = max(0.0, float(effect.get("start") or 0))
                duration = max(0.05, float(effect.get("duration") or 0))
                blades = max(2, min(64, int(float(effect.get("blades") or 12))))
            except (TypeError, ValueError):
                continue
            priority = 0 if {"white-intro", "background", "venetian-blinds-bg"} & tags else 1
            candidates.append(
                {
                    "type": "venetian-blinds",
                    "start": start,
                    "duration": duration,
                    "blades": blades,
                    "orientation": str(effect.get("orientation") or "vertical").strip().casefold(),
                    "priority": priority,
                }
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item["priority"], item["start"], item["duration"]))
    return candidates[0]


def _venetian_alpha_expression(effect: dict) -> str:
    start = max(0.0, float(effect.get("start") or 0.0))
    duration = max(0.05, float(effect.get("duration") or 0.05))
    blades = max(2, min(64, int(effect.get("blades") or 12)))
    orientation = str(effect.get("orientation") or "vertical").casefold()
    coord = "Y" if orientation == "horizontal" else "X"
    axis = "H" if orientation == "horizontal" else "W"
    period = f"({axis}/{blades})"
    progress = f"((T-{start:.6f})/{duration:.6f})"
    return (
        f"if(lt(T,{start:.6f}),0,"
        f"if(gte(T,{start + duration:.6f}),255,"
        f"if(lt(mod({coord},{period}),{period}*{progress}),255,0)))"
    )


def _figma_video_overlay_filter_parts(index: int, overlay_input: str, motion: MotionSpec, duration: float) -> tuple[list[str], str]:
    reveal_effect = _figma_video_reveal_effect(motion)
    fade_duration = 0.0 if reveal_effect else _figma_video_fade_in_duration(motion)
    fade_out_duration = _figma_video_fade_out_duration(motion)
    scaled_label = f"[ovscale{index}]"
    video_filter = (
        f"{overlay_input}"
        f"scale={max(1, int(motion.width))}:{max(1, int(motion.height))}:flags=lanczos,"
        "format=rgba"
    )
    if reveal_effect:
        video_filter += (
            ",geq="
            "r='r(X,Y)':"
            "g='g(X,Y)':"
            "b='b(X,Y)':"
            f"a='{_venetian_alpha_expression(reveal_effect)}'"
        )
    if fade_duration > 0:
        video_filter += f",fade=t=in:st=0:d={min(fade_duration, duration):.3f}:alpha=1"
    if fade_out_duration > 0:
        fade_start = max(0.0, duration - min(fade_out_duration, duration))
        video_filter += f",fade=t=out:st={fade_start:.3f}:d={min(fade_out_duration, duration):.3f}:alpha=1"
    return [f"{video_filter}{scaled_label}"], scaled_label


def apply_overlays(base_video: Path, subtitles: Path | None, motions: list[MotionSpec], assets_dir: Path, output: Path) -> Path:
    if not motions and subtitles is None:
        if base_video.resolve() != output.resolve():
            _run(["ffmpeg", "-y", "-i", str(base_video), "-c", "copy", str(output)])
        return output

    encoder = preferred_ffmpeg_encoder()
    command = ["ffmpeg", "-y", "-i", str(base_video)]
    filter_parts: list[str] = []
    current_label = "[0:v]"

    for index, motion in enumerate(motions, start=1):
        asset_path = assets_dir / f"{motion.id}.png"
        video_asset_path = _motion_video_asset_path(assets_dir, motion)
        duration = max(0.1, float(motion.duration))
        if video_asset_path.exists():
            command.extend(["-i", str(video_asset_path)])
        else:
            command.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(asset_path)])
        overlay_input = f"[{index}:v]"
        overlay_label = f"[v{index}]"
        enable = f"between(t,{motion.start:.3f},{(motion.start + duration):.3f})"
        x_expr, y_expr = _overlay_position_expressions(motion, duration)
        if video_asset_path.exists():
            shifted_label = f"[ov{index}]"
            prepared_parts, prepared_label = _figma_video_overlay_filter_parts(index, overlay_input, motion, duration)
            filter_parts.extend(prepared_parts)
            overlay_input = prepared_label
            filter_parts.append(f"{overlay_input}setpts=PTS+{motion.start:.3f}/TB{shifted_label}")
            overlay_input = shifted_label
        filter_parts.append(
            f"{current_label}{overlay_input}overlay=x='{x_expr}':y='{y_expr}':enable='{enable}'{overlay_label}"
        )
        current_label = overlay_label

    if subtitles is not None:
        subtitled_label = "[vsub]"
        filter_parts.append(f"{current_label}subtitles='{subtitles.name}'{subtitled_label}")
        current_label = subtitled_label

    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            current_label,
            "-map",
            "0:a?",
            *_video_encode_args(encoder),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
    )
    try:
        _run(command, cwd=output.parent)
    except subprocess.CalledProcessError:
        if encoder == "h264_nvenc":
            fallback = ["ffmpeg", "-y", "-i", str(base_video)]
            fallback_filter_parts: list[str] = []
            current_label = "[0:v]"
            for index, motion in enumerate(motions, start=1):
                asset_path = assets_dir / f"{motion.id}.png"
                video_asset_path = _motion_video_asset_path(assets_dir, motion)
                duration = max(0.1, float(motion.duration))
                if video_asset_path.exists():
                    fallback.extend(["-i", str(video_asset_path)])
                else:
                    fallback.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(asset_path)])
                overlay_input = f"[{index}:v]"
                overlay_label = f"[v{index}]"
                enable = f"between(t,{motion.start:.3f},{(motion.start + duration):.3f})"
                x_expr, y_expr = _overlay_position_expressions(motion, duration)
                if video_asset_path.exists():
                    shifted_label = f"[ov{index}]"
                    prepared_parts, prepared_label = _figma_video_overlay_filter_parts(index, overlay_input, motion, duration)
                    fallback_filter_parts.extend(prepared_parts)
                    overlay_input = prepared_label
                    fallback_filter_parts.append(f"{overlay_input}setpts=PTS+{motion.start:.3f}/TB{shifted_label}")
                    overlay_input = shifted_label
                fallback_filter_parts.append(
                    f"{current_label}{overlay_input}overlay=x='{x_expr}':y='{y_expr}':enable='{enable}'{overlay_label}"
                )
                current_label = overlay_label
            if subtitles is not None:
                subtitled_label = "[vsub]"
                fallback_filter_parts.append(f"{current_label}subtitles='{subtitles.name}'{subtitled_label}")
                current_label = subtitled_label
            fallback.extend(
                [
                    "-filter_complex",
                    ";".join(fallback_filter_parts),
                    "-map",
                    current_label,
                    "-map",
                    "0:a?",
                    *_video_encode_args("libx264"),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    str(output),
                ]
            )
            _run(fallback, cwd=output.parent)
        else:
            raise
    return output


def render_project_preview(project: ProjectState, project_root: Path) -> Path:
    source = project_root / project.source_video
    renders_dir = project_root / "renders"
    assets_dir = project_root / "assets"
    renders_dir.mkdir(parents=True, exist_ok=True)
    source_duration = detect_duration(source)
    raw_keep_ranges = [(item.start, item.end) for item in (project.edit_plan.keep_ranges if project.edit_plan else [])]
    keep_ranges = []
    for start, end in raw_keep_ranges:
        start = max(0.0, min(float(start), source_duration))
        end = max(0.0, min(float(end), source_duration))
        if end - start >= 0.05:
            keep_ranges.append((start, end))
    if not keep_ranges:
        keep_ranges = [(0.0, source_duration)]
    segments = extract_segments(source, keep_ranges, renders_dir)
    base = concat_segments(segments, renders_dir)
    subtitle_path = None
    subtitles_enabled = bool(getattr(project, "subtitles_enabled", False))
    if subtitles_enabled and project.edit_plan and project.transcript and project.edit_plan.subtitle_style != "none":
        subtitle_path = build_srt(project.transcript, keep_ranges, renders_dir / "preview.srt")
    canvas_width, canvas_height = detect_video_size(base)
    fitted_motions = [fit_motion_to_canvas(motion, canvas_width, canvas_height) for motion in project.motions]
    final_output = renders_dir / "preview.mp4"
    has_figma_assets = any(getattr(motion, "source_type", "generated") == "figma" for motion in fitted_motions)
    if fitted_motions and not has_figma_assets:
        try:
            duration = detect_duration(base)
            hyperframes_output = final_output if subtitle_path is None else renders_dir / "hyperframes_overlays.mp4"
            rendered = render_hyperframes_preview(
                base_video=base,
                motions=fitted_motions,
                output=hyperframes_output,
                work_dir=renders_dir / "hyperframes",
                width=canvas_width,
                height=canvas_height,
                duration=duration,
            )
            if subtitle_path is not None:
                return burn_subtitles(rendered, subtitle_path, final_output)
            return rendered
        except HyperframesRenderError:
            pass

    rendered_motions = []
    for motion_index, (source_motion, fitted_motion) in enumerate(zip(project.motions, fitted_motions)):
        is_figma_motion = getattr(source_motion, "source_type", "generated") == "figma"
        existing_video = _motion_video_asset_path(assets_dir, source_motion)
        existing_png = assets_dir / f"{source_motion.id}.png"
        if (
            is_figma_motion
            and existing_video.exists()
            and existing_png.exists()
            and _motion_video_matches_current_dsl(existing_video, source_motion, assets_dir)
        ):
            rendered_motions.append(fitted_motion)
            continue
        render_source = source_motion if is_figma_motion else fitted_motion
        asset_path = render_motion_asset(render_source, assets_dir)
        latest_video = _latest_motion_video_relpath(project_root, render_source.id)
        asset_updates = {
            "asset_version": str(asset_path.stat().st_mtime_ns),
            "asset_signature": motion_asset_signature(render_source),
            "video_asset_path": latest_video or fitted_motion.video_asset_path,
        }
        if is_figma_motion:
            project.motions[motion_index] = source_motion.model_copy(update=asset_updates)
        rendered_motions.append(
            fitted_motion.model_copy(update=asset_updates)
        )
    return apply_overlays(base, subtitle_path, rendered_motions, assets_dir, final_output)
