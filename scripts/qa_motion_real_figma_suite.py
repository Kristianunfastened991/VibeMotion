from __future__ import annotations

import argparse
import json
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
from app.services.motion import render_motion_video_asset
from app.services.motion_intent import attach_frame_motion_contract, build_layer_motion_recipe_from_prompt


USER_ADVANCED_PROMPT = (
    "\u0424\u043e\u043d\u043e\u0432\u044b\u0439 \u0441\u043b\u043e\u0439 \u043f\u043e\u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 Venetian Blinds "
    "(\u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u0430\u043d\u0438\u043c\u0430\u0446\u0438\u0438 - 0,5\u0441\u0435\u043a), "
    "\u0437\u0430\u0442\u0435\u043c \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 \u043f\u043e\u044f\u0432\u043b\u044f\u044e\u0442\u0441\u044f "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 \u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441\u0430, "
    "\u043f\u043e\u0441\u043b\u0435 \u0447\u0435\u0433\u043e \u043f\u043e\u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0442\u0435\u043a\u0441\u0442 "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 fade up lines "
    "(\u0441\u043d\u0430\u0447\u0430\u043b\u0430 \u0433\u043b\u0430\u0432\u043d\u044b\u0439 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a, "
    "\u0437\u0430\u0442\u0435\u043c \u0432\u0441\u0435 \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u044b\u0435 \u0447\u0430\u0441\u0442\u0438 \u0442\u0435\u043a\u0441\u0442\u0430 - "
    "\u0430\u043d\u0438\u043c\u0430\u0446\u0438\u044f \u043f\u043e\u044f\u0432\u043b\u0435\u043d\u0438\u044f \u043f\u0440\u043e\u0438\u0441\u0445\u043e\u0434\u0438\u0442 "
    "\u0432 \u043f\u043e\u0440\u044f\u0434\u043a\u0435 \u00ab\u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437\u00bb), "
    "\u0437\u0430\u0442\u0435\u043c \u043f\u043e\u044f\u0432\u043b\u044f\u044e\u0442\u0441\u044f \u0447\u0435\u0440\u043d\u044b\u0435 \u043a\u043d\u043e\u043f\u043a\u0438 "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 \u0432\u044b\u043b\u0435\u0442\u0430 \u043f\u043e position Y "
    "\u0441\u043d\u0438\u0437\u0443 \u0432\u0432\u0435\u0440\u0445 + \u043b\u0435\u0433\u043a\u0438\u0439 fade in. "
    "\u0412\u0441\u044f \u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0434\u043e\u043b\u0436\u043d\u0430 "
    "\u043f\u043e\u044f\u0432\u0438\u0442\u044c\u0441\u044f (\u0438 \u0432\u0441\u0435 \u0430\u043d\u0438\u043c\u0430\u0446\u0438\u0438 "
    "\u0434\u043e\u043b\u0436\u043d\u044b \u043f\u0440\u043e\u0438\u0437\u043e\u0439\u0442\u0438) \u0437\u0430 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b. "
    "\u0410\u043d\u0438\u043c\u0430\u0446\u0438\u044f \u0438\u0441\u0447\u0435\u0437\u0430\u043d\u0438\u044f \u0432\u0441\u0435\u0439 "
    "\u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\u0438\u0438 \u043f\u0443\u0441\u0442\u044c \u043f\u0440\u043e\u0438\u0441\u0445\u043e\u0434\u0438\u0442 "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 \u00ab\u0440\u0430\u0441\u0441\u044b\u043f\u0430\u043d\u0438\u044f\u00bb "
    "\u0441\u043b\u043e\u0435\u0432 \u0438 \u043e\u043f\u0430\u0434\u0430\u043d\u0438\u044f \u0438\u0445 \u0432\u043d\u0438\u0437 "
    "\u0441 \u0443\u0447\u0435\u0442\u043e\u043c \u0437\u0430\u043a\u043e\u043d\u043e\u0432 \u0444\u0438\u0437\u0438\u043a\u0438"
)

SELECTED_LAYER_PROMPTS = [
    ("fade_2s", "\u0421\u0434\u0435\u043b\u0430\u0439 \u0444\u0435\u0439\u0434\u0438\u043d \u0432\u043d\u0430\u0447\u0430\u043b\u0435 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c\u044e 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b"),
    ("append_drop", "\u0447\u0435\u0440\u0435\u0437 \u0441\u0435\u043a\u0443\u043d\u0434\u0443 \u043f\u043e\u0441\u043b\u0435 fade in \u0432\u0435\u0441\u044c \u0431\u043b\u043e\u043a \u043f\u0430\u0434\u0430\u0435\u0442 \u0432\u043d\u0438\u0437 \u043a\u0430\u043a \u043a\u0430\u043c\u0435\u043d\u044c"),
]

REAL_FRAME_IDS = ["12-247", "136-242", "263-363"]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_source(motion: Any, project_root: Path) -> Image.Image:
    return Image.open(project_root / str(motion.asset_path)).convert("RGB").resize((motion.width, motion.height), Image.Resampling.LANCZOS)


