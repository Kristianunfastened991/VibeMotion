from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.figma_plugin import motion_from_plugin_asset  # noqa: E402
from scripts.qa_motion_5prompts_10frames import (  # noqa: E402
    assert_phase_contract,
    assert_text_integrity,
    font,
    phase_map,
    plan_motion,
    render_and_sample,
    source_texts,
    strict_gradient_issues,
    text_transform_issues,
)


PROMPTS: list[dict[str, Any]] = [
    {
        "id": "w01-ru-gradient-typo-no-fly",
        "title": "Russian typo gradient fade, no fly-in",
        "prompt": "Вначале только белый фон - Фейд ин 2 секунды. Потом сверху вниз все слои появляются графдиентом через фейд ин, без залета. Выход - весь фрейм фейдаут 2 секунды вконце.",
        "samples": [0.4, 1.9, 2.5, 4.8, 7.4],
        "expected_phases": {"intro": ("white-bg-fade", 2.0), "build": ("gradient-fade-stagger", 3.0), "outro": ("full-frame-fade-out", 2.0)},
        "strict_gradient": True,
    },
    {
        "id": "w02-en-gradient-no-flying",
        "title": "English gradient fade, explicit no flying",
        "prompt": "First 1.5 seconds: white background only. Then every layer appears top-to-bottom with a soft gradient fade-in over 2.5 seconds, no flying, no movement. Fade out the whole frame over the final 1.5 seconds.",
        "samples": [0.3, 1.6, 2.8, 5.0, 7.2],
        "expected_phases": {"intro": ("white-bg-fade", 1.5), "build": ("gradient-fade-stagger", 2.5), "outro": ("full-frame-fade-out", 1.5)},
        "strict_gradient": True,
    },
    {
        "id": "w03-random-fly-fade",
        "title": "Random fly-in, final fade",
        "prompt": "First 1 second: background only. Then all layers fly into place over 2 seconds in random order. At the end the full frame fades out over the last 1 second.",
        "samples": [0.35, 1.3, 2.8, 6.9, 7.7],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "w04-top-down-fly-fade",
        "title": "Top-down fly-in order, final fade",
        "prompt": "First 1 second white background. Then all layers fly into place from top to bottom over 3 seconds. In the final 1 second the whole frame fades out.",
        "samples": [0.3, 1.4, 3.6, 6.7, 7.7],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 3.0), "outro": ("full-frame-fade-out", 1.0)},
        "expected_order": "top-down-by-role",
    },
    {
        "id": "w05-shatter-glass",
        "title": "Full-frame glass shatter",
        "prompt": "First 1 second background only. Then all layers fly into place over 2 seconds. During the last 2 seconds the full frame shatters like broken glass into pieces and fades out.",
        "samples": [0.4, 1.4, 2.8, 6.4, 7.5],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-shatter", 2.0)},
    },
    {
        "id": "w06-full-frame-drop-as-one",
        "title": "Full frame drops as one object",
        "prompt": "First 1 second white background only. Then all layers fly into place over 2 seconds. In the last 2 seconds the entire frame drops down as one whole object with gravity and fades out.",
        "samples": [0.4, 1.4, 2.8, 6.4, 7.5],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-drop", 2.0)},
    },
    {
        "id": "w07-layer-scatter-physics",
        "title": "Layers scatter/fall individually",
        "prompt": "First 1 second white background. Then all layers fly into place over 2 seconds. In the last 2 seconds layers scatter away individually and fall down with physics while fading out.",
        "samples": [0.4, 1.4, 2.8, 6.4, 7.5],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("layer-scatter-fall", 2.0)},
    },
    {
        "id": "w08-venetian-editorial",
        "title": "Venetian intro, editorial role build",
        "prompt": "First 0.5 seconds the background appears with venetian blinds. Then photos use parallax, text uses fade up lines from top to bottom, and buttons rise on position Y; the whole composition appears within 2 seconds. In the last 2 seconds layers scatter and fall down with physics while fading out.",
        "samples": [0.2, 0.8, 1.8, 6.6, 7.5],
        "expected_phases": {"intro": ("venetian-blinds-bg", 0.5), "build": ("advanced-composition-build", 1.5), "outro": ("layer-scatter-fall", 2.0)},
        "expected_subphases": {"photos": "parallax-photo", "text": "fade-up-lines", "buttons": "button-y-rise"},
    },
    {
        "id": "w09-tetris-build",
        "title": "Tetris build, full fade",
        "prompt": "First 1 second white background only. Then all layers build like tetris blocks over 3 seconds. At the end the whole frame fades out during the final 1 second.",
        "samples": [0.3, 1.5, 3.8, 6.7, 7.7],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("tetris-build", 3.0), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "w10-signal-scan-intro",
        "title": "Signal scan intro with fly build",
        "prompt": "First 1 second the white background appears with a clean signal scan reveal. Then all layers fly into place over 2 seconds. The whole frame fades out in the last 1 second.",
        "samples": [0.2, 1.3, 2.8, 6.8, 7.7],
        "expected_phases": {"intro": ("signal-scan-reveal", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "w11-glass-light-intro",
        "title": "Glass light sweep intro",
        "prompt": "First 1 second white background appears with a glass light sweep. Then all layers fly into place over 2 seconds. Fade out the whole frame over the final 1 second.",
        "samples": [0.2, 1.3, 2.8, 6.8, 7.7],
        "expected_phases": {"intro": ("glass-light-sweep", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "w12-pixel-snap-gradient",
        "title": "Pixel snap intro and gradient build",
        "prompt": "First 1 second: white background appears with pixel snap. Then all layers appear from top to bottom with soft gradient fade-in over 2 seconds, no flying. Final 1.5 seconds: the whole frame fades out.",
        "samples": [0.2, 1.2, 2.6, 6.2, 7.4],
        "expected_phases": {"intro": ("soft-pixel-snap", 1.0), "build": ("gradient-fade-stagger", 2.0), "outro": ("full-frame-fade-out", 1.5)},
        "strict_gradient": True,
    },
    {
        "id": "w13-camera-push",
        "title": "Camera push only",
        "prompt": "Add a slow camera push in over 3 seconds. Keep all layers exactly as designed, do not animate separate layers.",
        "samples": [0.0, 1.5, 3.0, 5.5, 7.5],
        "expected_phases": {"camera": ("camera-push", 8.0)},
        "camera_only": True,
    },
    {
        "id": "w14-camera-pan",
        "title": "Camera pan only",
        "prompt": "Add a slow whole-frame camera pan to the right over 3 seconds. Keep the layer design unchanged and do not animate individual layers.",
        "samples": [0.0, 1.5, 3.0, 5.5, 7.5],
        "expected_phases": {"camera": ("camera-pan", 8.0)},
        "camera_only": True,
    },
    {
        "id": "w15-camera-pull",
        "title": "Camera pull-back only",
        "prompt": "Add a slow whole-frame camera pull back over 3 seconds. Do not animate layers separately, preserve all text exactly.",
        "samples": [0.0, 1.5, 3.0, 5.5, 7.5],
        "expected_phases": {"camera": ("camera-pull", 8.0)},
        "camera_only": True,
    },
    {
        "id": "w16-ru-fade-no-drop",
        "title": "Russian fade-only outro with negated drop/shatter",
        "prompt": "Первые 1 секунда только белый фон. Потом все слои появляются сверху вниз градиентным фейд ин за 2 секунды, без залета. В конце весь фрейм фейдаут 2 секунды, без падения и без осколков.",
        "samples": [0.3, 1.3, 2.8, 6.1, 7.2],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("gradient-fade-stagger", 2.0), "outro": ("full-frame-fade-out", 2.0)},
        "strict_gradient": True,
    },
    {
        "id": "w17-ru-fly-shatter",
        "title": "Russian fly-in and shatter",
        "prompt": "Первые 1 секунда белый фон без элементов, потом все слои в течение 2 секунд влетают в случайном порядке, в конце последние 2 секунды весь фрейм разбивается на куски и уходит в фейд аут.",
        "samples": [0.3, 1.4, 2.8, 6.4, 7.5],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-shatter", 2.0)},
    },
    {
        "id": "w18-ru-full-drop",
        "title": "Russian full frame falls as one",
        "prompt": "Сначала 1 секунда белый фон. Потом все слои влетают на свои места за 2 секунды. В конце весь фрейм целиком падает вниз как один объект за последние 2 секунды и исчезает в фейд аут.",
        "samples": [0.3, 1.4, 2.8, 6.4, 7.5],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("random-fly-in-stagger", 2.0), "outro": ("full-frame-drop", 2.0)},
    },
    {
        "id": "w19-venetian-fly-fade",
        "title": "Venetian background, fly-in, clean fade",
        "prompt": "First 0.5 seconds use venetian blinds on the white background only. Then all layers fly into their places over 2.5 seconds in random order. Final 1 second: full-frame fade out, no shatter, no falling.",
        "samples": [0.2, 0.8, 2.5, 6.8, 7.7],
        "expected_phases": {"intro": ("venetian-blinds-bg", 0.5), "build": ("random-fly-in-stagger", 2.5), "outro": ("full-frame-fade-out", 1.0)},
    },
    {
        "id": "w20-minimal-fade",
        "title": "Minimal whole-frame fade in/out",
        "prompt": "Make it minimal: white background fades in for 1 second, all layers softly fade in from top to bottom over 2 seconds with no movement, and the whole frame fades out in the final 1 second.",
        "samples": [0.2, 1.2, 2.6, 6.8, 7.7],
        "expected_phases": {"intro": ("white-bg-fade", 1.0), "build": ("gradient-fade-stagger", 2.0), "outro": ("full-frame-fade-out", 1.0)},
        "strict_gradient": True,
    },
]


def valid_text_assets() -> list[str]:
    records = json.loads((ROOT / "app/static/assets/figma-plugin/assets.json").read_text(encoding="utf-8"))
    items = list(records.values()) if isinstance(records, dict) else list(records)
    assets: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "")
        if not asset_id:
            continue
        if not (ROOT / "app/static/assets/figma-plugin" / str(item.get("asset_file") or f"{asset_id}.png")).exists():
            continue
        if len(source_texts(list(item.get("figma_layers") or []))) > 0:
            assets.append(asset_id)
    return sorted(assets)


