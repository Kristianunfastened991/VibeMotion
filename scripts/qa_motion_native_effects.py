from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer_motion import _fallback_recipe  # noqa: E402
from app.services.motion import _apply_sprite_visual_effects  # noqa: E402


def _font(size: int) -> ImageFont.ImageFont:
    for name in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arial.ttf"):
        path = Path(name)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _sprite() -> Image.Image:
    image = Image.new("RGBA", (360, 220), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 359, 219), outline=(32, 32, 32, 255), width=2)
    draw.text((22, 18), "Hey creator!", fill=(0, 0, 0, 255), font=_font(34))
    draw.text((24, 67), "Clean raster text, exact crop,\nno relayout during motion.", fill=(28, 28, 28, 255), font=_font(15), spacing=4)
    draw.rounded_rectangle((230, 26, 330, 148), radius=16, fill=(152, 170, 166, 255))
    draw.ellipse((252, 45, 309, 105), fill=(74, 48, 38, 255))
    draw.rounded_rectangle((224, 138, 324, 196), radius=14, fill=(28, 28, 28, 255))
    draw.text((244, 158), "CTA", fill=(255, 255, 255, 255), font=_font(22))
    draw.rounded_rectangle((24, 170, 112, 195), radius=12, fill=(0, 0, 0, 255))
    draw.rounded_rectangle((126, 170, 214, 195), radius=12, fill=(18, 18, 18, 255))
    draw.text((45, 175), "link", fill=(255, 255, 255, 255), font=_font(13))
    draw.text((147, 175), "more", fill=(255, 255, 255, 255), font=_font(13))
    return image


