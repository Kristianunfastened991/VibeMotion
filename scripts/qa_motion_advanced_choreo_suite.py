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

from app.models.schemas import MotionSpec
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography, should_use_frame_choreography_prompt
from app.services.motion import render_motion_video_asset
from app.services.motion_intent import attach_frame_motion_contract


USER_PROMPT = (
    "\u0424\u043e\u043d\u043e\u0432\u044b\u0439 \u0441\u043b\u043e\u0439 \u043f\u043e\u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 Venetian Blinds "
    "(\u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u0430\u043d\u0438\u043c\u0430\u0446\u0438\u0438 - 0,5\u0441\u0435\u043a), "
    "\u0437\u0430\u0442\u0435\u043c \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 \u043f\u043e\u044f\u0432\u043b\u044f\u044e\u0442\u0441\u044f "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 \u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441\u0430, "
    "\u043f\u043e\u0441\u043b\u0435 \u0447\u0435\u0433\u043e \u043f\u043e\u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0442\u0435\u043a\u0441\u0442 "
    "\u0447\u0435\u0440\u0435\u0437 \u044d\u0444\u0444\u0435\u043a\u0442 fade up lines "
    "(\u0441\u043d\u0430\u0447\u0430\u043b\u0430 \u0433\u043b\u0430\u0432\u043d\u044b\u0439 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a, "
    "\u0437\u0430\u0442\u0435\u043c \u043e\u0441\u0442\u0430\u043b\u044c\u043d\u043e\u0439 \u0442\u0435\u043a\u0441\u0442, "
    "\u043f\u043e\u0440\u044f\u0434\u043e\u043a \u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437), "
    "\u0437\u0430\u0442\u0435\u043c \u0447\u0435\u0440\u043d\u044b\u0435 \u043a\u043d\u043e\u043f\u043a\u0438 \u0432\u044b\u043b\u0435\u0442\u0430\u044e\u0442 "
    "\u043f\u043e position Y \u0441\u043d\u0438\u0437\u0443 \u0432\u0432\u0435\u0440\u0445 + \u043b\u0435\u0433\u043a\u0438\u0439 fade in. "
    "\u0412\u0441\u044f \u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0434\u043e\u043b\u0436\u043d\u0430 "
    "\u043f\u043e\u044f\u0432\u0438\u0442\u044c\u0441\u044f \u0437\u0430 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b. "
    "\u0418\u0441\u0447\u0435\u0437\u0430\u043d\u0438\u0435 \u0447\u0435\u0440\u0435\u0437 \u0440\u0430\u0441\u0441\u044b\u043f\u0430\u043d\u0438\u0435 "
    "\u0441\u043b\u043e\u0435\u0432 \u0438 \u043e\u043f\u0430\u0434\u0430\u043d\u0438\u0435 \u0432\u043d\u0438\u0437 \u0441 \u0444\u0438\u0437\u0438\u043a\u043e\u0439."
)

