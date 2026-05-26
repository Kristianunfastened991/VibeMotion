from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.figma_plugin import motion_from_plugin_asset  # noqa: E402
from app.services.layer_motion import describe_motion_plan, plan_frame_choreography  # noqa: E402
from app.services.motion import (  # noqa: E402
    _figma_visual_self_check_from_frames,
    _restore_exact_hold_frames,
    render_motion_asset,
)
from app.services.motion_intent import attach_frame_motion_contract  # noqa: E402


FINAL_PROMPT = (
    "Background appears via Venetian Blinds duration 0.5 seconds, then photos appear through parallax, "
    "then text appears with fade up lines from top to bottom, then black buttons rise on position Y with fade in. "
    "The whole composition must appear within 2 seconds. At the end the composition scatters and falls down with physics."
)


def assert_sidecar(report_path: Path) -> dict:
    if not report_path.exists():
        raise AssertionError(f"missing visual self-check sidecar: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "pass":
        raise AssertionError(f"visual self-check failed: {report}")
    checks = {str(item.get("id") or ""): item for item in list(report.get("checks") or []) if isinstance(item, dict)}
    for check_id in [
        "source_figma_png",
        "rendered_frames",
        "exact_hold_pixel_match",
        "settled_sharpness",
        "prompt_execution_auditor",
    ]:
        if checks.get(check_id, {}).get("status") != "pass":
            raise AssertionError(f"{check_id} did not pass: {checks.get(check_id)}")
    execution = report.get("prompt_execution_audit") if isinstance(report.get("prompt_execution_audit"), dict) else {}
    execution_checks = {
        str(item.get("id") or ""): item
        for item in list(execution.get("checks") or [])
        if isinstance(item, dict)
    }
    for check_id in ["venetian_blinds_visual", "appearance_progression", "scatter_fall_visual"]:
        if execution_checks.get(check_id, {}).get("status") != "pass":
            raise AssertionError(f"{check_id} did not pass: {execution_checks.get(check_id)}")
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    if float(metrics.get("exact_hold_mean_abs_diff") if metrics.get("exact_hold_mean_abs_diff") is not None else 999) > 8.0:
        raise AssertionError(f"hold diff too high: {metrics}")
    return report


def render_sidecar_case(out_dir: Path) -> dict:
    work_dir = out_dir / "work"
    source_motion = motion_from_plugin_asset(work_dir, "12-159", start=0, duration=8.0)
    layers = plan_frame_choreography(FINAL_PROMPT, source_motion)
    plan = describe_motion_plan(layers) or {}
    layers = attach_frame_motion_contract(FINAL_PROMPT, str(source_motion.id), layers, plan)
    plan = describe_motion_plan(layers) or plan
    motion = source_motion.model_copy(update={"figma_layers": layers, "motion_plan": plan})
    assets_dir = work_dir / "assets"
    render_motion_asset(motion, assets_dir)
    report_path = assets_dir / f"{motion.id}.visual-self-check.json"
    report = assert_sidecar(report_path)
    return {
        "name": "render-sidecar",
        "status": "pass",
        "report": str(report_path),
        "metrics": report.get("metrics") or {},
    }


def auto_retry_helper_case(out_dir: Path) -> dict:
    frames_dir = out_dir / "retry-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    source = Image.new("RGB", (160, 90), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((18, 18, 142, 72), outline="black", width=4)
    draw.line((22, 68, 138, 22), fill="black", width=3)
    for index in range(20):
        frame = Image.new("RGB", (160, 90), "black")
        frame.save(frames_dir / f"frame_{index:05d}.png")
    fake_motion = motion_from_plugin_asset(out_dir / "fake-work", "12-159", start=0, duration=2.0)
    before = _figma_visual_self_check_from_frames(fake_motion, frames_dir, source, 10, 2.0, 20, (0.2, 1.2), [])
    if before.get("status") != "fail":
        raise AssertionError(f"expected broken frames to fail, got {before}")
    restored = _restore_exact_hold_frames(frames_dir, source, 10, 20, (0.2, 1.2))
    after = _figma_visual_self_check_from_frames(
        fake_motion,
        frames_dir,
        source,
        10,
        2.0,
        20,
        (0.2, 1.2),
        [{"kind": "exact-source-hold", "target": "figma-frame-png", "detail": f"restored {restored} test frames"}],
    )
    if restored <= 0 or after.get("status") != "pass":
        raise AssertionError(f"auto retry did not repair frames: restored={restored}, after={after}")
    return {
        "name": "auto-retry-helper",
        "status": "pass",
        "restored_frames": restored,
        "before": before.get("metrics") or {},
        "after": after.get("metrics") or {},
    }


def main() -> None:
    out_dir = ROOT / "qa_artifacts" / f"motion-visual-self-check-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = [render_sidecar_case(out_dir), auto_retry_helper_case(out_dir)]
    report = {"status": "pass", "out_dir": str(out_dir), "tests": results}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
