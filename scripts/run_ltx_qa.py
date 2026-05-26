from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

from app.models.schemas import MotionSpec
from app.services.ltx_video import generate_ltx_layer_preview
from app.services.motion import render_motion_video_asset


APP_ROOT = Path(__file__).resolve().parents[1]
STATIC_FIGMA_ROOT = APP_ROOT / "app" / "static" / "assets" / "figma-plugin"
PROJECTS_ROOT = APP_ROOT / "projects"
QA_ROOT = APP_ROOT / "qa_artifacts" / "ltx_tests"


@dataclass(frozen=True)
class LtxQaCase:
    name: str
    asset_id: str
    layer_id: str
    prompt: str
    duration: float
    fps: int = 24
    seed: int = 42


CASES = [
    LtxQaCase(
        "overhead_portrait_4s",
        "12-272",
        "12:290",
        "A subtle cinematic portrait animation. The woman slowly breathes and looks toward the camera. The overhead light flickers softly, no camera shake.",
        4.0,
        seed=42,
    ),
    LtxQaCase(
        "side_light_portrait_6s",
        "119-118",
        "119:135",
        "A slow dolly-in on the seated woman. Her shoulders and hair move naturally, dramatic side lighting stays consistent.",
        6.0,
        seed=43,
    ),
    LtxQaCase(
        "brand_model_grid_4s",
        "254-159",
        "254:163",
        "A fashion model turns slightly toward the light with elegant fabric motion. Keep the product editorial composition stable.",
        4.0,
        seed=44,
    ),
    LtxQaCase(
        "helmet_grid_6s",
        "263-363",
        "263:371",
        "A premium product animation with a very slow push-in. Highlights travel across the helmet surface, object shape remains stable.",
        6.0,
        seed=45,
    ),
    LtxQaCase(
        "vertical_photo_portrait_4s",
        "12-247",
        "12:250",
        "A gentle vertical portrait animation. The woman breathes naturally and the camera slowly pushes in, keep the body proportions stable.",
        4.0,
        seed=46,
    ),
]