PROMPTS = [
    {"id": "user-venetian-2s", "prompt": USER_PROMPT, "intro": 0.5, "appearance": 2.0},
    {
        "id": "en-depth-parallax-24s",
        "prompt": "Background reveals with Venetian Blinds for 0.6 seconds, then photos appear with depth parallax, then headline and body text use fade up lines top down, then black CTA buttons rise on position Y with a soft fade in. The whole composition appears in 2.4 seconds. The outro scatters all layers and they fall down with physics while fading out.",
        "intro": 0.6,
        "appearance": 2.4,
    },
    {
        "id": "ru-fast-venetian-2s",
        "prompt": "\u0424\u043e\u043d \u0447\u0435\u0440\u0435\u0437 \u0436\u0430\u043b\u044e\u0437\u0438 0,4\u0441\u0435\u043a, \u0437\u0430\u0442\u0435\u043c \u0444\u043e\u0442\u043e \u0441 \u043f\u0430\u0440\u0430\u043b\u043b\u0430\u043a\u0441 zoom, \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a \u0438 \u0442\u0435\u043a\u0441\u0442 fade up \u0441\u0432\u0435\u0440\u0445\u0443 \u0432\u043d\u0438\u0437, \u043a\u043d\u043e\u043f\u043a\u0438 \u0441\u043d\u0438\u0437\u0443 \u0432\u0432\u0435\u0440\u0445, \u0432\u0441\u044f \u043a\u043e\u043c\u043f\u043e\u0437\u0438\u0446\u0438\u044f \u0437\u0430 2 \u0441\u0435\u043a\u0443\u043d\u0434\u044b, \u0444\u0438\u043d\u0430\u043b: \u0441\u043b\u043e\u0438 \u0440\u0430\u0441\u0441\u044b\u043f\u0430\u044e\u0442\u0441\u044f \u0438 \u043f\u0430\u0434\u0430\u044e\u0442 \u0432\u043d\u0438\u0437.",
        "intro": 0.4,
        "appearance": 2.0,
    },
    {
        "id": "en-ordered-ui-2s",
        "prompt": "First reveal the background with Venetian Blinds in 0.5 sec, photos parallax from depth, then title and all text fade up lines from top to bottom, then buttons fly upward on position Y with fade in. All animations finish in 2 seconds. At the end the composition scatters into separate layers and falls with gravity.",
        "intro": 0.5,
        "appearance": 2.0,
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


def make_photo(path: Path, size: tuple[int, int], color_a: tuple[int, int, int], color_b: tuple[int, int, int], label: str) -> None:
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        p = y / max(1, size[1] - 1)
        color = tuple(int(color_a[i] + (color_b[i] - color_a[i]) * p) for i in range(3))
        draw.line((0, y, size[0], y), fill=(*color, 255))
    draw.ellipse((size[0] * 0.18, size[1] * 0.18, size[0] * 0.88, size[1] * 0.92), fill=(255, 255, 255, 42))
    draw.text((18, size[1] - 42), label, font=font(24, True), fill=(255, 255, 255, 235))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def rect(layer: dict[str, Any]) -> tuple[int, int, int, int]:
    x = int(round(float(layer["x"])))
    y = int(round(float(layer["y"])))
    return x, y, x + int(round(float(layer["width"]))), y + int(round(float(layer["height"])))


def draw_source(spec: MotionSpec, project_root: Path) -> None:
    image = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for layer in spec.figma_layers:
        if layer.get("visible") is False or str(layer.get("node_type") or "").upper() == "FRAME":
            continue
        if layer.get("kind") == "shape":
            fill = str(layer.get("fill") or "rgba(255,255,255,1)")
            if "0,0,0" in fill or "0, 0, 0" in fill:
                color = (0, 0, 0, int(float(layer.get("opacity", 1) or 1) * 255))
            elif "#eef" in fill:
                color = (232, 238, 255, 255)
            elif "#f7" in fill:
                color = (247, 247, 244, 255)
            else:
                color = (255, 255, 255, 255)
            draw.rounded_rectangle(rect(layer), radius=int(layer.get("radius") or 0), fill=color)
        elif layer.get("kind") == "image" and layer.get("asset_path"):
            with Image.open(project_root / str(layer["asset_path"])).convert("RGBA") as child:
                image.alpha_composite(child.resize((int(layer["width"]), int(layer["height"])), Image.Resampling.LANCZOS), (int(layer["x"]), int(layer["y"])))
        elif layer.get("kind") == "text":
            size = int(layer.get("font_size") or 24)
            draw.multiline_text((int(layer["x"]), int(layer["y"])), str(layer.get("text") or ""), font=font(size, size > 34), fill=(0, 0, 0, 255), spacing=max(2, size // 5))
    asset = project_root / str(spec.asset_path)
    asset.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(asset)


def make_frame(project_root: Path, case_id: str, width: int, height: int, background: str) -> MotionSpec:
    frame_dir = Path("assets") / "qa-advanced" / case_id
    photo_a = frame_dir / "photo-a.png"
    photo_b = frame_dir / "photo-b.png"
    make_photo(project_root / photo_a, (360, 430), (72, 115, 196), (236, 109, 87), "PHOTO A")
    make_photo(project_root / photo_b, (300, 260), (50, 165, 122), (255, 210, 94), "PHOTO B")
    layers: list[dict[str, Any]] = [
        {"id": f"{case_id}:frame", "name": "Synthetic frame", "kind": "shape", "node_type": "FRAME", "x": 0, "y": 0, "width": width, "height": height, "fill": "rgba(0,0,0,0)", "visible": True},
        {"id": f"{case_id}:bg", "name": "Background", "kind": "shape", "node_type": "RECTANGLE", "x": 0, "y": 0, "width": width, "height": height, "fill": background, "visible": True},
        {"id": f"{case_id}:photo1", "name": "Hero photo", "kind": "image", "node_type": "RECTANGLE", "x": int(width * 0.56), "y": int(height * 0.10), "width": int(width * 0.32), "height": int(height * 0.62), "radius": 20, "asset_path": str(photo_a), "visible": True},
        {"id": f"{case_id}:photo2", "name": "Secondary photo", "kind": "image", "node_type": "RECTANGLE", "x": int(width * 0.48), "y": int(height * 0.38), "width": int(width * 0.22), "height": int(height * 0.28), "radius": 22, "asset_path": str(photo_b), "visible": True},
        {"id": f"{case_id}:title", "name": "Headline", "kind": "text", "node_type": "TEXT", "x": int(width * 0.08), "y": int(height * 0.11), "width": int(width * 0.42), "height": int(height * 0.11), "font_size": max(28, int(width * 0.045)), "line_height": max(32, int(width * 0.05)), "text": "Hey creator!", "visible": True},
        {"id": f"{case_id}:body", "name": "Body copy", "kind": "text", "node_type": "TEXT", "x": int(width * 0.08), "y": int(height * 0.26), "width": int(width * 0.42), "height": int(height * 0.26), "font_size": max(12, int(width * 0.017)), "line_height": max(15, int(width * 0.022)), "text": "Every setup is designed to save hours of trial and error.\\nUse these looks to build a clean motion composition.", "visible": True},
        {"id": f"{case_id}:how", "name": "How It Works", "kind": "text", "node_type": "TEXT", "x": int(width * 0.08), "y": int(height * 0.58), "width": int(width * 0.36), "height": int(height * 0.08), "font_size": max(20, int(width * 0.029)), "line_height": max(24, int(width * 0.034)), "text": "How It Works", "visible": True},
        {"id": f"{case_id}:steps", "name": "Steps", "kind": "text", "node_type": "TEXT", "x": int(width * 0.08), "y": int(height * 0.68), "width": int(width * 0.42), "height": int(height * 0.15), "font_size": max(12, int(width * 0.016)), "line_height": max(15, int(width * 0.021)), "text": "1. Select a recipe\\n2. Copy the prompt\\n3. Experiment with the result", "visible": True},
        {"id": f"{case_id}:button1", "name": "Follow button", "kind": "shape", "node_type": "RECTANGLE", "x": int(width * 0.08), "y": int(height * 0.88), "width": int(width * 0.18), "height": max(24, int(height * 0.055)), "radius": 18, "fill": "rgba(0,0,0,1)", "visible": True},
        {"id": f"{case_id}:button1_text", "name": "Follow text", "kind": "text", "node_type": "TEXT", "x": int(width * 0.105), "y": int(height * 0.895), "width": int(width * 0.13), "height": 24, "font_size": max(10, int(width * 0.012)), "line_height": 16, "text": "Follow for more", "color": "rgba(255,255,255,1)", "visible": True},
        {"id": f"{case_id}:button2", "name": "Handle button", "kind": "shape", "node_type": "RECTANGLE", "x": int(width * 0.30), "y": int(height * 0.88), "width": int(width * 0.16), "height": max(24, int(height * 0.055)), "radius": 18, "fill": "rgba(0,0,0,1)", "visible": True},
        {"id": f"{case_id}:button2_text", "name": "Handle text", "kind": "text", "node_type": "TEXT", "x": int(width * 0.33), "y": int(height * 0.895), "width": int(width * 0.10), "height": 24, "font_size": max(10, int(width * 0.012)), "line_height": 16, "text": "@Creator", "color": "rgba(255,255,255,1)", "visible": True},
    ]
    spec = MotionSpec(
        id=f"qa-advanced-{case_id}",
        text="",
        start=0,
        duration=6.0,
        x=0,
        y=0,
        width=width,
        height=height,
        source_type="figma",
        asset_path=str(frame_dir / "source.png"),
        figma_node_id=f"{case_id}:frame",
        figma_layers=layers,
    )
    draw_source(spec, project_root)
    return spec


def mean_diff(a: Image.Image, b: Image.Image) -> float:
    stat = ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB")))
    return float(sum(stat.mean) / max(1, len(stat.mean)))


def extract_frame(video: Path, seconds: float, target: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(target)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_contact_sheet(paths: list[Path], target: Path) -> None:
    thumbs = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((360, 220))
        tile = Image.new("RGB", (360, 250), "white")
        tile.paste(image, ((360 - image.width) // 2, 0))
        ImageDraw.Draw(tile).text((8, 226), path.stem, font=font(12), fill=(0, 0, 0))
        thumbs.append(tile)
    sheet = Image.new("RGB", (720, ((len(thumbs) + 1) // 2) * 250), (240, 240, 240))
    for index, tile in enumerate(thumbs):
        sheet.paste(tile, ((index % 2) * 360, (index // 2) * 250))
    sheet.save(target)


def run_case(base_spec: MotionSpec, prompt_case: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    prompt = str(prompt_case["prompt"])
    case_id = f"{base_spec.id}-{prompt_case['id']}"
    issues: list[str] = []
    if not should_use_frame_choreography_prompt(prompt):
        issues.append("prompt did not route to frame choreography")
    planned_layers = plan_frame_choreography(prompt, base_spec)
    plan = describe_motion_plan(planned_layers)
    if not isinstance(plan, dict):
        issues.append("missing phase plan")
        plan = {}
    layers = attach_frame_motion_contract(prompt, base_spec.id, planned_layers, plan)
    phases = {str(phase.get("id")): phase for phase in list(plan.get("phases") or []) if isinstance(phase, dict)}
    intro = phases.get("intro") or {}
    build = phases.get("build") or {}
    outro = phases.get("outro") or {}
    expected_intro = float(prompt_case["intro"])
    expected_appearance = float(prompt_case["appearance"])
    actual_intro = float(intro.get("duration") or 0)
    actual_build_start = float(build.get("start") or 0)
    actual_build_duration = float(build.get("duration") or 0)
    if abs(actual_intro - expected_intro) > 0.035:
        issues.append(f"intro duration {actual_intro:.3f}s != {expected_intro:.3f}s")
    if abs((actual_build_start + actual_build_duration) - expected_appearance) > 0.06:
        issues.append(f"appearance end {actual_build_start + actual_build_duration:.3f}s != {expected_appearance:.3f}s")
    if intro.get("preset") != "venetian-blinds-bg":
        issues.append(f"intro preset {intro.get('preset')} != venetian-blinds-bg")
    if build.get("preset") != "advanced-composition-build":
        issues.append(f"build preset {build.get('preset')} != advanced-composition-build")
    if outro.get("preset") != "layer-scatter-fall":
        issues.append(f"outro preset {outro.get('preset')} != layer-scatter-fall")
    recipes = [layer.get("motion_recipe") for layer in layers if isinstance(layer.get("motion_recipe"), dict)]
    presets = sorted({str(recipe.get("preset")) for recipe in recipes})
    for required in ["venetian-blinds-bg", "parallax-photo", "fade-up-lines", "button-y-rise"]:
        if required not in presets:
            issues.append(f"missing preset {required}")
    for recipe in recipes:
        frames = [frame for frame in list((recipe.get("motion_dsl") or {}).get("keyframes") or []) if isinstance(frame, dict)]
        times = [float(frame.get("time") or 0) for frame in frames]
        if times != sorted(times):
            issues.append(f"non-monotonic keyframes in {recipe.get('id')}")
        for frame in frames:
            if abs(float(frame.get("scaleX", 1) or 1) - 1) > 0.001 or abs(float(frame.get("scaleY", 1) or 1) - 1) > 0.001:
                issues.append(f"unexpected non-uniform scale in {recipe.get('id')}")
                break
    spec = base_spec.model_copy(
        update={
            "id": case_id,
            "figma_layers": layers,
            "motion_plan": plan,
            "motion_units": [],
            "video_asset_path": None,
            "asset_version": None,
            "prompt": f"QA advanced choreography: {prompt_case['id']}",
        }
    )
    video = render_motion_video_asset(spec, out_dir / "assets", fps=8)
    copied_video = None
    frames: list[Path] = []
    fidelity = None
    if video and video.exists():
        copied_video = out_dir / f"{case_id}.mp4"
        shutil.copy2(video, copied_video)
        frame_dir = out_dir / case_id
        frame_dir.mkdir(parents=True, exist_ok=True)
        for seconds in [0.1, expected_intro * 0.75, min(expected_appearance - 0.15, expected_intro + 0.6), expected_appearance + 0.25, 5.2]:
            target = frame_dir / f"{case_id}_{seconds:.2f}s.jpg"
            extract_frame(video, max(0.0, seconds), target)
            frames.append(target)
        source = Image.open(out_dir / str(base_spec.asset_path)).convert("RGB")
        settled = Image.open(frames[3]).convert("RGB").resize(source.size)
        fidelity = round(mean_diff(source, settled), 3)
        if fidelity > 5.0:
            issues.append(f"settled source fidelity diff too high: {fidelity}")
    else:
        issues.append("render did not produce video")
    return {
        "id": case_id,
        "status": "pass" if not issues else "fail",
        "issues": issues,
        "plan": plan,
        "presets": presets,
        "settled_mean_abs_diff": fidelity,
        "video": str(copied_video) if copied_video else None,
        "frames": [str(path) for path in frames],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    out_dir = Path(args.out) if args.out else ROOT / "qa_artifacts" / f"motion-advanced-choreo-suite-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        make_frame(out_dir, "wide", 960, 540, "#f7f7f4"),
        make_frame(out_dir, "square", 900, 900, "#eef3ff"),
        make_frame(out_dir, "vertical", 720, 960, "#f7f7f4"),
    ]
    cases = [run_case(spec, prompt_case, out_dir) for spec in specs for prompt_case in PROMPTS]
    frame_paths = [Path(path) for case in cases[:3] for path in case.get("frames", [])]
    if frame_paths:
        make_contact_sheet(frame_paths, out_dir / "contact-sheet-first-3-cases.jpg")
    report = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "out_dir": str(out_dir),
        "case_count": len(cases),
        "frames_tested": [spec.id for spec in specs],
        "prompt_count": len(PROMPTS),
        "cases": cases,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
