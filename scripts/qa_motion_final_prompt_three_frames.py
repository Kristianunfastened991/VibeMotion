from __future__ import annotations

import argparse
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


FINAL_PROMPT = (
    "Фоновый слой появляется через эффект Venetian Blinds "
    "(длительность анимации - 0,5сек), затем фотографии появляются через эффект параллакса, "
    "после чего появляется текст через эффект fade up lines (сначала главный заголовок, затем все остальные части текста - "
    "анимация появления происходит в порядке «сверху вниз»), затем появляются черные кнопки через эффект вылета по "
    "position Y снизу вверх + легкий fade in. Вся композиция должна появиться (и все анимации должны произойти) "
    "за 2 секунды. Анимация исчезания всей композициипусть происходит через эффект «рассыпания» слоев и опадания "
    "их вниз с учетом законов физики"
)

DEFAULT_ASSETS = ["123-9785", "12-159", "136-242"]
SAMPLE_TIMES = [0.125, 0.35, 0.5, 1.1, 1.65, 2.05, 5.5, 7.4]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def relpath(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def safe_time(value: float) -> str:
    return str(value).replace(".", "p")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"]:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def extract_frame(video: Path, time_value: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-i", str(video), "-ss", f"{time_value:.3f}", "-frames:v", "1", "-update", "1", "-q:v", "2", str(output)])


