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


PROMPT_CASES: list[dict[str, Any]] = [
    {
        "id": "p01_scatter_pieces",
        "asset": "123-9785",
        "prompt": (
            "Background layer appears with Venetian Blinds for 0.5 sec, photos reveal with depth parallax, then headline "
            "and text fade up line by line from top to bottom, black buttons rise on position Y from below with a light fade. "
            "The whole composition must finish appearing in 2 seconds. At the end all layers scatter into pieces and fall "
            "down with gravity for the last 3 seconds."
        ),
        "expect": {"intro": ("venetian-blinds-bg", 0.5), "build": ("advanced-composition-build", 1.5), "outro": ("layer-scatter-fall", 3.0)},
    },
    {
        "id": "p02_white_random_drop",
        "asset": "12-159",
        "prompt": (
            "First 0.8 seconds show only a clean white background. Then every visual element flies into the frame in a "
            "staggered random order over 2.4 seconds, photos come from different depth planes, text settles top to bottom, "
            "and buttons rise from the bottom. In the final 2 seconds the whole frame drops down like a heavy stone and fades out."
        ),
        "expect": {"intro": ("white-bg-fade", 0.8), "build": ("advanced-composition-build", 2.4), "outro": ("gravity-drop-fade", 2.0)},
    },
    {
        "id": "p03_fade_only_negation",
        "asset": "136-242",
        "prompt": (
            "Reveal the background using vertical Venetian blinds in 0.7 seconds. Photos should slide with parallax depth, "
            "the main title should fade up first, all smaller text should fade up lines top-to-bottom after it, and CTA buttons "
            "should move on Y from below. All appearance animation must be done by 2.8 seconds. The final 2.2 seconds should "
            "be a clean full-frame fade out only, no falling."
        ),
        "expect": {"intro": ("venetian-blinds-bg", 0.7), "build": ("advanced-composition-build", 2.1), "outro": ("full-frame-fade-out", 2.2)},
    },
    {
        "id": "p04_white_shatter",
        "asset": "123-9785",
        "prompt": (
            "For the first 1.1 seconds use a white screen fade of the background without other elements. After that images pop "
            "in with parallax, text lines fade up from top to bottom, and buttons fly upward on position Y. Everything must be "
            "visible by 3 seconds. During the last 1.5 seconds the composition shatters like broken glass and disappears."
        ),
        "expect": {"intro": ("white-bg-fade", 1.1), "build": ("advanced-composition-build", 1.9), "outro": ("full-frame-shatter", 1.5)},
    },
    {
        "id": "p05_fast_venetian_scatter",
        "asset": "12-159",
        "prompt": (
            "Venetian blinds background reveal duration 0.4 seconds, then photos parallax forward, headline fade up lines, body "
            "copy fade up lines after the headline, badges and black buttons rise from below with slight fade. Finish all intro "
            "animation in 1.6 seconds. In the last 2.5 seconds all layers scatter and fall with physics while fading out."
        ),
        "expect": {"intro": ("venetian-blinds-bg", 0.4), "build": ("advanced-composition-build", 1.2), "outro": ("layer-scatter-fall", 2.5)},
    },
    {
        "id": "p06_three_wave_gravity",
        "asset": "136-242",
        "prompt": (
            "Start with background only for 0.6 seconds. Then build the frame in three waves: photographs with parallax first, "
            "text fade up lines second, buttons rise from bottom third. Whole composition appears over 2.5 seconds. End with "
            "a gravity drop fade over the final 1.8 seconds."
        ),
        "expect": {"intro": ("white-bg-fade", 0.6), "build": ("advanced-composition-build", 1.9), "outro": ("gravity-drop-fade", 1.8)},
    },
    {
        "id": "p07_ru_venetian_scatter",
        "asset": "123-9785",
        "prompt": (
            "Сначала 0,5 секунды фоновый слой открывается эффектом Venetian Blinds. Потом фотографии появляются через "
            "параллакс, заголовок и остальные строки текста появляются fade up lines сверху вниз, кнопки вылетают по position "
            "Y снизу вверх с легким fade in. Вся композиция должна появиться за 2 секунды. В последние 3 секунды слои "
            "рассыпаются на части и опадают вниз по физике."
        ),
        "expect": {"intro": ("venetian-blinds-bg", 0.5), "build": ("advanced-composition-build", 1.5), "outro": ("layer-scatter-fall", 3.0)},
    },
    {
        "id": "p08_ru_white_random_drop",
        "asset": "12-159",
        "prompt": (
            "Первые 1,2 секунды только белый фон без других элементов. Потом все элементы влетают в кадр в случайном порядке "
            "за 2,3 секунды, фотографии идут параллаксом, текст появляется сверху вниз, кнопки поднимаются снизу вверх. В "
            "конце весь фрейм падает вниз как камень и уходит в фейд аут за последние 2 секунды."
        ),
        "expect": {"intro": ("white-bg-fade", 1.2), "build": ("advanced-composition-build", 2.3), "outro": ("gravity-drop-fade", 2.0)},
    },
    {
        "id": "p09_ru_glass_shatter",
        "asset": "136-242",
        "prompt": (
            "Фон появляется через жалюзи за 0,9 секунды. Фотографии идут через parallax, главный заголовок появляется первым "
            "через fade up lines, затем все остальные тексты сверху вниз, затем черные кнопки летят снизу вверх по Y. Все "
            "появление укладывается в 2,4 секунды. Финал: последние 2,5 секунды кадр разбивается как стекло и исчезает."
        ),
        "expect": {"intro": ("venetian-blinds-bg", 0.9), "build": ("advanced-composition-build", 1.5), "outro": ("full-frame-shatter", 2.5)},
    },
    {
        "id": "p10_premium_layered_build",
        "asset": "123-9785",
        "prompt": (
            "Make a premium layered build: Venetian blinds reveal the background for 0.3 seconds, image cards drift in with "
            "parallax depth, text reveals line-by-line with fade up from top to bottom, badges and buttons rise from below "
            "with a soft fade. The whole composition appears within 2.1 seconds. For the final 3 seconds the layers break "
            "apart, scatter outward, fall down under gravity, and fade out."
        ),
        "expect": {"intro": ("venetian-blinds-bg", 0.3), "build": ("advanced-composition-build", 1.8), "outro": ("layer-scatter-fall", 3.0)},
    },
]

