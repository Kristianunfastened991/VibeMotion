from __future__ import annotations

import argparse
import json
import random
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

from app.services.figma_plugin import motion_from_plugin_asset  # noqa: E402
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt  # noqa: E402
from app.services.motion import render_motion_video_asset  # noqa: E402
from app.services.motion_intent import attach_frame_motion_contract  # noqa: E402


STRICT_GRADIENT_PROMPT = (
    "\u0412\u043d\u0430\u0447\u0430\u043b\u0435 \u0442\u043e\u043b\u044c\u043a\u043e "
    "\u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d - \u0424\u0435\u0439\u0434 \u0438\u043d 2 "
    "\u0441\u0435\u043a\u0443\u043d\u0434\u044b. \u041f\u043e\u0442\u043e\u043c "
    "\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437 \u0432\u0441\u0435 "
    "\u0441\u043b\u043e\u0438 \u043f\u043e\u044f\u0432\u043b\u044f\u044e\u0442\u0441\u044f "
    "\u0433\u0440\u0430\u0444\u0434\u0438\u0435\u043d\u0442\u043e\u043c \u0447\u0435\u0440\u0435\u0437 "
    "\u0444\u0435\u0439\u0434 \u0438\u043d, \u0431\u0435\u0437 \u0437\u0430\u043b\u0435\u0442\u0430. "
    "\u0412\u044b\u0445\u043e\u0434 - \u0432\u0435\u0441\u044c \u0444\u0440\u0435\u0439\u043c "
    "\u0444\u0435\u0439\u0434\u0430\u0443\u0442 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b "
    "\u0432\u043a\u043e\u043d\u0446\u0435."
)

PROMPT_SPECS: list[dict[str, Any]] = [
    {
        "id": "q01-strict-gradient",
        "title": "white intro, top-down gradient/fade layer reveal, final full-frame fadeout",
        "prompt": STRICT_GRADIENT_PROMPT,
        "samples": [0.4, 1.9, 2.45, 4.8, 7.0],
        "expected_phases": {
            "intro": ("white-bg-fade", 2.0),
            "build": ("gradient-fade-stagger", 3.0),
            "outro": ("full-frame-fade-out", 2.0),
        },
        "strict_gradient": True,
    },
    {
        "id": "q02-random-fly-shatter",
        "title": "white intro, random fly-in, final shatter/fade",
        "prompt": "First 1 second: only a white background. Then all layers fly into place over 2 seconds in random order. In the last 2 seconds the full frame shatters into pieces and fades out.",
        "samples": [0.35, 1.4, 2.8, 6.5, 7.4],
        "expected_phases": {
            "intro": ("white-bg-fade", 1.0),
            "build": ("random-fly-in-stagger", 2.0),
            "outro": ("full-frame-shatter", 2.0),
        },
    },
    {
        "id": "q03-advanced-editorial",
        "title": "venetian intro, role-specific editorial build, physics scatter outro",
        "prompt": "First 0.5 seconds the background appears with venetian blinds. Then photos use parallax, text uses fade up lines from top to bottom, and buttons rise on position Y; the whole composition appears within 2 seconds. In the last 2 seconds layers scatter and fall down with physics while fading out.",
        "samples": [0.2, 0.8, 1.8, 6.6, 7.5],
        "expected_phases": {
            "intro": ("venetian-blinds-bg", 0.5),
            "build": ("advanced-composition-build", 1.5),
            "outro": ("layer-scatter-fall", 2.0),
        },
        "expected_subphases": {
            "photos": "parallax-photo",
            "text": "fade-up-lines",
            "buttons": "button-y-rise",
        },
    },
    {
        "id": "q04-camera-push",
        "title": "camera-only push, no layer animation",
        "prompt": "Add a slow camera push in over 3 seconds. Keep all layers exactly as designed, do not animate separate layers.",
        "samples": [0.0, 1.5, 3.0, 5.5, 7.5],
        "expected_phases": {
            "camera": ("camera-push", 8.0),
        },
        "camera_only": True,
    },
    {
        "id": "q05-pixel-gradient-fade",
        "title": "pixel snap intro, top-down gradient fade, final fadeout",
        "prompt": "First 1 second: white background appears with pixel snap. Then all layers appear from top to bottom with a soft gradient fade-in over 2 seconds, no flying. Final 1.5 seconds: the whole frame fades out.",
        "samples": [0.2, 1.2, 2.6, 6.2, 7.4],
        "expected_phases": {
            "intro": ("soft-pixel-snap", 1.0),
            "build": ("gradient-fade-stagger", 2.0),
            "outro": ("full-frame-fade-out", 1.5),
        },
        "strict_gradient": True,
    },
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def extract_frame(video: Path, seconds: float, output: Path) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0 and output.exists()


def mean_rgb_diff(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def phase_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in list(plan.get("phases") or []) if isinstance(item, dict)}


def source_texts(layers: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(layer.get("id") or ""): str(layer.get("text") or "")
        for layer in layers
        if layer.get("kind") == "text" and str(layer.get("id") or "") and str(layer.get("text") or "")
    }


def selected_asset_ids(count: int, seed: int) -> list[str]:
    records = json.loads((ROOT / "app/static/assets/figma-plugin/assets.json").read_text(encoding="utf-8"))
    items = list(records.values()) if isinstance(records, dict) else list(records)
    candidates: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "")
        if not asset_id:
            continue
        if not (ROOT / "app/static/assets/figma-plugin" / str(item.get("asset_file") or f"{asset_id}.png")).exists():
            continue
        if len(source_texts(list(item.get("figma_layers") or []))) <= 0:
            continue
        candidates.append(item)
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return [str(item["id"]) for item in candidates[:count]]


