from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.figma_plugin import motion_from_plugin_asset
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.media import detect_video_size
from app.services.motion import fit_motion_to_canvas, render_motion_asset
from app.services.motion_intent import attach_frame_motion_contract
from app.services.render import apply_overlays


BASE_VIDEO = ROOT / "projects" / "test-b5b1e836" / "input" / "source.mp4"
CASES = [
    {
        "asset_id": "123-9785",
        "name": "camera_push",
        "prompt": "whole frame camera push in, slow zoom in for 3 seconds",
        "identity_time": 0.0,
        "motion_time": 2.9,
        "expect": "start_identity",
    },
    {
        "asset_id": "12-159",
        "name": "camera_pull",
        "prompt": "whole frame camera pull back, zoom out for 3 seconds",
        "identity_time": 2.9,
        "motion_time": 0.0,
        "expect": "end_identity",
    },
    {
        "asset_id": "136-242",
        "name": "camera_pan_right",
        "prompt": "pan the whole frame to the right with a real scene camera for 3 seconds",
        "identity_time": None,
        "motion_time": 1.5,
        "expect": "pan_no_identity",
    },
]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def relpath(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def safe_time(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def font(size: int) -> ImageFont.ImageFont:
    for candidate in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def extract_frame(video: Path, time_value: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-ss", f"{time_value:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])


def mean_rgb_diff(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def border_luma(image: Image.Image) -> float:
    rgb = image.convert("RGB")
    w, h = rgb.size
    strips = [
        rgb.crop((0, 0, w, min(6, h))),
        rgb.crop((0, max(0, h - 6), w, h)),
        rgb.crop((0, 0, min(6, w), h)),
        rgb.crop((max(0, w - 6), 0, w, h)),
    ]
    means = []
    for strip in strips:
        gray = strip.convert("L")
        means.append(float(ImageStat.Stat(gray).mean[0]))
    return min(means)


def build_diff(a: Image.Image, b: Image.Image, output: Path) -> None:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    diff = diff.point(lambda value: min(255, value * 6))
    diff.save(output)


def build_contact_sheet(rows: list[dict[str, Any]], output: Path) -> None:
    columns = ["source", "start", "mid", "end", "diff"]
    cell_w, cell_h, label_h = 280, 160, 30
    sheet = Image.new("RGB", (cell_w * len(columns), (cell_h + label_h) * len(rows)), (242, 242, 242))
    draw = ImageDraw.Draw(sheet)
    label_font = font(14)
    for row_index, row in enumerate(rows):
        y = row_index * (cell_h + label_h)
        for col_index, key in enumerate(columns):
            x = col_index * cell_w
            image_path = Path(row["images"][key])
            image = Image.open(image_path).convert("RGB")
            image.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            bg = Image.new("RGB", (cell_w, cell_h), (18, 18, 18))
            bg.paste(image, ((cell_w - image.width) // 2, (cell_h - image.height) // 2))
            sheet.paste(bg, (x, y + label_h))
            draw.text((x + 6, y + 7), f"{row['name']} {key}", fill=(0, 0, 0), font=label_font)
    sheet.save(output)


def assert_scene_camera_plan(plan: dict[str, Any], planned_layers: list[dict[str, Any]], case: dict[str, Any]) -> None:
    camera = plan.get("camera") if isinstance(plan.get("camera"), dict) else None
    if not camera:
        raise AssertionError(f"{case['name']}: missing scene camera plan")
    keyframes = ((camera.get("motion_dsl") or {}).get("keyframes") or [])
    if len(keyframes) < 2:
        raise AssertionError(f"{case['name']}: scene camera has no useful keyframes")
    if camera.get("scope") != "post-composite":
        raise AssertionError(f"{case['name']}: scene camera must run after the frame is composed")
    controllers = [
        layer
        for layer in planned_layers
        if str(layer.get("id") or "").startswith("__frame_choreo_camera_")
    ]
    if len(controllers) != 1:
        raise AssertionError(f"{case['name']}: expected one transparent camera controller, got {len(controllers)}")
    visible_moving_layers = [
        layer
        for layer in planned_layers
        if not str(layer.get("id") or "").startswith("__frame_choreo_camera_")
        and layer.get("motion_recipe")
        and layer.get("visible") is not False
    ]
    if visible_moving_layers:
        names = ", ".join(str(layer.get("name") or layer.get("id")) for layer in visible_moving_layers[:5])
        raise AssertionError(f"{case['name']}: camera-only prompt animated visible layers: {names}")


def render_case(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    work_dir = root / "work" / case["name"]
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    source_motion = motion_from_plugin_asset(work_dir, case["asset_id"], start=0, duration=3.0)
    if not should_use_frame_choreography_prompt(case["prompt"]):
        raise AssertionError(f"{case['name']}: prompt did not route to whole-frame motion")

    planned_layers = plan_frame_choreography(case["prompt"], source_motion)
    plan = describe_motion_plan(planned_layers) or {}
    planned_layers = attach_frame_motion_contract(case["prompt"], str(source_motion.id), planned_layers, plan)
    plan = describe_motion_plan(planned_layers) or plan
    assert_scene_camera_plan(plan, planned_layers, case)

    motion = source_motion.model_copy(update={"figma_layers": planned_layers, "motion_plan": plan, "duration": 3.0})
    assets_dir = work_dir / "assets"
    asset_png = render_motion_asset(motion, assets_dir)
    motion_videos = sorted(assets_dir.glob(f"{motion.id}-*.mp4"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    if not motion_videos:
        raise RuntimeError(f"{case['name']}: no motion mp4 generated")
    motion_video = motion_videos[0]

    source_path = work_dir / str(source_motion.asset_path)
    source = Image.open(source_path).convert("RGB").resize((motion.width, motion.height), Image.Resampling.LANCZOS)
    case_dir = root / case["name"]
    case_dir.mkdir(parents=True, exist_ok=True)
    source_out = case_dir / "source.png"
    source.save(source_out)

    frames: dict[str, Path] = {}
    for label, t in (("start", 0.0), ("mid", 1.5), ("end", 2.9)):
        frame_path = case_dir / f"motion_{label}_{safe_time(t)}s.png"
        extract_frame(motion_video, t, frame_path)
        frames[label] = frame_path

    start_img = Image.open(frames["start"]).convert("RGB")
    mid_img = Image.open(frames["mid"]).convert("RGB")
    end_img = Image.open(frames["end"]).convert("RGB")
    diffs = {
        "start": mean_rgb_diff(source, start_img),
        "mid": mean_rgb_diff(source, mid_img),
        "end": mean_rgb_diff(source, end_img),
    }
    diff_reference = end_img if case["expect"] == "start_identity" else start_img
    diff_path = case_dir / "amplified_diff.png"
    build_diff(source, diff_reference, diff_path)

    if case["expect"] == "start_identity":
        if diffs["start"] > 5.5:
            raise AssertionError(f"{case['name']}: start is not source-fidelity enough ({diffs['start']:.2f})")
        if diffs["end"] < 7.0:
            raise AssertionError(f"{case['name']}: camera push is too weak ({diffs['end']:.2f})")
    elif case["expect"] == "end_identity":
        if diffs["end"] > 5.5:
            raise AssertionError(f"{case['name']}: end is not source-fidelity enough ({diffs['end']:.2f})")
        if diffs["start"] < 7.0:
            raise AssertionError(f"{case['name']}: camera pull is too weak ({diffs['start']:.2f})")
    else:
        if diffs["mid"] < 5.0:
            raise AssertionError(f"{case['name']}: pan did not visibly move the composed frame ({diffs['mid']:.2f})")

    for label, image in (("start", start_img), ("mid", mid_img), ("end", end_img)):
        if border_luma(image) < 8.0:
            raise AssertionError(f"{case['name']}: possible black edge in {label} frame")

    final_video = None
    final_frames: dict[str, str] = {}
    if BASE_VIDEO.exists():
        motion_for_overlay = motion.model_copy(
            update={
                "asset_path": relpath(asset_png, work_dir),
                "video_asset_path": relpath(motion_video, work_dir),
            }
        )
        canvas_w, canvas_h = detect_video_size(BASE_VIDEO)
        fitted = fit_motion_to_canvas(motion_for_overlay, canvas_w, canvas_h)
        final_video = root / f"{case['name']}_final_overlay.mp4"
        apply_overlays(BASE_VIDEO, None, [fitted], assets_dir, final_video)
        for label, t in (("start", 0.0), ("mid", 1.5), ("end", 2.9)):
            frame_path = case_dir / f"final_{label}_{safe_time(t)}s.png"
            extract_frame(final_video, t, frame_path)
            final_frames[label] = str(frame_path)

    return {
        "name": case["name"],
        "asset_id": case["asset_id"],
        "prompt": case["prompt"],
        "status": "pass",
        "motion_video": str(motion_video),
        "final_video": str(final_video) if final_video else None,
        "diffs": diffs,
        "plan_camera": plan.get("camera"),
        "images": {
            "source": str(source_out),
            "start": str(frames["start"]),
            "mid": str(frames["mid"]),
            "end": str(frames["end"]),
            "diff": str(diff_path),
        },
        "final_frames": final_frames,
    }


def main() -> int:
    out_dir = ROOT / "qa_artifacts" / f"motion-scene-camera-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    issues: list[str] = []
    for case in CASES:
        try:
            results.append(render_case(out_dir, case))
        except Exception as exc:
            issues.append(f"{case['name']}: {exc}")
    if results:
        build_contact_sheet(results, out_dir / "scene_camera_contact_sheet.png")
    report = {
        "status": "pass" if not issues else "fail",
        "artifact_dir": str(out_dir),
        "cases": results,
        "issues": issues,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