SAMPLE_LABELS = ["source", "intro", "build", "hold", "outro", "diff"]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def relpath(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def safe_name(value: str) -> str:
    return value.replace(".", "p").replace(":", "-")


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


def crop_overlay(frame: Image.Image, fitted: Any) -> Image.Image:
    x = max(0, int(round(float(fitted.x))))
    y = max(0, int(round(float(fitted.y))))
    w = max(1, int(round(float(fitted.width))))
    h = max(1, int(round(float(fitted.height))))
    return frame.convert("RGB").crop((x, y, x + w, y + h))


def detect_vertical_blinds(frame: Image.Image, fitted: Any) -> float:
    crop = crop_overlay(frame, fitted).resize((240, 135), Image.Resampling.LANCZOS).convert("L")
    columns = []
    for x in range(crop.width):
        total = 0
        for y in range(crop.height):
            total += crop.getpixel((x, y))
        columns.append(total / crop.height)
    mean = sum(columns) / max(1, len(columns))
    return (sum((value - mean) ** 2 for value in columns) / max(1, len(columns))) ** 0.5


def phase_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in list(plan.get("phases") or []) if isinstance(item, dict)}


def build_sample_times(phases: dict[str, dict[str, Any]], duration: float) -> dict[str, float]:
    intro = phases.get("intro") or {}
    build = phases.get("build") or {}
    hold = phases.get("hold") or {}
    outro = phases.get("outro") or {}
    intro_start = float(intro.get("start") or 0.0)
    intro_duration = max(0.1, float(intro.get("duration") or 0.1))
    build_start = float(build.get("start") or intro_duration)
    build_duration = max(0.15, float(build.get("duration") or 0.15))
    hold_start = float(hold.get("start") or (build_start + build_duration))
    outro_start = float(outro.get("start") or max(0.1, duration - float(outro.get("duration") or 0.1)))
    return {
        "intro": min(duration - 0.05, intro_start + intro_duration * 0.52),
        "build": min(duration - 0.05, build_start + build_duration * 0.55),
        "hold": min(duration - 0.05, hold_start + 0.25),
        "outro": min(duration - 0.05, max(outro_start + 0.25, duration - 0.35)),
    }