def mean_rgb_diff(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def layer_counts(layers: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"photos": 0, "texts": 0, "button_like": 0}
    for layer in layers:
        if layer.get("visible") is False:
            continue
        kind = layer.get("kind")
        name_text = f"{layer.get('name') or ''} {layer.get('text') or ''}".casefold()
        fill = str(layer.get("fill") or "").casefold().replace(" ", "")
        width = float(layer.get("width") or 0)
        height = float(layer.get("height") or 0)
        if kind == "image":
            counts["photos"] += 1
        if kind == "text":
            counts["texts"] += 1
        if (
            "button" in name_text
            or "follow" in name_text
            or "unlock" in name_text
            or (
                height > 0
                and width / height > 1.7
                and ("0,0,0" in fill or "#000" in fill)
            )
        ):
            counts["button_like"] += 1
    return counts


def assert_phase_plan(plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    phases = {str(item.get("id")): item for item in list(plan.get("phases") or []) if isinstance(item, dict)}
    expected = {
        "intro": ("venetian-blinds-bg", 0.5),
        "build": ("advanced-composition-build", 1.5),
        "outro": ("layer-scatter-fall", 3.0),
    }
    for phase_id, (preset, duration) in expected.items():
        phase = phases.get(phase_id)
        if not phase:
            issues.append(f"missing phase {phase_id}")
            continue
        if phase.get("preset") != preset:
            issues.append(f"{phase_id} preset {phase.get('preset')} != {preset}")
        actual_duration = float(phase.get("duration") or 0)
        if abs(actual_duration - duration) > 0.02:
            issues.append(f"{phase_id} duration {actual_duration:.3f}s != {duration:.3f}s")
    build = phases.get("build") or {}
    subphases = {str(item.get("id")): item for item in list(build.get("subphases") or []) if isinstance(item, dict)}
    sub_expected = {
        "photos": "parallax-photo",
        "text": "fade-up-lines",
        "buttons": "button-y-rise",
    }
    for sub_id, preset in sub_expected.items():
        if subphases.get(sub_id, {}).get("preset") != preset:
            issues.append(f"build subphase {sub_id} != {preset}")
    hold = phases.get("hold") or {}
    if abs(float(hold.get("start") or 0) - 2.0) > 0.02:
        issues.append(f"hold starts at {float(hold.get('start') or 0):.3f}s, expected 2.000s")
    return issues


def crop_overlay(frame: Image.Image, fitted: Any) -> Image.Image:
    x = max(0, int(round(float(fitted.x))))
    y = max(0, int(round(float(fitted.y))))
    w = max(1, int(round(float(fitted.width))))
    h = max(1, int(round(float(fitted.height))))
    return frame.convert("RGB").crop((x, y, x + w, y + h))


def detect_vertical_blinds(frame: Image.Image, fitted: Any) -> float:
    crop = crop_overlay(frame, fitted).resize((240, 135), Image.Resampling.LANCZOS).convert("L")
    values = []
    for x in range(crop.width):
        total = 0
        for y in range(crop.height):
            total += crop.getpixel((x, y))
        values.append(total / crop.height)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance ** 0.5


def build_contact_sheet(cases: list[dict[str, Any]], output: Path) -> None:
    labels = ["source", "0p125", "0p35", "0p5", "1p1", "1p65", "2p05", "5p5", "7p4", "diff"]
    thumb_w, thumb_h, label_h = 260, 146, 30
    sheet = Image.new("RGB", (thumb_w * len(labels), (thumb_h + label_h) * len(cases)), (242, 242, 242))
    draw = ImageDraw.Draw(sheet)
    font = load_font(14)
    for row_index, case in enumerate(cases):
        y = row_index * (thumb_h + label_h)
        for col_index, label in enumerate(labels):
            x = col_index * thumb_w
            path = case.get("images", {}).get(label)
            if path:
                image = Image.open(path).convert("RGB")
                image.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                bg = Image.new("RGB", (thumb_w, thumb_h), (18, 18, 18))
                bg.paste(image, ((thumb_w - image.width) // 2, (thumb_h - image.height) // 2))
                sheet.paste(bg, (x, y + label_h))
            draw.text((x + 6, y + 7), f"{case['asset_id']} {label}", fill=(0, 0, 0), font=font)
    sheet.save(output)


def render_case(asset_id: str, out_dir: Path, base_video: Path) -> dict[str, Any]:
    work_dir = out_dir / "work" / asset_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_motion = motion_from_plugin_asset(work_dir, asset_id, start=0, duration=8.0)
    source_counts = layer_counts(list(source_motion.figma_layers or []))
    if not should_use_frame_choreography_prompt(FINAL_PROMPT):
        raise AssertionError("final prompt did not route to whole-frame choreography")
    planned_layers = plan_frame_choreography(FINAL_PROMPT, source_motion)
    plan = describe_motion_plan(planned_layers) or {}
    planned_layers = attach_frame_motion_contract(FINAL_PROMPT, str(source_motion.id), planned_layers, plan)
    plan = describe_motion_plan(planned_layers) or plan
    motion = source_motion.model_copy(update={"figma_layers": planned_layers, "motion_plan": plan})
    assets_dir = work_dir / "assets"
    asset_png = render_motion_asset(motion, assets_dir)
    videos = sorted(assets_dir.glob(f"{motion.id}-*.mp4"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    if not videos:
        raise RuntimeError(f"{asset_id}: motion MP4 was not generated")
    motion_video = videos[0]
    motion = motion.model_copy(
        update={
            "asset_path": relpath(asset_png, work_dir),
            "video_asset_path": relpath(motion_video, work_dir),
        }
    )
    canvas_w, canvas_h = detect_video_size(base_video)
    fitted = fit_motion_to_canvas(motion, canvas_w, canvas_h)
    final_video = out_dir / f"{asset_id}-final-preview.mp4"
    apply_overlays(base_video, None, [fitted], assets_dir, final_video)
    frames_dir = out_dir / asset_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    source_path = work_dir / str(source_motion.asset_path)
    source_img = Image.open(source_path).convert("RGB")
    source_copy = frames_dir / "source.png"
    source_img.save(source_copy)
    images = {"source": str(source_copy)}
    final_frames: dict[float, Path] = {}
    for sample_time in SAMPLE_TIMES:
        frame_path = frames_dir / f"final_{safe_time(sample_time)}s.png"
        extract_frame(final_video, sample_time, frame_path)
        final_frames[sample_time] = frame_path
        images[safe_time(sample_time)] = str(frame_path)
    settled_frame = Image.open(final_frames[2.05]).convert("RGB")
    settled_crop = crop_overlay(settled_frame, fitted)
    source_scaled = source_img.resize(settled_crop.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(source_scaled, settled_crop)
    diff_boost = diff.point(lambda value: min(255, value * 6)).convert("RGB")
    diff_path = frames_dir / "source_vs_final_2p05_diff_x6.png"
    diff_boost.save(diff_path)
    images["diff"] = str(diff_path)
    hold_diff = mean_rgb_diff(source_scaled, settled_crop)
    build_crop = crop_overlay(Image.open(final_frames[1.65]).convert("RGB"), fitted)
    build_diff = mean_rgb_diff(source_scaled, build_crop)
    outro_crop = crop_overlay(Image.open(final_frames[7.4]).convert("RGB"), fitted)
    outro_diff = mean_rgb_diff(source_scaled, outro_crop)
    stripe_score = max(
        detect_vertical_blinds(Image.open(final_frames[0.125]).convert("RGB"), fitted),
        detect_vertical_blinds(Image.open(final_frames[0.35]).convert("RGB"), fitted),
    )
    issues = assert_phase_plan(plan)
    if hold_diff > 10.0:
        issues.append(f"settled Figma diff too high: {hold_diff:.3f}")
    if build_diff < 2.0:
        issues.append(f"build frame too close to settled state: {build_diff:.3f}")
    if outro_diff < 5.0:
        issues.append(f"outro frame too close to settled state: {outro_diff:.3f}")
    if stripe_score < 8.0:
        issues.append(f"Venetian stripe signal too weak: {stripe_score:.3f}")
    return {
        "asset_id": asset_id,
        "frame_name": source_motion.text,
        "source_counts": source_counts,
        "motion_video": str(motion_video),
        "final_video": str(final_video),
        "source_size": list(source_img.size),
        "fitted_rect": {
            "x": round(float(fitted.x), 3),
            "y": round(float(fitted.y), 3),
            "width": round(float(fitted.width), 3),
            "height": round(float(fitted.height), 3),
        },
        "plan": plan,
        "metrics": {
            "venetian_stripe_score": round(stripe_score, 3),
            "build_vs_source_diff_1p65": round(build_diff, 3),
            "settled_vs_source_diff_2p05": round(hold_diff, 3),
            "outro_vs_source_diff_7p4": round(outro_diff, 3),
        },
        "images": images,
        "status": "pass" if not issues else "fail",
        "issues": issues,
    }


def write_report(out_dir: Path, cases: list[dict[str, Any]], contact_sheet: Path) -> Path:
    report = out_dir / "REPORT.md"
    lines = [
        "# Final Prompt 3-Frame QA",
        "",
        f"Prompt: {FINAL_PROMPT}",
        "",
        f"Contact sheet: `{contact_sheet}`",
        "",
    ]
    for case in cases:
        metrics = case["metrics"]
        lines.extend(
            [
                f"## {case['asset_id']} / {case['frame_name']}",
                "",
                f"Status: `{case['status']}`",
                f"Final MP4: `{case['final_video']}`",
                f"Motion MP4: `{case['motion_video']}`",
                f"Source counts: `{case['source_counts']}`",
                f"Metrics: `{metrics}`",
                f"Issues: `{case['issues']}`",
                "",
            ]
        )
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="*", default=DEFAULT_ASSETS)
    parser.add_argument("--out", default="")
    parser.add_argument("--base-video", default="")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-final-prompt-3frames-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base_video = Path(args.base_video)
    cases = [render_case(asset_id, out_dir, base_video) for asset_id in args.assets[:3]]
    contact_sheet = out_dir / "final_prompt_3frames_contact_sheet.png"
    build_contact_sheet(cases, contact_sheet)
    report = write_report(out_dir, cases, contact_sheet)
    summary = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ["status", "out_dir", "report", "contact_sheet"]}, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
