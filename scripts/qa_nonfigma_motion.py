from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import MotionSpec, ProjectState  # noqa: E402
from app.services.render import render_project_preview  # noqa: E402


SAMPLE_TIMES = [0.5, 1.5, 3.0, 5.0, 7.5]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for name in candidates:
        path = Path(name)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=True)


def _build_source_video(project_root: Path, width: int = 1280, height: int = 720, duration: float = 8.0, fps: int = 30) -> Path:
    frames_dir = project_root / "source_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    total = int(duration * fps)
    title_font = _font(36, bold=True)
    small_font = _font(18)
    for index in range(total):
        t = index / max(1, total - 1)
        image = Image.new("RGB", (width, height), (16, 18, 20))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            ratio = y / max(1, height - 1)
            r = int(18 + 28 * ratio + 10 * t)
            g = int(20 + 38 * ratio)
            b = int(24 + 42 * (1 - ratio) + 14 * t)
            draw.line((0, y, width, y), fill=(r, g, b))
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        cx = int(width * (0.28 + 0.42 * t))
        cy = int(height * (0.42 + 0.06 * t))
        glow_draw.ellipse((cx - 260, cy - 190, cx + 260, cy + 190), fill=(78, 151, 178, 58))
        glow = glow.filter(ImageFilter.GaussianBlur(65))
        image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
        draw = ImageDraw.Draw(image)
        for x in range(60, width, 120):
            draw.line((x, 0, x, height), fill=(255, 255, 255, 18), width=1)
        for y in range(55, height, 110):
            draw.line((0, y, width, y), fill=(255, 255, 255, 14), width=1)
        draw.rounded_rectangle((820, 92, 1160, 430), radius=24, fill=(34, 38, 40), outline=(132, 164, 168), width=2)
        draw.rectangle((850, 132, 1130, 166), fill=(58, 93, 98))
        for row in range(5):
            draw.rounded_rectangle((850, 202 + row * 38, 1090 - row * 22, 222 + row * 38), radius=8, fill=(82, 92, 92))
        draw.text((72, 76), "Native VibeMotion canvas", font=title_font, fill=(238, 241, 237))
        draw.text((74, 124), "No Figma import, only generated motion blocks over source video", font=small_font, fill=(190, 202, 204))
        image.save(frames_dir / f"frame_{index:05d}.png", quality=95)

    output = project_root / "input" / "source.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "frame_%05d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "17",
            str(output),
        ],
        timeout=180,
    )
    return output


def _motion_blocks() -> list[MotionSpec]:
    return [
        MotionSpec(
            id="native-title",
            kind="glass-card",
            design_preset="soft-neumorphism",
            text="Native motion, no Figma",
            start=0.85,
            duration=3.25,
            x=72,
            y=72,
            width=520,
            height=164,
            accent="#75d7c7",
            background="rgba(244, 241, 231, 0.96)",
            enter_animation="rise",
            exit_animation="fade",
            enter_from="bottom",
            exit_to="center",
            enter_duration=0.55,
            exit_duration=0.45,
            easing="expo",
            prompt="Native generated title card rises smoothly, holds, then fades.",
            motion_plan={"agent_beat": {"id": "01", "eyebrow": "BUILD"}},
        ),
        MotionSpec(
            id="native-workflow",
            kind="glass-card",
            design_preset="frosted-glass",
            text="Prompt -> motion block -> final render",
            start=2.15,
            duration=3.75,
            x=610,
            y=392,
            width=570,
            height=190,
            accent="#8bd3ff",
            background="rgba(22, 27, 31, 0.64)",
            enter_animation="slide",
            exit_animation="slide",
            enter_from="right",
            exit_to="left",
            enter_duration=0.6,
            exit_duration=0.5,
            easing="expo",
            prompt="Frosted workflow card slides in from the right and exits left.",
            motion_plan={"agent_beat": {"id": "02", "eyebrow": "INTEGRATE"}},
        ),
        MotionSpec(
            id="native-tune",
            kind="glass-card",
            design_preset="data-panel",
            text="Build. Integrate. Tune.",
            start=4.35,
            duration=3.1,
            x=102,
            y=470,
            width=500,
            height=140,
            accent="#f6d66f",
            background="rgba(10, 16, 20, 0.72)",
            enter_animation="pop",
            exit_animation="slide",
            enter_from="center",
            exit_to="right",
            enter_duration=0.45,
            exit_duration=0.5,
            easing="expo",
            prompt="Compact data panel pops in, holds, then slides out right.",
            motion_plan={"agent_beat": {"id": "03", "eyebrow": "TUNE"}},
        ),
    ]


def _extract_frame(video: Path, output: Path, seconds: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-ss", f"{seconds:.2f}", "-i", str(video), "-frames:v", "1", str(output)], timeout=60)


def _contact_sheet(frames: dict[str, Path], output: Path) -> None:
    thumbs: list[tuple[str, Image.Image]] = []
    for label, path in frames.items():
        image = Image.open(path).convert("RGB")
        image.thumbnail((390, 220), Image.Resampling.LANCZOS)
        thumbs.append((label, image.copy()))
    cell_w, cell_h = 410, 270
    sheet = Image.new("RGB", (cell_w * len(thumbs), cell_h), (10, 10, 10))
    draw = ImageDraw.Draw(sheet)
    label_font = _font(14)
    for index, (label, image) in enumerate(thumbs):
        x = index * cell_w + 10
        sheet.paste(image, (x, 20))
        draw.text((x, 236), label, fill=(240, 240, 240), font=label_font)
    sheet.save(output)


def _brightness(path: Path) -> float:
    return float(ImageStat.Stat(Image.open(path).convert("L")).mean[0])


def main() -> None:
    run_dir = ROOT / "qa_artifacts" / "motion_autotest" / f"nonfigma_{time.strftime('%Y%m%d-%H%M%S')}"
    project_root = run_dir / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    source = _build_source_video(project_root)
    project = ProjectState(
        project_id="qa-nonfigma",
        title="QA Non-Figma Native Motion",
        status="ready",
        mode="full",
        source_video=str(source.relative_to(project_root)),
        motions=_motion_blocks(),
    )
    preview = render_project_preview(project, project_root)
    frames: dict[str, Path] = {}
    for seconds in SAMPLE_TIMES:
        key = f"{seconds:.2f}s"
        frame_path = run_dir / "frames" / f"t{str(seconds).replace('.', '_')}.png"
        _extract_frame(preview, frame_path, seconds)
        frames[key] = frame_path
    contact = run_dir / "nonfigma_contact_sheet.jpg"
    _contact_sheet(frames, contact)
    report = {
        "status": "pass",
        "source_type": "generated",
        "uses_figma": False,
        "preview": str(preview),
        "contact_sheet": str(contact),
        "frames": {key: str(path) for key, path in frames.items()},
        "frame_brightness": {key: round(_brightness(path), 3) for key, path in frames.items()},
        "motions": [motion.model_dump() for motion in project.motions],
    }
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "pass", "run_dir": str(run_dir), "preview": str(preview), "contact_sheet": str(contact)}, indent=2))


if __name__ == "__main__":
    main()
