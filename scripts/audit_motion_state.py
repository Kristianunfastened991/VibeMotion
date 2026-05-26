#!/usr/bin/env python3
"""Audit active VibeMotion motion state for render/preview regressions."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def rel_exists(project_dir: Path, raw: Any) -> bool:
    if not raw:
        return True
    path = Path(str(raw))
    return path.exists() if path.is_absolute() else (project_dir / path).exists()


def motion_has_recipe(motion: dict[str, Any]) -> bool:
    return any(
        isinstance(layer, dict) and isinstance(layer.get("motion_recipe"), dict)
        for layer in list(motion.get("figma_layers") or [])
    )


def motion_identity_issues(motion: dict[str, Any], prefix: str = "") -> list[str]:
    issues: list[str] = []
    mid = str(motion.get("id") or "<unknown>")
    label = f"{prefix}{mid}"
    layers = [layer for layer in list(motion.get("figma_layers") or []) if isinstance(layer, dict)]
    layer_ids = [str(layer.get("id") or "") for layer in layers if layer.get("id")]
    for layer_id, count in Counter(layer_ids).items():
        if count > 1:
            issues.append(f"{label}: duplicate figma layer id {layer_id}: {count}")
    recipe_ids = [
        str(layer["motion_recipe"].get("id") or "")
        for layer in layers
        if isinstance(layer.get("motion_recipe"), dict) and layer["motion_recipe"].get("id")
    ]
    for recipe_id, count in Counter(recipe_ids).items():
        if count > 1:
            issues.append(f"{label}: duplicate motion recipe/action id {recipe_id}: {count}")
    return issues


def audit_project(path: Path) -> dict[str, Any]:
    state = load_json(path)
    project_dir = path.parent
    issues: list[str] = []
    checks: list[str] = []

    motions = list(state.get("motions") or [])
    checks.append(f"active motions: {len(motions)}")
    preview = state.get("outputs", {}).get("preview")
    if preview and not rel_exists(project_dir, preview):
        issues.append(f"preview output missing: {preview}")
    elif preview:
        checks.append(f"preview output exists: {preview}")

    for motion in motions:
        mid = str(motion.get("id") or "<unknown>")
        source_type = motion.get("source_type")
        issues.extend(motion_identity_issues(motion))
        video_asset = motion.get("video_asset_path")
        if video_asset and not rel_exists(project_dir, video_asset):
            issues.append(f"{mid}: active video_asset_path missing: {video_asset}")
        if source_type == "figma" and motion_has_recipe(motion):
            plan = motion.get("motion_plan")
            if not isinstance(plan, dict):
                issues.append(f"{mid}: missing top-level motion_plan")
            else:
                phases = list(plan.get("phases") or [])
                if plan.get("time_mode") != "absolute":
                    issues.append(f"{mid}: motion_plan time_mode is not absolute")
                if not phases:
                    issues.append(f"{mid}: motion_plan has no phases")
                else:
                    checks.append(f"{mid}: motion_plan phases={','.join(str(p.get('id')) for p in phases)}")
            units = list(motion.get("motion_units") or [])
            if not units:
                issues.append(f"{mid}: missing motion_units")
            else:
                checks.append(f"{mid}: motion_units={len(units)}")
            for layer in list(motion.get("figma_layers") or []):
                if not isinstance(layer, dict):
                    continue
                lid = str(layer.get("id") or "")
                if layer.get("mask_role") and layer.get("motion_recipe"):
                    issues.append(f"{mid}: visual mask has motion_recipe: {lid}")
                if lid.startswith("__frame_choreo_shard_") and not layer.get("choreo_static_skip"):
                    issues.append(f"{mid}: shard layer is not marked static-skip: {lid}")

    stale_history_assets = 0
    for stack_name in ("undo_stack", "redo_stack"):
        for snapshot_index, snapshot in enumerate(list(state.get(stack_name) or [])):
            for motion in list(snapshot.get("motions") or []):
                if not isinstance(motion, dict):
                    continue
                issues.extend(motion_identity_issues(motion, prefix=f"{stack_name}[{snapshot_index}]/"))
                raw = motion.get("video_asset_path")
                if raw and not rel_exists(project_dir, raw):
                    stale_history_assets += 1
    if stale_history_assets:
        issues.append(f"history contains stale video_asset_path entries: {stale_history_assets}")
    else:
        checks.append("history has no stale video_asset_path entries")

    return {
        "project": project_dir.name,
        "path": str(path),
        "status": state.get("status"),
        "issues": issues,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Project id, project directory, or project.json path")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="VibeMotion root")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    root = Path(args.root)
    raw_project = Path(args.project)
    if raw_project.is_file():
        path = raw_project
    elif raw_project.is_dir():
        path = raw_project / "project.json"
    else:
        path = root / "projects" / args.project / "project.json"
    if not path.exists():
        raise SystemExit(f"project not found: {path}")

    report = audit_project(path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1 if report["issues"] else 0)

    print(f"[{report['project']}] {report['status']}")
    if report["checks"]:
        print("checks:")
        for item in report["checks"]:
            print(f"  - {item}")
    if report["issues"]:
        print("issues:")
        for item in report["issues"]:
            print(f"  - {item}")
        raise SystemExit(1)
    print("issues: none")


if __name__ == "__main__":
    main()