def camera_layer_issues(layers: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    recipes = []
    for layer in layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if recipe:
            recipes.append((layer, recipe))
    if len(recipes) != 1:
        issues.append(f"camera-only expected 1 camera controller recipe, got {len(recipes)}")
    for layer, recipe in recipes:
        tags = {str(tag) for tag in list(recipe.get("tags") or [])}
        if "scene-camera" not in tags or "camera-controller" not in tags:
            issues.append(f"camera-only added non-camera recipe to {layer.get('id')}")
    return issues


def order_issues(prompt_spec: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    expected = prompt_spec.get("expected_order")
    if not expected:
        return []
    build = phase_map(plan).get("build") or {}
    actual = build.get("order")
    return [] if actual == expected else [f"build order {actual} != {expected}"]


def build_contact_sheet(cases: list[dict[str, Any]], out_dir: Path) -> Path:
    label_w = 420
    thumb_w = 190
    thumb_h = 107
    row_h = 150
    cols = 5
    height = 56 + len(cases) * row_h
    sheet = Image.new("RGB", (label_w + cols * thumb_w + 34, height), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    draw.text((18, 16), "20 prompts x 3 random Figma frames QA", fill=(18, 18, 18), font=font(22, True))
    small = font(10)
    label = font(13, True)
    y = 56
    for case in cases:
        color = (28, 128, 68) if case["status"] == "pass" else (174, 54, 34)
        draw.rounded_rectangle((10, y + 6, sheet.width - 10, y + row_h - 8), radius=8, fill=(255, 255, 255), outline=(218, 214, 204))
        draw.text((22, y + 18), f"{case['id']} {case['status'].upper()}", fill=color, font=label)
        draw.text((22, y + 38), f"{case['asset_id']} / {case['prompt_id']}", fill=(20, 20, 20), font=small)
        draw.text((22, y + 56), case.get("title", "")[:88], fill=(72, 72, 72), font=small)
        message = "; ".join(case.get("issues") or []) or case.get("summary", "")
        draw.text((22, y + 76), message[:105], fill=(150, 45, 30) if case.get("issues") else (72, 72, 72), font=small)
        for index, frame_path in enumerate(case.get("frames", [])[:cols]):
            x = label_w + 12 + index * thumb_w
            try:
                with Image.open(frame_path).convert("RGB") as frame:
                    frame.thumbnail((thumb_w - 12, thumb_h), Image.Resampling.LANCZOS)
                    bg = Image.new("RGB", (thumb_w - 12, thumb_h), (18, 18, 18))
                    bg.paste(frame, ((bg.width - frame.width) // 2, (bg.height - frame.height) // 2))
                    sheet.paste(bg, (x, y + 19))
            except Exception:
                draw.rectangle((x, y + 19, x + thumb_w - 12, y + 19 + thumb_h), fill=(210, 210, 210))
            sample = case.get("samples", [])[index] if index < len(case.get("samples", [])) else ""
            draw.text((x + 4, y + 19 + thumb_h + 4), f"t={sample}s", fill=(65, 65, 65), font=small)
        y += row_h
    target = out_dir / "motion_20prompts_wave_contact_sheet.png"
    sheet.save(target)
    return target


def write_report(cases: list[dict[str, Any]], out_dir: Path, contact_sheet: Path) -> Path:
    report = out_dir / "REPORT.md"
    lines = [
        "# 20 Prompts x 3 Random Figma Frames QA",
        "",
        f"- Total: `{len(cases)}`, pass: `{sum(1 for c in cases if c['status'] == 'pass')}`, fail: `{sum(1 for c in cases if c['status'] != 'pass')}`",
        f"- Contact sheet: `{contact_sheet}`",
        "",
        f"![contact sheet]({contact_sheet.as_posix()})",
        "",
        "## Prompts",
        "",
    ]
    for prompt in PROMPTS:
        lines.append(f"- `{prompt['id']}`: {prompt['prompt']}")
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
    parser.add_argument("--seed", type=int, default=20260518 + 20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--fps", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    all_assets = valid_text_assets()
    if not all_assets:
        raise SystemExit("No text-bearing Figma assets found")
    rng = random.Random(args.seed)
    assignments = {prompt["id"]: [rng.choice(all_assets) for _ in range(args.repeats)] for prompt in PROMPTS}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-20prompts-wave-{stamp}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_cache: dict[str, Any] = {}
    cases: list[dict[str, Any]] = []

    for prompt_spec in PROMPTS:
        for repeat_index, asset_id in enumerate(assignments[prompt_spec["id"]], start=1):
            asset_work_dir = out_dir / "work" / asset_id
            if asset_id not in work_cache:
                if asset_work_dir.exists():
                    shutil.rmtree(asset_work_dir)
                asset_work_dir.mkdir(parents=True, exist_ok=True)
                work_cache[asset_id] = motion_from_plugin_asset(asset_work_dir, asset_id, start=0, duration=8.0)
            source_motion = work_cache[asset_id]
            case_id = f"{prompt_spec['id']}-r{repeat_index}-{asset_id}"
            case = {
                "id": case_id,
                "asset_id": asset_id,
                "prompt_id": prompt_spec["id"],
                "title": prompt_spec["title"],
                "prompt": prompt_spec["prompt"],
                "samples": list(prompt_spec["samples"]),
                "source_asset_path": str(source_motion.asset_path),
                "text_layer_count": len(source_texts(list(source_motion.figma_layers or []))),
            }
            issues: list[str] = []
            try:
                planned_layers, plan = plan_motion(source_motion, prompt_spec["prompt"])
                issues.extend(assert_phase_contract(prompt_spec, plan))
                issues.extend(order_issues(prompt_spec, plan))
                issues.extend(text_transform_issues(prompt_spec, planned_layers))
                if prompt_spec.get("strict_gradient"):
                    issues.extend(strict_gradient_issues(planned_layers, plan))
                if prompt_spec.get("camera_only"):
                    issues.extend(camera_layer_issues(planned_layers))
                motion = source_motion.model_copy(
                    update={
                        "id": f"qa20-{prompt_spec['id']}-r{repeat_index}-{asset_id}",
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
                checks = {str(item.get("id")): item for item in list(visual_report.get("checks") or []) if isinstance(item, dict)}
                rendered = checks.get("rendered_frames") or {}
                if rendered.get("status") not in {"pass", None}:
                    issues.append(f"rendered_frames check {rendered.get('status')}")
                if prompt_spec.get("camera_only"):
                    before_text = source_texts(list(source_motion.figma_layers or []))
                    after_text = source_texts(planned_layers)
                    changed = [key for key, value in before_text.items() if after_text.get(key) != value]
                    if changed:
                        issues.append(f"figma text layer content changed: {changed[:8]}")
                else:
                    issues.extend(assert_text_integrity(source_motion, planned_layers, visual_report, first_frame_diff))
                case["summary"] = ", ".join(f"{phase.get('id')}:{phase.get('preset')} {phase.get('duration')}s" for phase in list(plan.get("phases") or []))
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

            for path in (asset_work_dir / "assets").glob(f"qa20-{prompt_spec['id']}-r{repeat_index}-{asset_id}*"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.suffix.lower() in {".mp4", ".json"} or "_frames_" in path.name:
                    path.unlink(missing_ok=True)

    contact_sheet = build_contact_sheet(cases, out_dir)
    report = write_report(cases, out_dir, contact_sheet)
    hold_values = [float(case["hold_diff"]) for case in cases if case.get("hold_diff") is not None]
    first_values = [float(case["first_frame_diff"]) for case in cases if case.get("first_frame_diff") is not None]
    summary = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "seed": args.seed,
        "assignments": assignments,
        "out_dir": str(out_dir),
        "report": str(report),
        "contact_sheet": str(contact_sheet),
        "total": len(cases),
        "pass": sum(1 for case in cases if case["status"] == "pass"),
        "fail": sum(1 for case in cases if case["status"] != "pass"),
        "max_hold_diff": max(hold_values) if hold_values else None,
        "max_first_frame_diff": max(first_values) if first_values else None,
        "cases": cases,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: summary[key] for key in ("status", "out_dir", "report", "contact_sheet", "total", "pass", "fail", "max_hold_diff", "max_first_frame_diff")},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
