from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import ProjectState  # noqa: E402
from app.services.layer_motion import (  # noqa: E402
    describe_motion_plan,
    describe_motion_units,
    plan_frame_choreography,
    should_use_frame_choreography_prompt,
)
from app.services.motion import render_motion_video_asset  # noqa: E402
from app.services.motion_intent import attach_frame_motion_contract  # noqa: E402


DEFAULT_PROJECT = "testmotion1-4892642e"


def _latest_project_dir() -> Path:
    candidates = sorted(
        (ROOT / "projects").glob("*/project.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise AssertionError("no project.json files found under projects")
    return candidates[0].parent


def _required_effects_for_prompt(prompt: str) -> set[str]:
    text = prompt.casefold()
    required: set[str] = set()
    if "white background" in text or "бел" in text:
        required.add("white-bg-fade")
    if ("gradient" in text or "градиент" in text or "графдиент" in text) and ("fade" in text or "фейд" in text):
        required.add("gradient-fade-stagger")
    if "signal scan" in text or "clean signal" in text or "цифровой скан" in text:
        required.add("signal-scan-reveal")
    if "tetris" in text or "тетрис" in text:
        required.add("tetris-build")
    if "text slide" in text or "lines from bottom" in text or "текст снизу" in text:
        required.add("text-slide-up-lines")
    if "fade up lines" in text or "line by line" in text or "строк" in text:
        required.add("fade-up-lines")
    if "whole frame falls" in text or "entire frame drops" in text or "падает целиком" in text:
        required.add("full-frame-drop")
    if "shatter" in text or "broken glass" in text or "оскол" in text or "разбив" in text:
        required.add("full-frame-shatter")
    if "fade out" in text or "фейдаут" in text or "фейд аут" in text:
        required.add("full-frame-fade-out")
    if not required:
        raise AssertionError("strict prompt does not contain recognized motion requirements")
    return required


def _strip_stored_prompt(prompt: str) -> str:
    prefix = "Frame choreography prompt:"
    if prompt.startswith(prefix):
        return prompt.split(":", 1)[1].strip()
    return prompt.strip()


def _find_motion(state: ProjectState, motion_id: str | None):
    motions = list(state.motions or [])
    if motion_id:
        for motion in motions:
            if motion.id == motion_id:
                return motion
        raise AssertionError(f"motion not found: {motion_id}")
    for motion in motions:
        prompt = _strip_stored_prompt(str(getattr(motion, "prompt", "") or ""))
        if getattr(motion, "source_type", "") == "figma" and getattr(motion, "figma_layers", None) and prompt:
            return motion
    raise AssertionError("no prompted figma motion found")


def _phase_effects(plan: dict[str, Any]) -> set[str]:
    effects: set[str] = set()
    for phase in list(plan.get("phases") or []):
        if isinstance(phase, dict) and phase.get("preset"):
            effects.add(str(phase.get("preset")))
        for subphase in list((phase or {}).get("subphases") or []):
            if isinstance(subphase, dict) and subphase.get("preset"):
                effects.add(str(subphase.get("preset")))
    for step in list(((plan.get("prompt_scenario") or {}).get("steps")) or []):
        if isinstance(step, dict) and step.get("effect"):
            effects.add(str(step.get("effect")))
    return effects


def _copy_source_asset(source_project_dir: Path, out_dir: Path, motion: Any) -> None:
    asset_path = Path(str(getattr(motion, "asset_path", "") or ""))
    if not asset_path:
        return
    source = source_project_dir / asset_path
    target = out_dir / asset_path
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _extract_frame(video: Path, time_value: float, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-ss",
            f"{time_value:.3f}",
            "-frames:v",
            "1",
            str(target),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0 and target.exists()


def _contact_sheet(frame_paths: list[Path], labels: list[str], target: Path) -> None:
    images = [Image.open(path).convert("RGB") for path in frame_paths if path.exists()]
    if not images:
        return
    thumb_w = 320
    thumb_h = max(1, int(images[0].height * (thumb_w / images[0].width)))
    sheet = Image.new("RGB", (thumb_w * len(images), thumb_h + 28), "white")
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(images):
        image = image.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x = index * thumb_w
        sheet.paste(image, (x, 0))
        draw.text((x + 8, thumb_h + 8), labels[index], fill=(20, 20, 20))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--motion-id", default=None)
    parser.add_argument("--fps", type=int, default=12)
    args = parser.parse_args()

    source_project_dir = ROOT / "projects" / args.project if args.project else _latest_project_dir()
    project_json = source_project_dir / "project.json"
    if not project_json.exists():
        source_project_dir = _latest_project_dir()
        project_json = source_project_dir / "project.json"
    state = ProjectState.model_validate_json(project_json.read_text(encoding="utf-8"))
    motion = _find_motion(state, args.motion_id)
    prompt = _strip_stored_prompt(str(motion.prompt or ""))
    if not should_use_frame_choreography_prompt(prompt):
        raise AssertionError("strict prompt did not route to frame choreography")

    layers = plan_frame_choreography(prompt, motion)
    plan = describe_motion_plan(layers) or {}
    layers = attach_frame_motion_contract(prompt, motion.id, layers, plan)
    plan = describe_motion_plan(layers) or plan
    effects = _phase_effects(plan)
    required_effects = _required_effects_for_prompt(prompt)
    missing = sorted(required_effects - effects)
    if missing:
        raise AssertionError(f"missing required effects: {', '.join(missing)}")

    gate = plan.get("qa_gate") if isinstance(plan.get("qa_gate"), dict) else {}
    if gate.get("status") == "fail":
        raise AssertionError(f"qa gate failed: {gate.get('errors')}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "qa_artifacts" / f"strict-user-prompt-{stamp}"
    _copy_source_asset(source_project_dir, out_dir, motion)
    spec = motion.model_copy(
        update={
            "duration": float(plan.get("duration") or motion.duration or 1.0),
            "figma_layers": layers,
            "motion_plan": plan,
            "motion_units": describe_motion_units(layers),
        }
    )
    video = render_motion_video_asset(spec, out_dir / "assets", fps=max(8, int(args.fps)))
    if not video or not video.exists():
        raise AssertionError("strict prompt render did not create a video")

    report_path = video.with_name(f"{video.stem}.visual-self-check.json")
    visual_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    if visual_report.get("status") != "pass":
        raise AssertionError(f"visual self-check failed: {visual_report.get('errors')}")

    phases = {str(phase.get("id")): phase for phase in list(plan.get("phases") or []) if isinstance(phase, dict)}
    hold = phases.get("hold") or {}
    outro = phases.get("outro") or {}
    outro_start = float(outro.get("start") or max(0.0, float(spec.duration) - 2.0))
    outro_duration = float(outro.get("duration") or 2.0)
    samples = [
        ("intro-signal-scan", 0.9),
        ("build-tetris-text", 3.6),
        ("exact-hold", float(hold.get("start") or 5.0) + 1.0),
        ("full-frame-drop", min(float(spec.duration) - 0.08, outro_start + outro_duration * 0.9)),
    ]
    frames: list[Path] = []
    labels: list[str] = []
    for label, time_value in samples:
        frame_path = out_dir / "frames" / f"{label}.png"
        if _extract_frame(video, time_value, frame_path):
            frames.append(frame_path)
            labels.append(f"{label} {time_value:.1f}s")
    sheet = out_dir / "strict-user-prompt-contact-sheet.png"
    _contact_sheet(frames, labels, sheet)

    summary = {
        "status": "pass",
        "project": source_project_dir.name,
        "motion_id": motion.id,
        "video": str(video),
        "visual_report": str(report_path),
        "contact_sheet": str(sheet) if sheet.exists() else None,
        "effects": sorted(effects),
        "required_effects": sorted(required_effects),
        "prompt_execution": (visual_report.get("prompt_execution_audit") or {}),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
