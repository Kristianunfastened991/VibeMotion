from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.routes import update_clip  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.models.schemas import ClipUpdateRequest, CutRange, EditPlan, ProjectState, TranscriptData  # noqa: E402
from app.services.projects import load_state, project_dir, save_state  # noqa: E402
from app.services.timeline import build_timeline  # noqa: E402


def _cleanup(project_id: str) -> None:
    base = project_dir(project_id).resolve()
    root = settings.projects_root.resolve()
    if base.name.startswith("qa-timeline-clip-edit-") and root in base.parents and base.exists():
        shutil.rmtree(base)


def _save(project_id: str, *, edit_plan: EditPlan | None = None) -> ProjectState:
    state = ProjectState(
        project_id=project_id,
        title=project_id,
        status="uploaded",
        source_video="input/source.mp4",
        transcript=TranscriptData(duration=20.0),
        edit_plan=edit_plan,
    )
    save_state(state)
    return state


def test_upload_only_trim_starts_at_zero() -> None:
    project_id = "qa-timeline-clip-edit-upload-only"
    _cleanup(project_id)
    try:
        _save(project_id)
        update_clip(project_id, "b01", ClipUpdateRequest(source_start=6.0, source_end=10.0))
        timeline = build_timeline(project_id, load_state(project_id), project_dir(project_id))
        clip = timeline["clips"][0]
        assert clip["start"] == 0.0, clip
        assert clip["source_start"] == 6.0, clip
        assert clip["source_end"] == 10.0, clip
        assert clip["duration"] == 4.0, clip
    finally:
        _cleanup(project_id)


def test_dragging_clip_to_start_reorders_edit_plan() -> None:
    project_id = "qa-timeline-clip-edit-reorder"
    _cleanup(project_id)
    try:
        edit_plan = EditPlan(
            summary="Manual timeline",
            strategy="Manual test",
            estimated_duration=9.0,
            keep_ranges=[
                CutRange(start=0.0, end=3.0, reason="a", handle_start=0.0, handle_end=20.0),
                CutRange(start=5.0, end=8.0, reason="b", handle_start=0.0, handle_end=20.0),
                CutRange(start=12.0, end=15.0, reason="c", handle_start=0.0, handle_end=20.0),
            ],
            subtitle_style="none",
        )
        _save(project_id, edit_plan=edit_plan)
        update_clip(project_id, "b03", ClipUpdateRequest(source_start=12.0, source_end=15.0, start=0.0))
        state = load_state(project_id)
        starts = [item.start for item in state.edit_plan.keep_ranges]
        assert starts == [12.0, 0.0, 5.0], starts
    finally:
        _cleanup(project_id)


if __name__ == "__main__":
    test_upload_only_trim_starts_at_zero()
    test_dragging_clip_to_start_reorders_edit_plan()
    print("timeline clip edit QA passed")
