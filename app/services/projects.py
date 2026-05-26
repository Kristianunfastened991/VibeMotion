from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from app.core.config import settings
from app.models.schemas import ProjectState


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "project"


def project_dir(project_id: str) -> Path:
    return settings.projects_root / project_id


def state_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def ensure_project_layout(base: Path) -> None:
    for child in [
        "input",
        "transcripts",
        "renders",
        "assets",
        "analysis",
    ]:
        (base / child).mkdir(parents=True, exist_ok=True)


def _hydrate_motion_asset_versions(state: ProjectState) -> ProjectState:
    updated = False
    base = project_dir(state.project_id)
    motions = []
    for motion in state.motions:
        asset_version = getattr(motion, "asset_version", None)
        asset_path = getattr(motion, "asset_path", None)
        updates = {}
        if not asset_version and asset_path:
            path = base / asset_path
            if path.exists():
                updates["asset_version"] = str(path.stat().st_mtime_ns)
        if not getattr(motion, "video_asset_path", None):
            assets_dir = base / "assets"
            versioned = sorted(assets_dir.glob(f"{motion.id}-*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            if versioned:
                updates["video_asset_path"] = str(versioned[0].relative_to(base))
            else:
                legacy = assets_dir / f"{motion.id}.mp4"
                if legacy.exists():
                    updates["video_asset_path"] = str(legacy.relative_to(base))
        if updates:
            motions.append(motion.model_copy(update=updates))
            updated = True
            continue
        motions.append(motion)
    if not updated:
        return state
    return state.model_copy(update={"motions": motions})


def clear_projects(keep_project_id: str | None = None) -> None:
    root = settings.projects_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.name == ".gitkeep":
            continue
        if keep_project_id and child.name == keep_project_id:
            continue
        resolved = child.resolve()
        if root not in resolved.parents:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def create_project(title: str) -> ProjectState:
    project_id = f"{slugify(title)}-{uuid.uuid4().hex[:8]}"
    base = project_dir(project_id)
    ensure_project_layout(base)
    state = ProjectState(project_id=project_id, title=title, status="created")
    save_state(state)
    return state


def list_projects() -> list[ProjectState]:
    states: list[ProjectState] = []
    for child in settings.projects_root.iterdir():
        if not child.is_dir():
            continue
        path = child / "project.json"
        if not path.exists():
            continue
        try:
            state = ProjectState.model_validate_json(path.read_text(encoding="utf-8-sig"))
            states.append(_hydrate_motion_asset_versions(state))
        except Exception:
            continue
    return sorted(states, key=lambda item: item.project_id, reverse=True)


def load_state(project_id: str) -> ProjectState:
    path = state_path(project_id)
    if not path.exists():
        raise FileNotFoundError(project_id)
    state = ProjectState.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return _hydrate_motion_asset_versions(state)


def save_state(state: ProjectState) -> None:
    base = project_dir(state.project_id)
    ensure_project_layout(base)
    state_path(state.project_id).write_text(
        json.dumps(state.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )


def add_note(state: ProjectState, message: str) -> ProjectState:
    state.notes.append(message)
    return state