def _run(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()


def _ffprobe(path: Path) -> dict[str, str]:
    raw = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_frames,r_frame_rate,duration",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(raw)["streams"][0]


def _extract_frame(video: Path, seconds: float, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1", str(output)],
        check=True,
    )


def _image_metrics(path: Path, crop: tuple[int, int, int, int] | None = None) -> dict[str, object]:
    with Image.open(path).convert("RGB") as image:
        if crop:
            left, top, right, bottom = crop
            left = max(0, min(image.width - 1, left))
            top = max(0, min(image.height - 1, top))
            right = max(left + 1, min(image.width, right))
            bottom = max(top + 1, min(image.height, bottom))
            image = image.crop((left, top, right, bottom))
        stat = ImageStat.Stat(image)
        extrema = stat.extrema
        mean = [round(value, 2) for value in stat.mean]
        black_pixels = 0
        total = image.width * image.height
        for r, g, b in image.getdata():
            if r < 8 and g < 8 and b < 8:
                black_pixels += 1
        return {
            "size": [image.width, image.height],
            "mean": mean,
            "extrema": extrema,
            "black_ratio": round(black_pixels / max(1, total), 5),
        }


def _load_asset(asset_id: str) -> dict:
    assets = json.loads((STATIC_FIGMA_ROOT / "assets.json").read_text(encoding="utf-8"))
    for asset in assets:
        if str(asset.get("id")) == asset_id:
            return asset
    raise KeyError(f"Figma asset not found: {asset_id}")


def _prepare_motion(project_root: Path, case: LtxQaCase) -> tuple[MotionSpec, dict]:
    asset = _load_asset(case.asset_id)
    layers = []
    layer_root = project_root / "assets" / "figma-plugin" / case.asset_id / "layers"
    layer_root.mkdir(parents=True, exist_ok=True)

    for layer in asset.get("figma_layers") or []:
        next_layer = dict(layer)
        asset_file = next_layer.pop("asset_file", None)
        if asset_file:
            source = STATIC_FIGMA_ROOT / asset_file
            target = layer_root / Path(asset_file).name
            if source.exists():
                shutil.copy2(source, target)
                next_layer["asset_path"] = str(target.relative_to(project_root))
        layers.append(next_layer)

    frame_file = asset.get("asset_file")
    motion_asset_path = None
    if frame_file:
        source = STATIC_FIGMA_ROOT / frame_file
        target = project_root / "assets" / "figma-plugin" / case.asset_id / "frame.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copy2(source, target)
            motion_asset_path = str(target.relative_to(project_root))

    motion = MotionSpec(
        id=f"ltx-qa-{case.name}",
        text=asset.get("name") or case.name,
        start=0,
        duration=case.duration,
        x=0,
        y=0,
        width=960,
        height=540,
        source_type="figma",
        asset_path=motion_asset_path,
        figma_node_id=asset.get("node_id"),
        figma_node_name=asset.get("name"),
        figma_layers=layers,
    )
    target_layer = next((layer for layer in motion.figma_layers if str(layer.get("id")) == case.layer_id), None)
    if not target_layer:
        raise KeyError(f"Layer {case.layer_id} not found in {case.asset_id}")
    return motion, target_layer


def _layer_crop(motion: MotionSpec, layer: dict, frame_size: tuple[int, int]) -> tuple[int, int, int, int]:
    layers = motion.figma_layers
    bounds_width = max(float(item.get("x", 0) or 0) + float(item.get("width", 0) or 0) for item in layers)
    bounds_height = max(float(item.get("y", 0) or 0) + float(item.get("height", 0) or 0) for item in layers)
    scale_x = frame_size[0] / max(1.0, bounds_width)
    scale_y = frame_size[1] / max(1.0, bounds_height)
    left = int(round(float(layer.get("x", 0) or 0) * scale_x))
    top = int(round(float(layer.get("y", 0) or 0) * scale_y))
    width = int(round(float(layer.get("width", 1) or 1) * scale_x))
    height = int(round(float(layer.get("height", 1) or 1) * scale_y))
    return left, top, left + max(1, width), top + max(1, height)


def run_case(project_root: Path, case: LtxQaCase) -> dict:
    started = time.time()
    motion, target_layer = _prepare_motion(project_root, case)
    preview = generate_ltx_layer_preview(
        project_root=project_root,
        motion=motion,
        layer_id=case.layer_id,
        prompt=case.prompt,
        duration=case.duration,
        fps=case.fps,
        seed=case.seed,
    )
    target_layer["ltx_preview"] = preview
    target_layer["ltx_video_path"] = preview["preview_path"]
    target_layer["ltx_prompt"] = preview["prompt"]
    target_layer["ltx_duration"] = preview["duration"]
    target_layer["ltx_fps"] = preview["fps"]

    rendered = render_motion_video_asset(motion, project_root / "assets", fps=30)
    if rendered is None:
        raise RuntimeError("Motion renderer returned no output")

    preview_path = project_root / preview["preview_path"]
    input_path = project_root / preview["input_path"]
    input_size = Image.open(input_path).size
    preview_probe = _ffprobe(preview_path)
    render_probe = _ffprobe(rendered)

    snapshots_dir = QA_ROOT / project_root.name / case.name
    ltx_frame = snapshots_dir / "ltx_mid.png"
    render_frame = snapshots_dir / "render_mid.png"
    _extract_frame(preview_path, min(case.duration / 2, max(0.1, case.duration - 0.1)), ltx_frame)
    _extract_frame(rendered, min(case.duration / 2, max(0.1, case.duration - 0.1)), render_frame)

    render_width = int(render_probe["width"])
    render_height = int(render_probe["height"])
    crop = _layer_crop(motion, target_layer, (render_width, render_height))
    preview_aspect = int(preview_probe["width"]) / int(preview_probe["height"])
    input_aspect = input_size[0] / input_size[1]
    result = {
        "case": case.name,
        "asset_id": case.asset_id,
        "layer_id": case.layer_id,
        "duration": case.duration,
        "fps": case.fps,
        "seed": case.seed,
        "prompt": case.prompt,
        "input_size": list(input_size),
        "preview": str(preview_path),
        "rendered": str(rendered),
        "preview_probe": preview_probe,
        "render_probe": render_probe,
        "aspect_delta_percent": round(abs(preview_aspect - input_aspect) / input_aspect * 100, 3),
        "ltx_frame_metrics": _image_metrics(ltx_frame),
        "render_crop_metrics": _image_metrics(render_frame, crop),
        "ltx_frame": str(ltx_frame),
        "render_frame": str(render_frame),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(CASES))
    parser.add_argument("--start-index", type=int, default=1, help="1-based case index to start from")
    parser.add_argument("--project-name", default=f"ltx-qa-{time.strftime('%Y%m%d-%H%M%S')}")
    args = parser.parse_args()

    project_root = PROJECTS_ROOT / args.project_name
    project_root.mkdir(parents=True, exist_ok=True)
    QA_ROOT.mkdir(parents=True, exist_ok=True)

    report_path = QA_ROOT / project_root.name / "report.json"
    if report_path.exists():
        results = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        results = []
    start = max(0, args.start_index - 1)
    stop = min(len(CASES), args.limit)
    for case in CASES[start:stop]:
        print(f"[qa] start {case.name} duration={case.duration}s layer={case.layer_id}", flush=True)
        result = run_case(project_root, case)
        results = [item for item in results if item.get("case") != case.name]
        results.append(result)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            f"[qa] done {case.name} elapsed={result['elapsed_seconds']}s "
            f"aspect_delta={result['aspect_delta_percent']} black={result['render_crop_metrics']['black_ratio']}",
            flush=True,
        )

    print(f"[qa] report {report_path}")


if __name__ == "__main__":
    main()