def plan_motion(motion: Any, prompt: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not should_use_frame_choreography_prompt(prompt):
        raise AssertionError("prompt did not route to whole-frame choreography")
    planning_motion = motion.model_copy(update={"figma_layers": [dict(layer) for layer in list(motion.figma_layers or [])]})
    layers = plan_frame_choreography(prompt, planning_motion)
    plan = describe_motion_plan(layers) or {}
    layers = attach_frame_motion_contract(prompt, str(motion.id), layers, plan)
    return layers, describe_motion_plan(layers) or plan


def assert_phase_contract(prompt_spec: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    phases = phase_map(plan)
    for phase_id, (expected_preset, expected_duration) in dict(prompt_spec["expected_phases"]).items():
        phase = phases.get(phase_id)
        if not phase:
            issues.append(f"missing phase {phase_id}")
            continue
        if phase.get("preset") != expected_preset:
            issues.append(f"{phase_id} preset {phase.get('preset')} != {expected_preset}")
        if abs(float(phase.get("duration") or 0) - float(expected_duration)) > 0.04:
            issues.append(f"{phase_id} duration {phase.get('duration')} != {expected_duration}")
    sub_expected = dict(prompt_spec.get("expected_subphases") or {})
    if sub_expected:
        build = phases.get("build") or {}
        subphases = {str(item.get("id")): item for item in list(build.get("subphases") or []) if isinstance(item, dict)}
        for sub_id, preset in sub_expected.items():
            if subphases.get(sub_id, {}).get("preset") != preset:
                issues.append(f"build subphase {sub_id} preset {subphases.get(sub_id, {}).get('preset')} != {preset}")
    return issues


def text_transform_issues(prompt_spec: dict[str, Any], layers: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for layer in layers:
        if layer.get("kind") != "text":
            continue
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if not recipe:
            continue
        dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
        keyframes = [frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        for frame in keyframes:
            scale = float(frame.get("scale", 1) or 1)
            scale_x = float(frame.get("scaleX", 1) or 1)
            scale_y = float(frame.get("scaleY", 1) or 1)
            if abs(scale_x - scale_y) > 0.01:
                issues.append(f"text layer {layer.get('id')}: non-uniform scaleX/scaleY")
                break
            if prompt_spec.get("camera_only") and abs(scale - 1) > 0.01:
                issues.append(f"text layer {layer.get('id')}: camera-only prompt added text-layer scale")
                break
            if prompt_spec.get("strict_gradient") and abs(scale - 1) > 0.01:
                issues.append(f"text layer {layer.get('id')}: strict gradient prompt added text scale")
                break
    return issues


def strict_gradient_issues(layers: list[dict[str, Any]], plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    phases = phase_map(plan)
    build = phases.get("build") or {}
    if build.get("order") != "top-down-by-role":
        issues.append(f"build order {build.get('order')} != top-down-by-role")
    for layer in layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if not recipe:
            continue
        tags = {str(tag) for tag in list(recipe.get("tags") or [])}
        if "frame" not in tags or "background" in tags or str(layer.get("id") or "").startswith("__frame_choreo_white_bg"):
            continue
        dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
        keyframes = [frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        for frame in keyframes[:4]:
            if abs(float(frame.get("x") or 0)) > 0.01 or abs(float(frame.get("y") or 0)) > 0.01:
                issues.append(f"{layer.get('id')}: strict gradient has unwanted fly movement")
                break
        effects = [effect for effect in list(dsl.get("effects") or []) if isinstance(effect, dict)]
        if not any(effect.get("type") == "wipe-reveal" and effect.get("direction") == "down" for effect in effects):
            issues.append(f"{layer.get('id')}: missing downward wipe-reveal")
    return issues


def assert_text_integrity(source_motion: Any, planned_layers: list[dict[str, Any]], visual_report: dict[str, Any], first_frame_diff: float | None) -> list[str]:
    issues: list[str] = []
    before = source_texts(list(source_motion.figma_layers or []))
    after = source_texts(planned_layers)
    if before != {key: after.get(key, "") for key in before}:
        missing = [key for key, value in before.items() if after.get(key) != value]
        issues.append(f"figma text layer content changed: {missing[:8]}")
    if before:
        checks = {str(item.get("id")): item for item in list(visual_report.get("checks") or []) if isinstance(item, dict)}
        hold = checks.get("exact_hold_pixel_match") or {}
        if hold.get("status") == "pass":
            diff = float((visual_report.get("metrics") or {}).get("exact_hold_mean_abs_diff") or 0)
            if diff > 0.75:
                issues.append(f"hold frame text/source diff too high: {diff:.3f}")
        elif first_frame_diff is not None:
            if first_frame_diff > 3.5:
                issues.append(f"camera/source first-frame diff too high for text check: {first_frame_diff:.3f}")
        else:
            issues.append(f"no exact hold pixel match for text check: {hold.get('status')}")
    return issues


def render_and_sample(case: dict[str, Any], motion: Any, assets_dir: Path, out_dir: Path, fps: int) -> tuple[Path, list[str], dict[str, Any], float | None]:
    video = render_motion_video_asset(motion, assets_dir, fps=fps)
    if video is None or not video.exists():
        raise RuntimeError("motion video was not rendered")
    copied = out_dir / f"{case['id']}.mp4"
    shutil.copy2(video, copied)
    report_path = video.with_suffix(".visual-self-check.json")
    visual_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    frame_dir = out_dir / case["id"]
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[str] = []
    first_frame_diff: float | None = None
    source_path = assets_dir.parent / str(case["source_asset_path"])
    source_image = Image.open(source_path).convert("RGB") if source_path.exists() else None
    for seconds in case["samples"]:
        frame = frame_dir / f"{case['id']}_{str(seconds).replace('.', 'p')}s.png"
        if extract_frame(video, float(seconds), frame):
            frames.append(str(frame))
            if abs(float(seconds)) < 0.001 and source_image is not None:
                rendered = Image.open(frame).convert("RGB").resize(source_image.size, Image.Resampling.LANCZOS)
                first_frame_diff = mean_rgb_diff(source_image, rendered)
    return copied, frames, visual_report, first_frame_diff


def build_contact_sheet(cases: list[dict[str, Any]], out_dir: Path) -> Path:
    label_w = 390
    thumb_w = 220
    thumb_h = 124
    row_h = 170
    cols = 5
    sheet = Image.new("RGB", (label_w + cols * thumb_w + 34, 56 + len(cases) * row_h), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    draw.text((18, 16), "5 prompts x 10 Figma frames QA", fill=(18, 18, 18), font=font(22, True))
    y = 56
    small = font(10)
    label = font(13, True)
    for case in cases:
        color = (26, 126, 64) if case["status"] == "pass" else (170, 55, 32)
        draw.rounded_rectangle((10, y + 6, sheet.width - 10, y + row_h - 8), radius=8, fill=(255, 255, 255), outline=(218, 214, 204))
        draw.text((22, y + 18), f"{case['id']} {case['status'].upper()}", fill=color, font=label)
        draw.text((22, y + 38), f"{case['asset_id']} / {case['prompt_id']}", fill=(25, 25, 25), font=small)
        draw.text((22, y + 56), case.get("title", "")[:80], fill=(70, 70, 70), font=small)
        message = "; ".join(case.get("issues") or []) or case.get("summary", "")
        draw.text((22, y + 75), message[:95], fill=(145, 45, 30) if case.get("issues") else (70, 70, 70), font=small)
        for index, frame_path in enumerate(case.get("frames", [])[:cols]):
            x = label_w + 12 + index * thumb_w
            try:
                with Image.open(frame_path).convert("RGB") as frame:
                    frame.thumbnail((thumb_w - 12, thumb_h), Image.Resampling.LANCZOS)
                    bg = Image.new("RGB", (thumb_w - 12, thumb_h), (18, 18, 18))
                    bg.paste(frame, ((bg.width - frame.width) // 2, (bg.height - frame.height) // 2))
                    sheet.paste(bg, (x, y + 20))
            except Exception:
                draw.rectangle((x, y + 20, x + thumb_w - 12, y + 20 + thumb_h), fill=(210, 210, 210))
            sample = case.get("samples", [])[index] if index < len(case.get("samples", [])) else ""
            draw.text((x + 4, y + 20 + thumb_h + 4), f"t={sample}s", fill=(65, 65, 65), font=small)
        y += row_h
    target = out_dir / "motion_5prompts_10frames_contact_sheet.png"
    sheet.save(target)
    return target


def write_report(cases: list[dict[str, Any]], out_dir: Path, contact_sheet: Path, assets: list[str]) -> Path:
    report = out_dir / "REPORT.md"
    lines = [
        "# 5 Random Prompts x 10 Figma Frames QA",
        "",
        f"- Assets: `{assets}`",
        f"- Total: `{len(cases)}`, pass: `{sum(1 for c in cases if c['status'] == 'pass')}`, fail: `{sum(1 for c in cases if c['status'] != 'pass')}`",
        f"- Contact sheet: `{contact_sheet}`",
        "",
        f"![contact sheet]({contact_sheet.as_posix()})",
        "",
        "## Prompts",
        "",
    ]
    for spec in PROMPT_SPECS:
        lines.append(f"- `{spec['id']}`: {spec['prompt']}")
    lines.extend(["", "## Cases", ""])
    for case in cases:
        lines.extend(
            [
                f"### {case['id']} - {case['status'].upper()}",
                "",
                f"- Asset: `{case['asset_id']}`",
                f"- Prompt: `{case['prompt_id']}`",
                f"- Summary: {case.get('summary', '')}",
                f"- Issues: {('; '.join(case.get('issues') or []) or 'none')}",
                f"- Text layers checked: `{case.get('text_layer_count')}`",
                f"- Hold diff: `{case.get('hold_diff')}`",
                f"- First-frame diff: `{case.get('first_frame_diff')}`",
                f"- Video: `{case.get('video')}`",
                "",
            ]
        )
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260518)
    parser.add_argument("--assets", nargs="*", default=None)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    assets = args.assets[: args.count] if args.assets else selected_asset_ids(args.count, args.seed)
    if len(assets) < args.count:
        raise SystemExit(f"Only {len(assets)} text-bearing Figma assets available")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-5prompts-10frames-{stamp}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, Any]] = []
    for asset_id in assets:
        asset_work_dir = out_dir / "work" / asset_id
        if asset_work_dir.exists():
            shutil.rmtree(asset_work_dir)
        asset_work_dir.mkdir(parents=True, exist_ok=True)
        source_motion = motion_from_plugin_asset(asset_work_dir, asset_id, start=0, duration=8.0)
        source_text_count = len(source_texts(list(source_motion.figma_layers or [])))
        for prompt_spec in PROMPT_SPECS:
            case_id = f"{asset_id}-{prompt_spec['id']}"
            case = {
                "id": case_id,
                "asset_id": asset_id,
                "prompt_id": prompt_spec["id"],
                "title": prompt_spec["title"],
                "prompt": prompt_spec["prompt"],
                "samples": list(prompt_spec["samples"]),
                "source_asset_path": str(source_motion.asset_path),
                "text_layer_count": source_text_count,
            }
            issues: list[str] = []
            try:
                planned_layers, plan = plan_motion(source_motion, prompt_spec["prompt"])
                issues.extend(assert_phase_contract(prompt_spec, plan))
                issues.extend(text_transform_issues(prompt_spec, planned_layers))
                if prompt_spec.get("strict_gradient"):
                    issues.extend(strict_gradient_issues(planned_layers, plan))
                motion = source_motion.model_copy(
                    update={
                        "id": f"qa50-{asset_id}-{prompt_spec['id']}",
                        "figma_layers": planned_layers,
                        "motion_plan": plan,
                        "duration": max(float(source_motion.duration or 0), float(plan.get("minimum_duration") or 0), 3.0),
                    }
                )
                video, frames, visual_report, first_frame_diff = render_and_sample(case, motion, asset_work_dir / "assets", out_dir, max(4, int(args.fps)))
                case["video"] = str(video)
                case["frames"] = frames
                case["first_frame_diff"] = round(first_frame_diff, 3) if first_frame_diff is not None else None
                metrics = visual_report.get("metrics") if isinstance(visual_report.get("metrics"), dict) else {}
                case["hold_diff"] = metrics.get("exact_hold_mean_abs_diff")
                issues.extend(assert_text_integrity(source_motion, planned_layers, visual_report, first_frame_diff))
                phases = ", ".join(f"{p.get('id')}:{p.get('preset')} {p.get('duration')}s" for p in list(plan.get("phases") or []))
                case["summary"] = phases
            except Exception as exc:
                issues.append(f"{type(exc).__name__}: {exc}")
                case["video"] = ""
                case["frames"] = []
                case["hold_diff"] = None
                case["first_frame_diff"] = None
            case["issues"] = issues
            case["status"] = "pass" if not issues else "fail"
            cases.append(case)
            print(f"{case_id}: {case['status']} - {issues or 'ok'}", flush=True)
            # Reset per-case frame/video cache so old animation cannot affect later cases.
            for path in (asset_work_dir / "assets").glob(f"qa50-{asset_id}-{prompt_spec['id']}*"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.suffix.lower() in {".mp4", ".json"} or "_frames_" in path.name:
                    path.unlink(missing_ok=True)

    contact_sheet = build_contact_sheet(cases, out_dir)
    report = write_report(cases, out_dir, contact_sheet, assets)
    summary = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "assets": assets,
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "total": len(cases),
        "pass": sum(1 for case in cases if case["status"] == "pass"),
        "fail": sum(1 for case in cases if case["status"] != "pass"),
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("status", "out_dir", "report", "contact_sheet", "total", "pass", "fail", "assets")}, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