def assert_plan(case: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    phases = phase_map(plan)
    expected = case["expect"]
    for phase_id in ["intro", "build", "outro"]:
        phase = phases.get(phase_id)
        preset, duration = expected[phase_id]
        if not phase:
            issues.append(f"missing {phase_id} phase")
            continue
        actual_preset = phase.get("preset")
        actual_duration = float(phase.get("duration") or 0.0)
        if actual_preset != preset:
            issues.append(f"{phase_id} preset {actual_preset} != {preset}")
        if abs(actual_duration - float(duration)) > 0.04:
            issues.append(f"{phase_id} duration {actual_duration:.3f}s != {float(duration):.3f}s")
    build = phases.get("build") or {}
    subphases = {str(item.get("id")): item for item in list(build.get("subphases") or []) if isinstance(item, dict)}
    for sub_id, preset in {"photos": "parallax-photo", "text": "fade-up-lines", "buttons": "button-y-rise"}.items():
        actual = subphases.get(sub_id, {}).get("preset")
        if actual != preset:
            issues.append(f"{sub_id} subphase {actual} != {preset}")
    intro_duration = float(expected["intro"][1])
    build_duration = float(expected["build"][1])
    expected_hold = intro_duration + build_duration
    hold = phases.get("hold")
    if hold and abs(float(hold.get("start") or 0.0) - expected_hold) > 0.04:
        issues.append(f"hold starts at {float(hold.get('start') or 0.0):.3f}s != {expected_hold:.3f}s")
    return issues


def render_case(case: dict[str, Any], out_dir: Path, base_video: Path) -> dict[str, Any]:
    case_id = str(case["id"])
    work_dir = out_dir / "work" / case_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    source_motion = motion_from_plugin_asset(work_dir, str(case["asset"]), start=0, duration=8.0)
    prompt = str(case["prompt"])
    route_ok = should_use_frame_choreography_prompt(prompt)
    planned_layers = plan_frame_choreography(prompt, source_motion)
    plan = describe_motion_plan(planned_layers) or {}
    planned_layers = attach_frame_motion_contract(prompt, str(source_motion.id), planned_layers, plan)
    plan = describe_motion_plan(planned_layers) or plan
    motion = source_motion.model_copy(update={"figma_layers": planned_layers, "motion_plan": plan})

    assets_dir = work_dir / "assets"
    asset_png = render_motion_asset(motion, assets_dir)
    videos = sorted(assets_dir.glob(f"{motion.id}-*.mp4"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
    if not videos:
        raise RuntimeError(f"{case_id}: motion MP4 was not generated")
    motion_video = videos[0]
    motion = motion.model_copy(update={"asset_path": relpath(asset_png, work_dir), "video_asset_path": relpath(motion_video, work_dir)})

    canvas_w, canvas_h = detect_video_size(base_video)
    fitted = fit_motion_to_canvas(motion, canvas_w, canvas_h)
    final_video = out_dir / f"{case_id}-final.mp4"
    apply_overlays(base_video, None, [fitted], assets_dir, final_video)

    frames_dir = out_dir / case_id
    frames_dir.mkdir(parents=True, exist_ok=True)
    source_path = work_dir / str(source_motion.asset_path)
    source_img = Image.open(source_path).convert("RGB")
    source_copy = frames_dir / "source.png"
    source_img.save(source_copy)

    phases = phase_map(plan)
    samples = build_sample_times(phases, float(plan.get("duration") or 8.0))
    images = {"source": str(source_copy)}
    extracted: dict[str, Path] = {}
    for label, sample_time in samples.items():
        frame_path = frames_dir / f"{label}_{safe_name(f'{sample_time:.3f}')}s.png"
        extract_frame(final_video, sample_time, frame_path)
        extracted[label] = frame_path
        images[label] = str(frame_path)

    hold_crop = crop_overlay(Image.open(extracted["hold"]).convert("RGB"), fitted)
    source_scaled = source_img.resize(hold_crop.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(source_scaled, hold_crop)
    diff_boost = diff.point(lambda value: min(255, value * 6)).convert("RGB")
    diff_path = frames_dir / "source_vs_hold_diff_x6.png"
    diff_boost.save(diff_path)
    images["diff"] = str(diff_path)

    build_diff = mean_rgb_diff(source_scaled, crop_overlay(Image.open(extracted["build"]).convert("RGB"), fitted))
    hold_diff = mean_rgb_diff(source_scaled, hold_crop)
    outro_diff = mean_rgb_diff(source_scaled, crop_overlay(Image.open(extracted["outro"]).convert("RGB"), fitted))
    stripe_score = detect_vertical_blinds(Image.open(extracted["intro"]).convert("RGB"), fitted)
    motion_size = detect_video_size(motion_video)

    issues = []
    if not route_ok:
        issues.append("prompt did not route to whole-frame choreography")
    issues.extend(assert_plan(case, plan))
    if list(motion_size) != list(source_img.size):
        issues.append(f"motion MP4 size {motion_size} != source PNG size {source_img.size}")
    if hold_diff > 12.0:
        issues.append(f"settled Figma diff too high: {hold_diff:.3f}")
    if build_diff < 1.0:
        issues.append(f"build frame too close to settled frame: {build_diff:.3f}")
    if outro_diff < 5.0:
        issues.append(f"outro frame too close to settled frame: {outro_diff:.3f}")
    if case["expect"]["intro"][0] == "venetian-blinds-bg" and stripe_score < 1.2:
        issues.append(f"Venetian stripe signal too weak: {stripe_score:.3f}")

    return {
        "id": case_id,
        "asset": case["asset"],
        "frame_name": source_motion.text,
        "status": "pass" if not issues else "fail",
        "issues": issues,
        "prompt": prompt,
        "plan": plan,
        "samples": {key: round(value, 3) for key, value in samples.items()},
        "metrics": {
            "venetian_stripe_score": round(stripe_score, 3),
            "build_vs_source_diff": round(build_diff, 3),
            "hold_vs_source_diff": round(hold_diff, 3),
            "outro_vs_source_diff": round(outro_diff, 3),
            "motion_video_size": list(motion_size),
            "source_png_size": list(source_img.size),
        },
        "motion_video": str(motion_video),
        "final_video": str(final_video),
        "images": images,
    }


def build_contact_sheet(cases: list[dict[str, Any]], output: Path) -> None:
    thumb_w, thumb_h, label_h = 260, 146, 42
    sheet = Image.new("RGB", (thumb_w * len(SAMPLE_LABELS), (thumb_h + label_h) * len(cases)), (242, 242, 242))
    draw = ImageDraw.Draw(sheet)
    font = load_font(14)
    for row_index, case in enumerate(cases):
        y = row_index * (thumb_h + label_h)
        for col_index, label in enumerate(SAMPLE_LABELS):
            x = col_index * thumb_w
            path = case.get("images", {}).get(label)
            if path:
                image = Image.open(path).convert("RGB")
                image.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                bg = Image.new("RGB", (thumb_w, thumb_h), (18, 18, 18))
                bg.paste(image, ((thumb_w - image.width) // 2, (thumb_h - image.height) // 2))
                sheet.paste(bg, (x, y + label_h))
            status = case.get("status", "fail")
            draw.text((x + 6, y + 5), f"{case['id']} {label}", fill=(0, 0, 0), font=font)
            draw.text((x + 6, y + 23), status, fill=(0, 110, 0) if status == "pass" else (180, 0, 0), font=font)
    sheet.save(output)


def write_report(out_dir: Path, cases: list[dict[str, Any]], contact_sheet: Path) -> Path:
    report = out_dir / "REPORT.md"
    lines = [
        "# Complex Prompt Motion QA",
        "",
        f"Contact sheet: `{contact_sheet}`",
        "",
        f"Passed: `{sum(1 for case in cases if case['status'] == 'pass')}/{len(cases)}`",
        "",
    ]
    for case in cases:
        phases = phase_map(case.get("plan") or {})
        compact_phases = {
            key: {"preset": value.get("preset"), "start": value.get("start"), "duration": value.get("duration")}
            for key, value in phases.items()
            if key in {"intro", "build", "hold", "outro"}
        }
        lines.extend(
            [
                f"## {case['id']} / {case['asset']} / {case['frame_name']}",
                "",
                f"Status: `{case['status']}`",
                f"Prompt: {case['prompt']}",
                f"Final MP4: `{case['final_video']}`",
                f"Motion MP4: `{case['motion_video']}`",
                f"Samples: `{case['samples']}`",
                f"Phases: `{compact_phases}`",
                f"Metrics: `{case['metrics']}`",
                f"Issues: `{case['issues']}`",
                "",
            ]
        )
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--base-video", default="")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-complex-prompts-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base_video = Path(args.base_video)
    cases = [render_case(case, out_dir, base_video) for case in PROMPT_CASES]
    contact_sheet = out_dir / "complex_prompt_contact_sheet.png"
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