def _text_sprite() -> Image.Image:
    image = Image.new("RGBA", (360, 220), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((22, 24), "Hey creator!", fill=(0, 0, 0, 255), font=_font(38))
    draw.text((24, 84), "Clean raster text", fill=(28, 28, 28, 255), font=_font(22))
    draw.text((24, 122), "Line height stays fixed", fill=(28, 28, 28, 255), font=_font(22))
    draw.text((24, 160), "No text relayout", fill=(28, 28, 28, 255), font=_font(22))
    return image


def _recipe(effect: dict) -> dict:
    return {
        "motion_dsl": {
            "version": 1,
            "keyframes": [{"time": 0, "opacity": 1}],
            "effects": [effect],
        }
    }


def _alpha_sum(image: Image.Image) -> float:
    return float(ImageStat.Stat(image.convert("RGBA").getchannel("A")).sum[0])


def _diff_score(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    return float(sum(ImageStat.Stat(diff).sum))


def _assert_between(name: str, value: float, low: float, high: float) -> None:
    if not (low <= value <= high):
        raise AssertionError(f"{name}: expected {low:.1f}..{high:.1f}, got {value:.1f}")


def _checker(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (238, 238, 238, 255))
    draw = ImageDraw.Draw(image)
    step = 16
    for y in range(0, height, step):
        for x in range(0, width, step):
            if ((x // step) + (y // step)) % 2:
                draw.rectangle((x, y, x + step - 1, y + step - 1), fill=(210, 210, 210, 255))
    return image


def _flatten(image: Image.Image) -> Image.Image:
    base = _checker(image.size)
    base.alpha_composite(image.convert("RGBA"), (0, 0))
    return base.convert("RGB")


def _contact_sheet(samples: list[tuple[str, list[Image.Image]]], output: Path) -> None:
    cell_w, cell_h = 390, 270
    sheet = Image.new("RGB", (cell_w * 3, cell_h * len(samples)), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    label_font = _font(15)
    for row, (name, images) in enumerate(samples):
        for col, image in enumerate(images):
            x = col * cell_w + 15
            y = row * cell_h + 38
            sheet.paste(_flatten(image), (x, y))
            title = f"{name} / {'start' if col == 0 else 'mid' if col == 1 else 'end'}"
            draw.text((col * cell_w + 15, row * cell_h + 12), title, fill=(0, 0, 0), font=label_font)
    sheet.save(output)


def _preview_video(effect_cases: list[tuple[str, dict]], sprite: Image.Image, output: Path) -> None:
    frames_dir = output.parent / "mp4_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    fps = 24
    duration = 1.25
    cell_w, cell_h = 390, 270
    cols = 2
    rows = math.ceil(len(effect_cases) / cols)
    label_font = _font(15)
    for index in range(int(duration * fps)):
        t = index / fps
        canvas = Image.new("RGB", (cell_w * cols, cell_h * rows), (245, 245, 245))
        draw = ImageDraw.Draw(canvas)
        for case_index, (name, effect) in enumerate(effect_cases):
            case_sprite = _text_sprite() if name in {"typewriter", "line-reveal"} else sprite
            image = _apply_sprite_visual_effects(case_sprite, _recipe(effect), t)
            x = (case_index % cols) * cell_w + 15
            y = (case_index // cols) * cell_h + 38
            canvas.paste(_flatten(image), (x, y))
            draw.text((x, y - 24), name, fill=(0, 0, 0), font=label_font)
        canvas.save(frames_dir / f"frame_{index:04d}.png")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "frame_%04d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "16",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _verify_visual_effects(root: Path) -> None:
    sprite = _sprite()
    effect_cases: list[tuple[str, dict]] = [
        ("wipe-reveal", {"type": "wipe-reveal", "start": 0, "duration": 1, "direction": "right"}),
        ("venetian-blinds", {"type": "venetian-blinds", "start": 0, "duration": 1, "blades": 12}),
        ("horizontal-venetian", {"type": "venetian-blinds", "start": 0, "duration": 1, "blades": 9, "orientation": "horizontal"}),
        ("iris-reveal", {"type": "iris-reveal", "start": 0, "duration": 1}),
        ("luma-wipe", {"type": "luma-wipe", "start": 0, "duration": 1, "direction": "in"}),
        ("liquid-wipe", {"type": "liquid-wipe", "start": 0, "duration": 1, "direction": "right"}),
        ("typewriter", {"type": "typewriter", "start": 0, "duration": 1, "steps": 24}),
        ("line-reveal", {"type": "line-reveal", "start": 0, "duration": 1, "lines": 4}),
        ("particle-dissolve-out", {"type": "particle-dissolve", "start": 0, "duration": 1, "direction": "out", "cells": 24}),
        ("smoke-dissolve-out", {"type": "smoke-dissolve", "start": 0, "duration": 1, "direction": "out"}),
        ("paper-tear", {"type": "paper-tear", "start": 0, "duration": 1, "direction": "right"}),
        ("pixelate", {"type": "pixelate", "start": 0, "duration": 1}),
        ("glitch", {"type": "glitch", "start": 0, "duration": 1, "amplitude": 0.055}),
        ("film-burn", {"type": "film-burn", "start": 0, "duration": 1, "strength": 0.5}),
        ("shimmer", {"type": "shimmer", "start": 0, "duration": 1, "strength": 0.24}),
    ]
    contact_samples: list[tuple[str, list[Image.Image]]] = []
    for name, effect in effect_cases:
        case_sprite = _text_sprite() if name in {"typewriter", "line-reveal"} else sprite
        base_alpha = _alpha_sum(case_sprite)
        start = _apply_sprite_visual_effects(case_sprite, _recipe(effect), 0)
        mid = _apply_sprite_visual_effects(case_sprite, _recipe(effect), 0.5)
        end = _apply_sprite_visual_effects(case_sprite, _recipe(effect), 1.1)
        contact_samples.append((name, [start, mid, end]))
        if name in {"shimmer", "glitch", "film-burn"}:
            if _diff_score(mid, case_sprite) <= 1000:
                raise AssertionError(f"{name}: mid frame did not change pixels")
            if _diff_score(end, case_sprite) != 0:
                raise AssertionError(f"{name}: final frame must settle to exact source")
            continue
        if name == "pixelate":
            if _diff_score(start, case_sprite) <= 1000:
                raise AssertionError("pixelate: start frame did not pixelate")
            if _diff_score(mid, case_sprite) <= 1000:
                raise AssertionError("pixelate: mid frame did not pixelate")
            if _diff_score(end, case_sprite) != 0:
                raise AssertionError("pixelate: final frame must settle to exact source")
            continue
        if name in {"particle-dissolve-out", "smoke-dissolve-out"}:
            _assert_between(f"{name} start alpha", _alpha_sum(start), base_alpha * 0.99, base_alpha)
            _assert_between(f"{name} mid alpha", _alpha_sum(mid), base_alpha * 0.2, base_alpha * 0.8)
            _assert_between(f"{name} end alpha", _alpha_sum(end), 0, base_alpha * 0.01)
            continue
        _assert_between(f"{name} start alpha", _alpha_sum(start), 0, base_alpha * 0.02)
        _assert_between(f"{name} mid alpha", _alpha_sum(mid), base_alpha * 0.15, base_alpha * 0.9)
        if _diff_score(end, case_sprite) != 0:
            raise AssertionError(f"{name}: final frame must settle to exact source")
    _contact_sheet(contact_samples, root / "native_effects_contact_sheet.png")
    _preview_video(effect_cases, sprite, root / "native_effects_preview.mp4")


def _verify_prompt_mapping() -> None:
    layer_text = {"kind": "text"}
    layer_image = {"kind": "image"}
    cases = [
        ("add a typewriter text animation for 1 second", layer_text, "typewriter"),
        ("make text fade up lines from top to bottom", layer_text, "line-reveal"),
        ("iris reveal this image in 0.5 seconds", layer_image, "iris-reveal"),
        ("luma wipe this image in 1 second", layer_image, "luma-wipe"),
        ("liquid wipe the layer from left to right", layer_image, "liquid-wipe"),
        ("particle dissolve out over 1 second", layer_image, "particle-dissolve"),
        ("smoke dissolve out over 1 second", layer_image, "smoke-dissolve"),
        ("paper tear reveal from left", layer_image, "paper-tear"),
        ("pixelate in for 1 second", layer_image, "pixelate"),
        ("digital glitch the layer", layer_image, "glitch"),
        ("film burn light leak", layer_image, "film-burn"),
        ("wipe reveal from left", layer_image, "wipe-reveal"),
        ("add a shimmer light sweep to the button", {"kind": "shape"}, "shimmer"),
        ("use kinetic typography on this text", layer_text, "line-reveal"),
        ("draw underline from left to right", {"kind": "shape"}, "wipe-reveal"),
        ("draw arrow from left to right", {"kind": "shape"}, "wipe-reveal"),
        ("add handheld camera shake", layer_image, "shake"),
    ]
    for prompt, layer, expected in cases:
        recipe = _fallback_recipe(prompt, layer)
        effects = {str(effect.get("type")) for effect in recipe.get("motion_dsl", {}).get("effects", [])}
        if expected not in effects:
            raise AssertionError(f"{prompt!r}: expected {expected}, got {sorted(effects)}")
    camera_cases = [
        ("camera push in / zoom in for 1 second", layer_image, lambda frames: max(frame.get("scale", 1) for frame in frames) > 1.05),
        ("camera pull back / zoom out for 1 second", layer_image, lambda frames: float(frames[0].get("scale", 1)) > float(frames[-1].get("scale", 1))),
        ("pan right for 1 second", layer_image, lambda frames: float(frames[0].get("x", 0)) < float(frames[-1].get("x", 0))),
    ]
    for prompt, layer, predicate in camera_cases:
        recipe = _fallback_recipe(prompt, layer)
        frames = recipe.get("motion_dsl", {}).get("keyframes", [])
        if not predicate(frames):
            raise AssertionError(f"{prompt!r}: camera/pan keyframes not generated: {frames}")

    parallax = _fallback_recipe("photos appear through depth parallax in 0.6 seconds", layer_image)
    parallax_frames = parallax.get("motion_dsl", {}).get("keyframes", [])
    if not parallax_frames or float(parallax_frames[0].get("opacity", 1)) > 0.01 or float(parallax_frames[0].get("scale", 1)) <= 1.01:
        raise AssertionError(f"parallax photo: expected hidden overscan start, got {parallax_frames}")
    if any(str(effect.get("type")) == "float" for effect in parallax.get("motion_dsl", {}).get("effects", [])):
        raise AssertionError("parallax photo: should settle exactly, not keep floating by default")

    button = _fallback_recipe("button rises from below on position Y with light fade in", {"kind": "shape"})
    button_frames = button.get("motion_dsl", {}).get("keyframes", [])
    if not button_frames or float(button_frames[0].get("y", 0)) <= 0 or float(button_frames[0].get("opacity", 1)) > 0.01:
        raise AssertionError(f"button-y-rise: expected hidden Y-rise start, got {button_frames}")

    flip = _fallback_recipe("flip card in from the center", layer_image)
    flip_frames = flip.get("motion_dsl", {}).get("keyframes", [])
    if not flip_frames or float(flip_frames[0].get("scaleX", 1)) >= 0.2 or float(flip_frames[-1].get("scaleX", 0)) != 1:
        raise AssertionError(f"flip-card: expected bounded scaleX flip, got {flip_frames}")

    cascade = _fallback_recipe("cascade this text from top to bottom", layer_text)
    cascade_frames = cascade.get("motion_dsl", {}).get("keyframes", [])
    if not cascade_frames or abs(float(cascade_frames[0].get("y", 0))) < 4 or float(cascade_frames[-1].get("y", 99)) != 0:
        raise AssertionError(f"cascade: expected bounded vertical cascade, got {cascade_frames}")


def main() -> None:
    output = ROOT / "qa_artifacts" / f"native_effects_{time.strftime('%Y%m%d_%H%M%S')}"
    output.mkdir(parents=True, exist_ok=True)
    _verify_visual_effects(output)
    _verify_prompt_mapping()
    print(f"PASS native motion effects QA")
    print(f"ARTIFACTS {output}")
    print(f"CONTACT {output / 'native_effects_contact_sheet.png'}")
    print(f"VIDEO {output / 'native_effects_preview.mp4'}")


if __name__ == "__main__":
    main()