def extract_frame(video: Path, time_value: float, output: Path) -> None:
    run(["ffmpeg", "-y", "-ss", f"{time_value:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])


def mean_diff(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def pick_representative_layer(layers: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        layer
        for layer in layers
        if layer.get("visible") is not False
        and not str(layer.get("id") or "").startswith("__")
        and str(layer.get("node_type") or "").upper() != "FRAME"
        and layer.get("asset_path")
    ]
    for kind in ("text", "image", "shape"):
        for layer in candidates:
            if layer.get("kind") == kind:
                return layer
    raise RuntimeError("No representative layer with asset_path found")


def apply_whole_frame_prompt(motion: Any) -> Any:
    if not should_use_frame_choreography_prompt(USER_ADVANCED_PROMPT):
        raise AssertionError("advanced user prompt did not route to whole-frame choreography")
    layers = plan_frame_choreography(USER_ADVANCED_PROMPT, motion)
    plan = describe_motion_plan(layers)
    layers = attach_frame_motion_contract(USER_ADVANCED_PROMPT, str(motion.id), layers, plan or {})
    return motion.model_copy(update={"figma_layers": layers, "motion_plan": describe_motion_plan(layers)})


def apply_selected_layer_stack(motion: Any) -> tuple[Any, dict[str, Any]]:
    layers = [dict(layer) for layer in list(motion.figma_layers or [])]
    target = pick_representative_layer(layers)
    recipe = None
    for index, (_case_id, prompt) in enumerate(SELECTED_LAYER_PROMPTS):
        mode = "replace" if index == 0 else "append"
        recipe = build_layer_motion_recipe_from_prompt(prompt, mode, target, layers, timeline_duration=float(motion.duration))
        target["motion_recipe"] = recipe
    next_layers = [target if str(layer.get("id") or "") == str(target.get("id") or "") else layer for layer in layers]
    return motion.model_copy(update={"figma_layers": next_layers}), {"target": target, "recipe": recipe}


def fit_preview(image: Image.Image, width: int = 360, height: int = 220) -> Image.Image:
    canvas = Image.new("RGB", (width, height), "white")
    copy = image.copy()
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas.paste(copy, ((width - copy.width) // 2, (height - copy.height) // 2))
    return canvas


def build_contact_sheet(rows: list[dict[str, Any]], output: Path) -> None:
    cell_w, cell_h = 360, 248
    labels = ["source", "mid", "settled", "outro", "diff x5"]
    sheet = Image.new("RGB", (cell_w * len(labels), cell_h * len(rows)), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    for row_index, row in enumerate(rows):
        y = row_index * cell_h
        for col_index, label in enumerate(labels):
            x = col_index * cell_w
            image = row.get(label)
            if image is not None:
                sheet.paste(fit_preview(image, cell_w, cell_h - 28), (x, y + 24))
            draw.text((x + 8, y + 5), f"{row['case_id']} | {label}", fill=(0, 0, 0), font=font)
    sheet.save(output)


def run_case(asset_id: str, mode: str, output_dir: Path, fps: int) -> dict[str, Any]:
    case_id = f"{asset_id}-{mode}"
    work_dir = output_dir / "work" / case_id
    work_dir.mkdir(parents=True, exist_ok=True)
    motion = motion_from_plugin_asset(work_dir, asset_id, start=0, duration=8.0)
    if mode == "whole":
        motion = apply_whole_frame_prompt(motion)
        plan = motion.motion_plan or {}
        settled_time = 2.25
        mid_time = 1.20
        outro_time = 6.60
    else:
        motion, selected_info = apply_selected_layer_stack(motion)
        plan = (selected_info["recipe"] or {}).get("phase_plan") or {}
        settled_time = 2.25
        mid_time = 1.00
        outro_time = 4.30
    video = render_motion_video_asset(motion, work_dir / "assets", fps=fps)
    if video is None or not video.exists():
        raise RuntimeError(f"{case_id}: motion video was not rendered")
    source = load_source(motion, work_dir)
    frame_paths: dict[str, Path] = {}
    for label, time_value in [("mid", mid_time), ("settled", settled_time), ("outro", outro_time)]:
        frame_path = output_dir / f"{case_id}-{label}.jpg"
        extract_frame(video, time_value, frame_path)
        frame_paths[label] = frame_path
    settled = Image.open(frame_paths["settled"]).convert("RGB").resize(source.size, Image.Resampling.LANCZOS)
    diff_value = mean_diff(source, settled)
    diff_image = ImageChops.difference(source, settled).point(lambda value: min(255, value * 5)).convert("RGB")
    return {
        "case_id": case_id,
        "asset_id": asset_id,
        "mode": mode,
        "status": "pass" if diff_value <= 6.0 else "fail",
        "settled_mean_abs_diff": round(diff_value, 3),
        "video": str(video),
        "plan": plan,
        "source": source,
        "mid": Image.open(frame_paths["mid"]).convert("RGB"),
        "settled": settled,
        "outro": Image.open(frame_paths["outro"]).convert("RGB"),
        "diff x5": diff_image,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--fps", type=int, default=6)
    parser.add_argument("--assets", nargs="*", default=REAL_FRAME_IDS)
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-real-figma-suite-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    for asset_id in args.assets:
        for mode in ("whole", "selected"):
            row = run_case(asset_id, mode, output_dir, max(1, int(args.fps)))
            rows.append(row)
            report_rows.append({key: value for key, value in row.items() if key not in {"source", "mid", "settled", "outro", "diff x5"}})

    contact_sheet = output_dir / "real_figma_contact_sheet.jpg"
    build_contact_sheet(rows, contact_sheet)
    summary = {
        "status": "pass" if all(row["status"] == "pass" for row in report_rows) else "fail",
        "prompt": USER_ADVANCED_PROMPT,
        "frames": args.assets,
        "cases": report_rows,
        "contact_sheet": str(contact_sheet),
    }
    (output_dir / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Real Figma Motion Suite",
        "",
        f"status: {summary['status']}",
        f"contact_sheet: {contact_sheet}",
        "",
    ]
    for row in report_rows:
        lines.append(
            f"- {row['case_id']}: {row['status']}; settled_mean_abs_diff={row['settled_mean_abs_diff']}; video={row['video']}"
        )
    (output_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "output_dir": str(output_dir), "contact_sheet": str(contact_sheet)}, ensure_ascii=False))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
