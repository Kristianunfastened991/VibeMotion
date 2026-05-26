from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import CutRange, EditPlan, ProjectState
from app.services.figma_plugin import motion_from_plugin_asset
from app.services.layer_motion import frame_choreography_required_duration, plan_frame_choreography
from app.services.media import detect_video_size
from app.services.motion import fit_motion_to_canvas, render_motion_asset
from app.services.projects import add_note, create_project, project_dir, save_state
from app.services.render import render_project_preview


DEFAULT_PROMPT = (
    "The first 2 seconds: a white screen fades in at the start, just the background, "
    "with no other elements. Then, over 3 seconds, all elements fly into the frame "
    "and settle into their places in random order. At the end, the entire frame "
    "shatters like broken glass over the last 3 seconds and fades out."
)


def latest_motion_video(project_root: Path, motion_id: str) -> str | None:
    assets_dir = project_root / "assets"
    videos = sorted(assets_dir.glob(f"{motion_id}-*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if videos:
        return str(videos[0].relative_to(project_root))
    legacy = assets_dir / f"{motion_id}.mp4"
    return str(legacy.relative_to(project_root)) if legacy.exists() else None


def build_qa_project(source_project: str, asset_ids: list[str], prompt: str, title: str) -> dict:
    source_root = project_dir(source_project)
    source_video = source_root / "input" / "source.mp4"
    if not source_video.exists():
        raise FileNotFoundError(f"source video not found: {source_video}")

    state = create_project(title)
    qa_root = project_dir(state.project_id)
    target_video = qa_root / "input" / "source.mp4"
    shutil.copy2(source_video, target_video)
    state.source_video = str(target_video.relative_to(qa_root))
    state.status = "uploaded"

    canvas_width, canvas_height = detect_video_size(target_video)
    required_duration = frame_choreography_required_duration(prompt, 0)
    starts = [2.0 + index * (required_duration + 2.0) for index in range(len(asset_ids))]
    motions = []

    for asset_id, start in zip(asset_ids, starts):
        motion = motion_from_plugin_asset(
            project_root=qa_root,
            asset_id=asset_id,
            start=start,
            duration=required_duration,
        )
        motion = fit_motion_to_canvas(motion, canvas_width, canvas_height)
        motion = motion.model_copy(
            update={
                "duration": max(float(motion.duration or 0), required_duration),
                "figma_layers": plan_frame_choreography(prompt, motion),
                "enter_animation": "none",
                "exit_animation": "none",
                "enter_from": "center",
                "exit_to": "center",
                "prompt": f"Frame choreography prompt: {prompt}",
            }
        )
        asset_path = render_motion_asset(motion, qa_root / "assets")
        motion = motion.model_copy(
            update={
                "asset_version": str(asset_path.stat().st_mtime_ns),
                "video_asset_path": latest_motion_video(qa_root, motion.id),
            }
        )
        motions.append(motion)

    state.motions = motions
    preview_duration = max((motion.start + motion.duration for motion in motions), default=0.0) + 2.0
    state.edit_plan = EditPlan(
        summary="Motion choreography QA",
        strategy="Short source segment for motion preview/render parity checks.",
        estimated_duration=round(preview_duration, 3),
        keep_ranges=[
            CutRange(
                start=0.0,
                end=round(preview_duration, 3),
                reason="QA render window",
                source="source",
                handle_start=0.0,
                handle_end=round(preview_duration, 3),
            )
        ],
        subtitle_style="none",
    )
    add_note(state, f"Motion choreography QA project for {len(motions)} Figma frames.")
    save_state(state)

    preview = render_project_preview(state, qa_root)
    state = ProjectState.model_validate(json.loads((qa_root / "project.json").read_text(encoding="utf-8")))
    state.outputs["preview"] = str(preview.relative_to(qa_root))
    state.status = "preview-rendered"
    add_note(state, "Motion choreography QA preview rendered.")
    save_state(state)

    return {
        "project_id": state.project_id,
        "project_root": str(qa_root),
        "preview": str(preview),
        "motions": [
            {
                "id": motion.id,
                "figma_node_id": motion.figma_node_id,
                "figma_node_name": motion.figma_node_name,
                "start": motion.start,
                "duration": motion.duration,
                "video": str(qa_root / str(motion.video_asset_path or "")),
                "phase_plan": next(
                    (
                        layer.get("motion_recipe", {}).get("phase_plan")
                        for layer in motion.figma_layers
                        if layer.get("motion_recipe", {}).get("phase_plan")
                    ),
                    None,
                ),
            }
            for motion in motions
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a 3-frame VibeMotion motion choreography QA project.")
    parser.add_argument("--source-project", default="test-b5b1e836")
    parser.add_argument("--assets", nargs="+", default=["12-159", "12-247", "119-43"])
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--title", default="motion-choreography-qa")
    args = parser.parse_args()
    print(json.dumps(build_qa_project(args.source_project, args.assets, args.prompt, args.title), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
