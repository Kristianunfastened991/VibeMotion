from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import MotionSpec  # noqa: E402
from app.services.figma_plugin import motion_from_plugin_asset  # noqa: E402
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt  # noqa: E402
from app.services.motion import apply_animation_prompt, prompt_to_motion, render_motion_video_asset  # noqa: E402
from app.services.motion_intent import build_layer_motion_recipe_from_prompt, motion_recipe_actions  # noqa: E402
from app.services.style_presets import apply_style_to_motion, load_style_preset  # noqa: E402


STYLE_PRESET_ID = "pulse-1349370a"


GENERATED_CASES: list[dict[str, Any]] = [
    {
        "id": "ru-fade5-drop2",
        "prompt": "Фейд ин на протяжении пяти секунд. Фейд аут вконце 2 секунды и весь фрейм падает вниз.",
        "duration": 7.25,
        "enter_duration": 5.0,
        "exit_duration": 2.0,
        "enter_animation": "fade",
        "exit_animation": "drop",
        "exit_to": "bottom",
    },
    {
        "id": "en-fade5-exit2",
        "prompt": "Fade in at the beginning for 5 seconds. Fade out at the end for 2 seconds.",
        "duration": 7.25,
        "enter_duration": 5.0,
        "exit_duration": 2.0,
        "enter_animation": "fade",
        "exit_animation": "fade",
    },
    {
        "id": "ru-slide-left-hold",
        "prompt": "Слайд слева 2 секунды и дальше держать на месте без выхода.",
        "enter_animation": "slide",
        "enter_from": "left",
        "exit_animation": "none",
    },
    {
        "id": "en-black-label-text",
        "prompt": 'Black label at top right with the text "Hello". Fade in for 1 second and hold.',
        "text": "Hello",
        "enter_animation": "fade",
        "exit_animation": "none",
    },
]


FIGMA_CASES: list[dict[str, Any]] = [
    {
        "id": "white-bg-layers-fade-final-fade",
        "prompt": "First 1 second white background only. Then all layers fade in over 2 seconds. Final 1 second full-frame fade out.",
        "phases": {"intro": ("white-bg-fade", 1.0), "build": ("basic-layer-fade", 2.0), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "gradient-top-down-no-fly",
        "prompt": "First 1 second white background only. Then every layer appears top-to-bottom with a soft gradient fade-in over 2 seconds, no flying, no movement. Final 1 second full-frame fade out.",
        "phases": {"intro": ("white-bg-fade", 1.0), "build": ("gradient-fade-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "random-fly",
        "prompt": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds in random order. Final 0.5 seconds full-frame fade out.",
        "phases": {"intro": ("white-bg-fade", 0.5), "build": ("random-fly-in-stagger", 1.5), "outro": ("full-frame-fade-out", 0.5)},
    },
    {
        "id": "full-frame-drop",
        "prompt": "First 0.5 seconds white background only. Then all layers fly into place over 1.5 seconds. In the final 2 seconds the entire frame drops down as one whole object with gravity and fades out.",
        "phases": {"intro": ("white-bg-fade", 0.5), "build": ("random-fly-in-stagger", 1.5), "outro": ("full-frame-drop", 2.0)},
    },
    {
        "id": "camera-push-only",
        "prompt": "Add a slow whole-frame camera push in over 3 seconds. Keep all layers exactly as designed, do not animate separate layers.",
        "phases": {"camera": ("camera-push", 8.0)},
    },
]


def fail(message: str) -> None:
    raise AssertionError(message)


def assert_close(actual: float, expected: float, label: str, tolerance: float = 0.06) -> None:
    if abs(float(actual) - float(expected)) > tolerance:
        fail(f"{label}: expected {expected}, got {actual}")


def base_motion() -> MotionSpec:
    return MotionSpec(
        id="qa-generated",
        text="QA",
        start=0.0,
        duration=4.0,
        x=80,
        y=120,
        width=520,
        height=160,
        design_preset="creator-vibe",
        kind="glass-card",
    )


def check_generated_prompts(report: dict[str, Any]) -> None:
    results: list[dict[str, Any]] = []
    for case in GENERATED_CASES:
        prompt = str(case["prompt"])
        parsed = prompt_to_motion(prompt, 4.0, "creator-vibe")
        applied = apply_animation_prompt(base_motion(), prompt)
        for spec_name, spec in [("prompt_to_motion", parsed), ("apply_animation_prompt", applied)]:
            for key, expected in case.items():
                if key in {"id", "prompt"}:
                    continue
                if key == "text" and spec_name != "prompt_to_motion":
                    continue
                actual = getattr(spec, key)
                if isinstance(expected, float):
                    assert_close(float(actual), expected, f"{case['id']}:{spec_name}:{key}")
                elif actual != expected:
                    fail(f"{case['id']}:{spec_name}:{key}: expected {expected!r}, got {actual!r}")
        results.append(
            {
                "id": case["id"],
                "prompt_to_motion": parsed.model_dump(mode="json"),
                "apply_animation_prompt": applied.model_dump(mode="json"),
            }
        )
    report["generated_prompt_cases"] = results


def load_figma_asset_ids(limit: int) -> list[str]:
    index_path = ROOT / "app" / "static" / "assets" / "figma-plugin" / "assets.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    asset_ids: list[str] = []
    for item in data:
        if item.get("kind") == "composition" and item.get("asset_file") and int(item.get("children_count") or 0) >= 3:
            asset_ids.append(str(item["id"]))
        if len(asset_ids) >= limit:
            break
    if not asset_ids:
        fail("no usable Figma plugin assets found")
    return asset_ids


def phase_by_id(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(phase.get("id")): dict(phase) for phase in list(plan.get("phases") or []) if isinstance(phase, dict)}


def layer_text_signature(layers: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    signature: list[tuple[str, str, str]] = []
    for layer in layers:
        text = str(layer.get("text") or "")
        if text:
            signature.append((str(layer.get("id") or ""), str(layer.get("name") or ""), text))
    return signature


def check_figma_plans(out_dir: Path, report: dict[str, Any], asset_limit: int, render_limit: int) -> None:
    asset_ids = load_figma_asset_ids(asset_limit)
    plan_results: list[dict[str, Any]] = []
    render_results: list[dict[str, Any]] = []
    renders_done = 0
    for asset_index, asset_id in enumerate(asset_ids):
        asset_root = out_dir / "figma-assets" / asset_id
        source_motion = motion_from_plugin_asset(asset_root, asset_id, 0.0, 8.0)
        source_texts = layer_text_signature(source_motion.figma_layers)
        for case_index, case in enumerate(FIGMA_CASES):
            prompt = str(case["prompt"])
            if not should_use_frame_choreography_prompt(prompt) and case["id"] != "camera-push-only":
                fail(f"{case['id']}: prompt did not route to frame choreography")
            layers = plan_frame_choreography(prompt, source_motion)
            plan = describe_motion_plan(layers) or {}
            phases = phase_by_id(plan)
            for phase_id, (expected_preset, expected_duration) in dict(case["phases"]).items():
                phase = phases.get(phase_id)
                if not phase:
                    fail(f"{case['id']}:{asset_id}: missing phase {phase_id}")
                actual_preset = str(phase.get("preset") or "")
                if actual_preset != expected_preset:
                    fail(f"{case['id']}:{asset_id}:{phase_id}: expected preset {expected_preset}, got {actual_preset}")
                assert_close(float(phase.get("duration") or 0), float(expected_duration), f"{case['id']}:{asset_id}:{phase_id}.duration")
            if layer_text_signature(layers) != source_texts:
                fail(f"{case['id']}:{asset_id}: Figma text changed during motion planning")
            plan_results.append({"asset_id": asset_id, "case": case["id"], "plan": plan})
            if renders_done < render_limit and case_index in {0, 1, 3}:
                spec = source_motion.model_copy(
                    update={
                        "duration": float(plan.get("duration") or 8.0),
                        "figma_layers": layers,
                        "motion_plan": plan,
                    }
                )
                video = render_motion_video_asset(spec, out_dir / "renders" / asset_id / case["id"] / "assets", fps=12)
                if not video or not video.exists():
                    fail(f"{case['id']}:{asset_id}: render did not create a video")
                visual_json = video.with_name(f"{video.stem}.visual-self-check.json")
                visual_report = json.loads(visual_json.read_text(encoding="utf-8")) if visual_json.exists() else {}
                if visual_report.get("status") not in {"pass", None}:
                    fail(f"{case['id']}:{asset_id}: visual self-check failed: {visual_report.get('errors')}")
                frames = sample_video_frames(video, sample_times_for_plan(plan, float(spec.duration)), out_dir / "frames" / asset_id / case["id"])
                if frames:
                    create_contact_sheet(frames, out_dir / "frames" / asset_id / f"{case['id']}-contact.jpg")
                render_results.append({"asset_id": asset_id, "case": case["id"], "video": str(video), "frames": [str(item) for item in frames]})
                renders_done += 1
    report["figma_plan_cases"] = plan_results
    report["figma_render_cases"] = render_results


def sample_video_frames(video: Path, times: list[float], target_dir: Path) -> list[Path]:
    if not shutil.which("ffmpeg"):
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, time_value in enumerate(times):
        target = target_dir / f"sample-{index:02d}-{time_value:.2f}s.jpg"
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{time_value:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0 and target.exists():
            if index > 0:
                check_nonblank_image(target)
            frames.append(target)
    return frames


def sample_times_for_plan(plan: dict[str, Any], duration: float) -> list[float]:
    phases = phase_by_id(plan)
    build = phases.get("build") or phases.get("camera") or {}
    outro = phases.get("outro") or {}
    build_start = float(build.get("start") or 0.0)
    build_duration = max(0.05, float(build.get("duration") or duration or 1.0))
    build_mid = min(max(0.1, duration - 0.1), build_start + build_duration * 0.65)
    if outro:
        outro_start = float(outro.get("start") or max(0.1, duration - 0.5))
        hold_time = max(0.1, min(duration - 0.1, outro_start - 0.15))
    else:
        hold_time = max(0.1, min(duration - 0.1, build_start + build_duration + 0.2))
    return [0.1, build_mid, hold_time]


def check_nonblank_image(path: Path) -> None:
    with Image.open(path).convert("RGB") as image:
        stat = ImageStat.Stat(image)
        extrema = ImageStat.Stat(ImageChops.difference(image, Image.new("RGB", image.size, tuple(int(v) for v in stat.mean)))).extrema
        spread = max(channel[1] for channel in extrema)
        if spread < 3:
            fail(f"rendered frame looks blank: {path}")


def create_contact_sheet(frames: list[Path], target: Path) -> None:
    images = [Image.open(path).convert("RGB") for path in frames]
    if not images:
        return
    thumb_w = 360
    thumb_h = int(images[0].height * (thumb_w / images[0].width))
    sheet = Image.new("RGB", (thumb_w * len(images), thumb_h + 26), "white")
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(images):
        thumb = image.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x = index * thumb_w
        sheet.paste(thumb, (x, 0))
        draw.text((x + 8, thumb_h + 7), frames[index].stem, fill=(20, 20, 20))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=92)


def check_add_mode(report: dict[str, Any], out_dir: Path) -> None:
    motion = motion_from_plugin_asset(out_dir / "add-mode", "12-159", 0.0, 8.0)
    target = next((layer for layer in motion.figma_layers if str(layer.get("kind") or "") in {"shape", "text", "image"}), None)
    if target is None:
        fail("add-mode: no target Figma layer")
    first = build_layer_motion_recipe_from_prompt("fade in for 1 second", "replace", target, motion.figma_layers, motion.duration)
    target_with_motion = dict(target)
    target_with_motion["motion_recipe"] = first
    second = build_layer_motion_recipe_from_prompt(
        "fade out at the end for 1 second",
        "append",
        target_with_motion,
        motion.figma_layers,
        motion.duration,
    )
    first_actions = motion_recipe_actions(first)
    second_actions = motion_recipe_actions(second)
    if len(first_actions) != 1:
        fail(f"add-mode: expected one initial action, got {len(first_actions)}")
    if len(second_actions) != 2:
        fail(f"add-mode: Add must append a second action, got {len(second_actions)}")
    if [action.get("preset") for action in second_actions] != ["fade-in", "fade-out"]:
        fail(f"add-mode: unexpected action presets: {[action.get('preset') for action in second_actions]}")
    report["add_mode"] = {
        "target_layer": {"id": target.get("id"), "name": target.get("name"), "kind": target.get("kind")},
        "actions_after_add": [action.get("preset") for action in second_actions],
    }


def check_style_memory(report: dict[str, Any]) -> None:
    style = load_style_preset(STYLE_PRESET_ID)
    if not style:
        fail(f"style preset missing: {STYLE_PRESET_ID}")
    if style.get("style_family") != "editorial-grid":
        fail(f"style preset {STYLE_PRESET_ID}: expected editorial-grid, got {style.get('style_family')!r}")
    tokens = dict(style.get("tokens") or {})
    accent_palette = list(tokens.get("accent_palette") or [])
    if len(accent_palette) < 2:
        fail(f"style preset {STYLE_PRESET_ID}: accent palette is too weak")
    styled = apply_style_to_motion(base_motion().model_copy(update={"height": 220}), style)
    if styled.design_preset != "data-panel":
        fail(f"style preset {STYLE_PRESET_ID}: expected data-panel mapping, got {styled.design_preset}")
    report["style_memory"] = {"preset_id": STYLE_PRESET_ID, "style_family": style.get("style_family"), "accent_palette": accent_palette[:6]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-limit", type=int, default=10)
    parser.add_argument("--render-limit", type=int, default=6)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "qa_artifacts" / f"vibemotion-minimal-regression-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"status": "running", "artifact_dir": str(out_dir), "checks": []}

    check_generated_prompts(report)
    report["checks"].append("generated-prompt-parser")
    check_style_memory(report)
    report["checks"].append("style-memory")
    check_add_mode(report, out_dir)
    report["checks"].append("add-mode-appends")
    check_figma_plans(out_dir, report, max(1, int(args.asset_limit)), max(0, int(args.render_limit)))
    report["checks"].append("figma-frame-plans-and-renders")
    report["status"] = "pass"

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "pass", "report": str(report_path), "artifact_dir": str(out_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
