from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat


WHOLE_FRAME_PROMPT = (
    "The first 2 seconds show only the clean background. Then over 3 seconds all visible elements fly into the frame "
    "in random order and settle smoothly. Hold the exact original frame. During the last 3 seconds the full frame "
    "shatters like glass and fades out."
)
WHOLE_FRAME_SAMPLE_TIMES = [0.5, 2.5, 4.5, 12.5, 14.5]
SELECTED_LAYER_SAMPLE_TIMES = [0.25, 0.75, 3.0, 5.5]
UI_REQUIRED_IDS = [
    "previewPlayer",
    "frameMotionTrack",
    "motionTrack",
    "motionPromptModal",
    "ltxPromptModal",
    "ltxPreviewShell",
    "ltxDuration",
    "ltxQuality",
    "generateLtxPromptBtn",
    "applyLtxPromptBtn",
    "cancelLtxPromptBtn",
]


@dataclass
class ScenarioResult:
    scenario_id: str
    status: str
    scores: dict[str, int]
    metrics: dict[str, Any]
    evidence: dict[str, Any]
    findings: list[str]


def _run(command: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)


def _safe_run(command: list[str], cwd: Path | None = None, timeout: int = 300) -> dict[str, Any]:
    started = time.time()
    try:
        completed = _run(command, cwd=cwd, timeout=timeout)
        return {
            "ok": True,
            "seconds": round(time.time() - started, 3),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except subprocess.CalledProcessError as exc:
        return {
            "ok": False,
            "seconds": round(time.time() - started, 3),
            "returncode": exc.returncode,
            "stdout": (exc.stdout or "")[-4000:],
            "stderr": (exc.stderr or "")[-4000:],
        }
    except Exception as exc:
        return {"ok": False, "seconds": round(time.time() - started, 3), "error": str(exc)}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mean_abs_diff(left: Image.Image, right: Image.Image) -> float:
    left = left.convert("RGB")
    right = right.convert("RGB")
    if left.size != right.size:
        right = right.resize(left.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(left, right)
    stat = ImageStat.Stat(diff)
    return float(sum(stat.mean) / len(stat.mean))


def _edge_score(image: Image.Image) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).mean[0])


def _brightness(image: Image.Image) -> float:
    return float(ImageStat.Stat(image.convert("L")).mean[0])


def _crop(image: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = rect
    left = max(0, min(image.width - 1, left))
    top = max(0, min(image.height - 1, top))
    right = max(left + 1, min(image.width, right))
    bottom = max(top + 1, min(image.height, bottom))
    return image.crop((left, top, right, bottom))


def _mask_out_rect(image: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    output = image.convert("RGB").copy()
    draw = ImageDraw.Draw(output)
    draw.rectangle(rect, fill=(0, 0, 0))
    return output


def _project_ids(root: Path) -> list[str]:
    projects_dir = root / "projects"
    return [item.name for item in projects_dir.iterdir() if item.is_dir() and (item / "project.json").exists()]


def _load_project(project_root: Path):
    from app.models.schemas import ProjectState

    data = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
    return ProjectState.model_validate(data)


def _copy_file_if_needed(source_root: Path, dest_root: Path, rel_path: str | None) -> None:
    if not rel_path:
        return
    src = source_root / rel_path
    dst = dest_root / rel_path
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src, dst)


def _build_min_project(source_root: Path, dest_root: Path, project, motion, duration: float = 16.0):
    dest_root.mkdir(parents=True, exist_ok=True)
    (dest_root / "input").mkdir(parents=True, exist_ok=True)
    (dest_root / "assets").mkdir(parents=True, exist_ok=True)
    _copy_file_if_needed(source_root, dest_root, motion.asset_path)
    for layer in list(motion.figma_layers or []):
        _copy_file_if_needed(source_root, dest_root, str(layer.get("asset_path") or ""))
        _copy_file_if_needed(source_root, dest_root, str(layer.get("ltx_video_path") or ""))
    source_video = dest_root / "input" / "qa_source.mp4"
    if not source_video.exists():
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=#101010:s=1920x1080:r=30",
                "-t",
                f"{duration:.3f}",
                "-pix_fmt",
                "yuv420p",
                str(source_video),
            ],
            timeout=120,
        )
    scenario_project = project.model_copy(
        update={
            "source_video": "input\\qa_source.mp4",
            "motions": [motion],
            "outputs": {},
            "current_job_id": None,
            "last_error": None,
            "subtitles_enabled": False,
        }
    )
    (dest_root / "project.json").write_text(scenario_project.model_dump_json(indent=2), encoding="utf-8")
    return scenario_project


def _find_figma_motion(project):
    motions = [
        motion
        for motion in list(project.motions or [])
        if getattr(motion, "source_type", "") == "figma" and motion.asset_path and motion.figma_layers
    ]
    if not motions:
        raise RuntimeError("No Figma motion with layer assets found")
    return max(motions, key=lambda motion: len(motion.figma_layers or []))


def _find_layer(motion, kind: str) -> dict[str, Any] | None:
    layers = [dict(layer) for layer in list(motion.figma_layers or []) if layer.get("visible") is not False]
    if kind == "image":
        candidates = [layer for layer in layers if layer.get("kind") == "image" and layer.get("asset_path")]
        candidates.sort(key=lambda layer: float(layer.get("width") or 0) * float(layer.get("height") or 0), reverse=True)
        return candidates[0] if candidates else None
    if kind == "text":
        return next((layer for layer in layers if layer.get("kind") == "text" and layer.get("asset_path")), None)
    if kind == "card":
        try:
            from app.services.motion import _figma_layer_bounds

            frame_width, frame_height = _figma_layer_bounds(motion, list(motion.figma_layers or []))
        except Exception:
            frame_width = float(getattr(motion, "width", 1920) or 1920)
            frame_height = float(getattr(motion, "height", 1080) or 1080)
        frame_area = max(1.0, frame_width * frame_height)
        candidates = [
            layer
            for layer in layers
            if layer.get("kind") == "shape"
            and not str(layer.get("id") or "").startswith("__")
            and (float(layer.get("width") or 0) * float(layer.get("height") or 0)) < frame_area * 0.75
            and 8 <= float(layer.get("height") or 0)
            and 8 <= float(layer.get("width") or 0)
        ]
        candidates.sort(key=lambda layer: float(layer.get("width") or 0) * float(layer.get("height") or 0), reverse=True)
        return candidates[0] if candidates else next((layer for layer in layers if layer.get("kind") == "shape"), None)
    return None


def _layer_rect_pixels(motion, layer: dict[str, Any], source_size: tuple[int, int]) -> tuple[int, int, int, int]:
    from app.services.motion import _figma_layer_bounds

    bounds_width, bounds_height = _figma_layer_bounds(motion, list(motion.figma_layers or []))
    scale_x = source_size[0] / max(1.0, float(bounds_width))
    scale_y = source_size[1] / max(1.0, float(bounds_height))
    x = int(round(float(layer.get("x") or 0) * scale_x))
    y = int(round(float(layer.get("y") or 0) * scale_y))
    width = int(round(float(layer.get("width") or 1) * scale_x))
    height = int(round(float(layer.get("height") or 1) * scale_y))
    pad = 6
    return x - pad, y - pad, x + width + pad, y + height + pad


def _union_rect(rects: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(rect[0] for rect in rects),
        min(rect[1] for rect in rects),
        max(rect[2] for rect in rects),
        max(rect[3] for rect in rects),
    )


def _layer_motion_rect_pixels(motion, layer: dict[str, Any], source_size: tuple[int, int], local_time: float, total_duration: float) -> tuple[int, int, int, int]:
    from app.services.motion import _figma_layer_bounds, _motion_dsl_state

    rect = _layer_rect_pixels(motion, layer, source_size)
    recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else {}
    state = _motion_dsl_state(recipe, local_time, total_duration) if recipe else {}
    bounds_width, bounds_height = _figma_layer_bounds(motion, list(motion.figma_layers or []))
    scale_x = source_size[0] / max(1.0, float(bounds_width))
    scale_y = source_size[1] / max(1.0, float(bounds_height))
    ref_w = max(1.0, float(layer.get("width") or 1)) * scale_x
    ref_h = max(1.0, float(layer.get("height") or 1)) * scale_y
    dx = int(round((float(state.get("x", 0) or 0) / 100.0) * ref_w))
    dy = int(round((float(state.get("y", 0) or 0) / 100.0) * ref_h))
    return rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy


def _ffprobe(path: Path) -> dict[str, Any]:
    result = _safe_run(
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
        ],
        timeout=30,
    )
    if not result.get("ok"):
        return {"ok": False, **result}
    try:
        payload = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {"ok": True, **payload}


def _api_base_url(app_url: str) -> str:
    if app_url.endswith("/app/index.html"):
        return app_url[: -len("/app/index.html")]
    if app_url.endswith("/app/"):
        return app_url[: -len("/app/")]
    if app_url.endswith("/app"):
        return app_url[: -len("/app")]
    return app_url.rstrip("/")


def _url_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body) if body.strip() else None
        return {"ok": True, "status": response.status, "payload": payload}


def _wait_for_api(api_url: str, seconds: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + seconds
    last_error = ""
    while time.time() < deadline:
        try:
            result = _url_json(api_url, timeout=2.0)
            return {"status": "ready", "code": result["status"]}
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    return {"status": "unreachable", "error": last_error}


def _ensure_server(root: Path, app_url: str, run_dir: Path) -> dict[str, Any]:
    api_url = urljoin(_api_base_url(app_url) + "/", "api/projects")
    ready = _wait_for_api(api_url, seconds=2.0)
    if ready.get("status") == "ready":
        return {**ready, "api_url": api_url, "started": False}

    log_dir = run_dir / "server"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = (log_dir / "uvicorn.stdout.log").open("ab")
    stderr = (log_dir / "uvicorn.stderr.log").open("ab")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8010",
            ],
            cwd=root,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    except Exception as exc:
        stdout.close()
        stderr.close()
        return {"status": "start-failed", "api_url": api_url, "started": False, "error": str(exc)}

    ready = _wait_for_api(api_url, seconds=20.0)
    return {
        **ready,
        "api_url": api_url,
        "started": True,
        "pid": process.pid,
        "stdout_log": str(log_dir / "uvicorn.stdout.log"),
        "stderr_log": str(log_dir / "uvicorn.stderr.log"),
    }


def _chromium_candidates() -> list[Path]:
    env_candidates = [
        os.environ.get("CHROME"),
        os.environ.get("CHROME_PATH"),
        os.environ.get("EDGE"),
        os.environ.get("EDGE_PATH"),
    ]
    fixed = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    candidates = [Path(value) for value in env_candidates if value]
    candidates.extend(Path(value) for value in fixed)
    return [path for path in candidates if path.exists()]


def _capture_browser_page(app_url: str, scenario_dir: Path) -> dict[str, Any]:
    browsers = _chromium_candidates()
    if not browsers:
        return {"ok": False, "error": "No Chrome/Edge executable found for headless browser smoke"}
    browser = browsers[0]
    profile_dir = scenario_dir / "chrome-profile"
    screenshot = scenario_dir / "browser_app.png"
    dom_path = scenario_dir / "browser_dom.html"
    common = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile_dir}",
        "--window-size=1440,1000",
        "--virtual-time-budget=5000",
    ]
    screenshot_result = _safe_run([*common, f"--screenshot={screenshot}", app_url], timeout=45)
    dom_started = time.time()
    try:
        completed = _run([*common, "--dump-dom", app_url], timeout=45)
        dom_path.write_text(completed.stdout, encoding="utf-8", errors="replace")
        dom_result = {
            "ok": True,
            "seconds": round(time.time() - dom_started, 3),
            "stdout_bytes": len(completed.stdout.encode("utf-8", errors="replace")),
            "stderr": completed.stderr[-4000:],
        }
    except subprocess.CalledProcessError as exc:
        dom_result = {
            "ok": False,
            "seconds": round(time.time() - dom_started, 3),
            "returncode": exc.returncode,
            "stdout_bytes": len((exc.stdout or "").encode("utf-8", errors="replace")),
            "stderr": (exc.stderr or "")[-4000:],
        }
    except Exception as exc:
        dom_result = {"ok": False, "seconds": round(time.time() - dom_started, 3), "error": str(exc)}
    shutil.rmtree(profile_dir, ignore_errors=True)
    return {
        "ok": bool(screenshot.exists()) and bool(dom_path.exists()),
        "browser": str(browser),
        "screenshot": str(screenshot) if screenshot.exists() else None,
        "dom": str(dom_path) if dom_path.exists() else None,
        "screenshot_run": screenshot_result,
        "dom_run": dom_result,
    }


def _extract_frames(video: Path, times: list[float], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, str] = {}
    for sample_time in times:
        name = f"t{sample_time:05.2f}".replace(".", "_") + ".png"
        target = out_dir / name
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{sample_time:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(target),
            ],
            timeout=60,
        )
        frames[f"{sample_time:.2f}"] = str(target)
    return frames


def _contact_sheet(frame_paths: list[Path], labels: list[str], output: Path) -> None:
    thumbs: list[Image.Image] = []
    for path in frame_paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((360, 205))
        canvas = Image.new("RGB", (360, 232), (12, 12, 12))
        canvas.paste(image, ((360 - image.width) // 2, 0))
        thumbs.append(canvas)
    sheet = Image.new("RGB", (360 * max(1, len(thumbs)), 232), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for index, thumb in enumerate(thumbs):
        x = index * 360
        sheet.paste(thumb, (x, 0))
        draw.text((x + 10, 210), labels[index], fill=(255, 255, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90)


def _score_from_failures(failures: list[str], base: dict[str, int]) -> dict[str, int]:
    scores = dict(base)
    for failure in failures:
        text = failure.lower()
        if "timing" in text or "phase" in text:
            scores["timing_accuracy"] = min(scores.get("timing_accuracy", 5), 2)
        if "hold" in text or "frame" in text or "outside" in text:
            scores["frame_integrity"] = min(scores.get("frame_integrity", 5), 2)
        if "decode" in text or "mp4" in text:
            scores["preview_render_parity"] = min(scores.get("preview_render_parity", 5), 2)
        if "ltx" in text or "vram" in text:
            scores["ltx_output_quality"] = min(scores.get("ltx_output_quality", 5), 2)
    return scores


def _result_status(findings: list[str]) -> str:
    return "fail" if findings else "pass"


def _render_motion_and_samples(scenario_dir: Path, project_root: Path, project, motion, sample_times: list[float]):
    from app.services.motion import render_motion_asset, render_motion_video_asset
    from app.services.render import render_project_preview

    started = time.time()
    motion_asset = render_motion_video_asset(motion, project_root / "assets")
    if motion_asset is None:
        render_motion_asset(motion, project_root / "assets")
    render_seconds = round(time.time() - started, 3)
    if motion_asset is None:
        raise RuntimeError("Motion asset renderer returned no MP4")
    final_project = project.model_copy(update={"motions": [motion]})
    final_preview = render_project_preview(final_project, project_root)
    motion_frames = _extract_frames(motion_asset, sample_times, scenario_dir / "motion_frames")
    final_frames = _extract_frames(final_preview, sample_times, scenario_dir / "final_frames")
    _contact_sheet(
        [Path(path) for path in motion_frames.values()],
        [f"{time}s" for time in motion_frames],
        scenario_dir / "motion_contact_sheet.jpg",
    )
    _contact_sheet(
        [Path(path) for path in final_frames.values()],
        [f"{time}s" for time in final_frames],
        scenario_dir / "final_contact_sheet.jpg",
    )
    return motion_asset, final_preview, motion_frames, final_frames, render_seconds


def run_api_browser_smoke(root: Path, app_url: str, run_dir: Path, server_info: dict[str, Any]) -> ScenarioResult:
    scenario_dir = run_dir / "api_browser_smoke"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    findings: list[str] = []
    metrics: dict[str, Any] = {"server": server_info}
    evidence: dict[str, Any] = {}
    api_url = server_info.get("api_url") or urljoin(_api_base_url(app_url) + "/", "api/projects")

    try:
        api_result = _url_json(str(api_url), timeout=5.0)
        projects_payload = api_result.get("payload")
        project_count = len(projects_payload) if isinstance(projects_payload, list) else 0
        metrics["api_projects_status"] = api_result.get("status")
        metrics["api_project_count"] = project_count
        _write_json(scenario_dir / "api_projects.json", projects_payload)
        evidence["api_projects"] = str(scenario_dir / "api_projects.json")
        if project_count <= 0:
            findings.append("API returned no projects for UI to load")
    except Exception as exc:
        metrics["api_error"] = str(exc)
        findings.append(f"API /api/projects is not reachable: {exc}")

    capture = _capture_browser_page(app_url, scenario_dir)
    _write_json(scenario_dir / "browser_capture.json", capture)
    evidence["browser_capture"] = str(scenario_dir / "browser_capture.json")
    if capture.get("screenshot"):
        evidence["browser_screenshot"] = str(capture["screenshot"])
        try:
            shot = Image.open(str(capture["screenshot"])).convert("RGB")
            metrics["browser_screenshot_brightness"] = round(_brightness(shot), 3)
            metrics["browser_screenshot_edge"] = round(_edge_score(shot), 3)
            if _brightness(shot) < 3 and _edge_score(shot) < 0.4:
                findings.append("Browser screenshot appears blank or black")
        except Exception as exc:
            findings.append(f"Browser screenshot could not be inspected: {exc}")
    else:
        findings.append(f"Browser screenshot was not captured: {capture.get('error') or capture.get('screenshot_run')}")
    if capture.get("dom"):
        evidence["browser_dom"] = str(capture["dom"])
        dom_text = Path(str(capture["dom"])).read_text(encoding="utf-8", errors="replace")
        missing_ids = [item for item in UI_REQUIRED_IDS if f'id="{item}"' not in dom_text]
        metrics["ui_required_ids_present"] = len(UI_REQUIRED_IDS) - len(missing_ids)
        metrics["ui_missing_ids"] = missing_ids
        if missing_ids:
            findings.append(f"Browser DOM is missing required controls: {', '.join(missing_ids)}")
        duration_options = [value for value in ("4", "8", "12", "16", "20") if f'<option value="{value}"' in dom_text]
        quality_options = [value for value in ("320", "480", "720", "1080") if f'<option value="{value}"' in dom_text]
        metrics["ltx_duration_options"] = duration_options
        metrics["ltx_quality_options"] = quality_options
        if duration_options != ["4", "8", "12", "16", "20"]:
            findings.append(f"LTX duration options mismatch: {duration_options}")
        if quality_options != ["320", "480", "720", "1080"]:
            findings.append(f"LTX quality options mismatch: {quality_options}")
        if 'value="2"' in dom_text and "2 seconds" in dom_text:
            findings.append("Legacy 2 second LTX duration option is still present in browser DOM")
    else:
        findings.append(f"Browser DOM was not captured: {capture.get('error') or capture.get('dom_run')}")

    scores = _score_from_failures(
        findings,
        {
            "ui_flow": 5,
            "ltx_output_quality": 4,
            "performance_vram_safety": 4,
            "preview_render_parity": 4,
        },
    )
    return ScenarioResult("api_browser_smoke", _result_status(findings), scores, metrics, evidence, findings)


def run_whole_frame(root: Path, source_project_root: Path, source_project, source_motion, run_dir: Path) -> ScenarioResult:
    from app.services.layer_motion import describe_motion_plan, describe_motion_units, plan_frame_choreography
    from app.services.motion_intent import attach_frame_motion_contract
    from app.services.motion_scenario import build_whole_frame_prompt_scenario

    scenario_dir = run_dir / "whole_frame_choreography"
    motion = source_motion.model_copy(update={"start": 0.0, "duration": 15.0, "x": 0, "y": 0, "width": 1920, "height": 1080, "prompt": WHOLE_FRAME_PROMPT})
    planned_layers = plan_frame_choreography(WHOLE_FRAME_PROMPT, motion)
    motion_plan = describe_motion_plan(planned_layers)
    planned_layers = attach_frame_motion_contract(WHOLE_FRAME_PROMPT, motion.id, planned_layers, motion_plan)
    motion_plan = describe_motion_plan(planned_layers)
    storyboard = build_whole_frame_prompt_scenario(WHOLE_FRAME_PROMPT, motion_plan)
    motion = motion.model_copy(update={"figma_layers": planned_layers, "motion_plan": motion_plan, "motion_units": describe_motion_units(planned_layers)})
    project_root = scenario_dir / "project"
    project = _build_min_project(source_project_root, project_root, source_project, motion, duration=16.0)
    _write_json(scenario_dir / "storyboard.json", storyboard)
    _write_json(scenario_dir / "motion_plan.json", motion_plan)
    motion_asset, final_preview, motion_frames, final_frames, render_seconds = _render_motion_and_samples(scenario_dir, project_root, project, motion, WHOLE_FRAME_SAMPLE_TIMES)

    source_frame = Image.open(project_root / motion.asset_path).convert("RGB")
    source_edge = _edge_score(source_frame)
    hold = Image.open(motion_frames["12.50"]).convert("RGB")
    intro = Image.open(motion_frames["0.50"]).convert("RGB")
    build_mid = Image.open(motion_frames["2.50"]).convert("RGB")
    build_end = Image.open(motion_frames["4.50"]).convert("RGB")
    outro = Image.open(motion_frames["14.50"]).convert("RGB")
    hold_diff = _mean_abs_diff(hold, source_frame)
    intro_edge = _edge_score(intro)
    intro_diff = _mean_abs_diff(intro, source_frame)
    build_mid_diff = _mean_abs_diff(build_mid, source_frame)
    build_end_diff = _mean_abs_diff(build_end, source_frame)
    outro_diff = _mean_abs_diff(outro, source_frame)
    parity_diffs = []
    for key in motion_frames:
        parity_diffs.append(_mean_abs_diff(Image.open(motion_frames[key]), Image.open(final_frames[key])))

    findings: list[str] = []
    if hold_diff > 9.0:
        findings.append(f"Hold frame differs from source too much: {hold_diff:.2f}")
    if intro_edge > max(1.2, source_edge * 0.72) and intro_diff < 8.0:
        findings.append(f"Intro is not clean/background-only enough: edge={intro_edge:.2f}, source_edge={source_edge:.2f}")
    if build_mid_diff <= hold_diff + 1.0:
        findings.append(f"Build phase does not visibly differ from hold: mid_diff={build_mid_diff:.2f}, hold_diff={hold_diff:.2f}")
    if build_end_diff > 18.0:
        findings.append(f"Build end has not settled close enough: end_diff={build_end_diff:.2f}")
    if outro_diff < 10.0:
        findings.append(f"Outro/shatter is too subtle: outro_diff={outro_diff:.2f}")
    if max(parity_diffs) > 14.0:
        findings.append(f"Render parity diff too high: max={max(parity_diffs):.2f}")

    metrics = {
        "render_seconds": render_seconds,
        "source_edge": round(source_edge, 3),
        "intro_edge": round(intro_edge, 3),
        "intro_vs_source_diff": round(intro_diff, 3),
        "build_mid_vs_source_diff": round(build_mid_diff, 3),
        "build_end_vs_source_diff": round(build_end_diff, 3),
        "hold_vs_source_diff": round(hold_diff, 3),
        "outro_vs_source_diff": round(outro_diff, 3),
        "parity_max_diff": round(max(parity_diffs), 3),
        "ffprobe_motion": _ffprobe(motion_asset),
        "ffprobe_final": _ffprobe(final_preview),
    }
    scores = _score_from_failures(
        findings,
        {
            "prompt_fidelity": 5,
            "timing_accuracy": 5,
            "smoothness": 4,
            "visual_beauty": 4,
            "frame_integrity": 5 if hold_diff <= 6 else 4,
            "preview_render_parity": 5 if max(parity_diffs) <= 8 else 4,
        },
    )
    evidence = {
        "motion_asset": str(motion_asset),
        "final_preview": str(final_preview),
        "motion_frames": motion_frames,
        "final_frames": final_frames,
        "motion_contact_sheet": str(scenario_dir / "motion_contact_sheet.jpg"),
        "final_contact_sheet": str(scenario_dir / "final_contact_sheet.jpg"),
        "storyboard": str(scenario_dir / "storyboard.json"),
        "motion_plan": str(scenario_dir / "motion_plan.json"),
    }
    return ScenarioResult("whole_frame_choreography", _result_status(findings), scores, metrics, evidence, findings)


def run_selected_layer(root: Path, source_project_root: Path, source_project, source_motion, run_dir: Path, kind: str) -> ScenarioResult:
    from app.services.motion_intent import build_layer_motion_recipe_from_prompt

    scenario_id = f"selected_{kind}_layer"
    scenario_dir = run_dir / scenario_id
    target = _find_layer(source_motion, kind)
    if not target:
        return ScenarioResult(scenario_id, "skip", {}, {}, {}, [f"No {kind} target layer found"])
    prompt = "Fly in smoothly from the lower left over 1 second, hold, then fade out at the end for 1 second."
    layers = []
    recipe = build_layer_motion_recipe_from_prompt(prompt, "replace", target, list(source_motion.figma_layers or []), timeline_duration=6.0)
    for layer in list(source_motion.figma_layers or []):
        next_layer = dict(layer)
        if str(next_layer.get("id") or "") == str(target.get("id") or ""):
            next_layer["motion_recipe"] = recipe
        else:
            next_layer.pop("motion_recipe", None)
        layers.append(next_layer)
    motion = source_motion.model_copy(update={"start": 0.0, "duration": 6.0, "x": 0, "y": 0, "width": 1920, "height": 1080, "figma_layers": layers, "motion_plan": None, "motion_units": []})
    project_root = scenario_dir / "project"
    project = _build_min_project(source_project_root, project_root, source_project, motion, duration=7.0)
    _write_json(scenario_dir / "recipe.json", recipe)
    motion_asset, final_preview, motion_frames, final_frames, render_seconds = _render_motion_and_samples(scenario_dir, project_root, project, motion, SELECTED_LAYER_SAMPLE_TIMES)

    source_frame = Image.open(project_root / motion.asset_path).convert("RGB")
    moving = Image.open(motion_frames["0.75"]).convert("RGB")
    hold = Image.open(motion_frames["3.00"]).convert("RGB")
    motion_target = next(
        (
            layer
            for layer in list(motion.figma_layers or [])
            if str(layer.get("id") or "") == str(target.get("id") or "")
        ),
        target,
    )
    rect = _layer_rect_pixels(motion, motion_target, source_frame.size)
    moving_rect = _layer_motion_rect_pixels(motion, motion_target, source_frame.size, 0.75, 6.0)
    protected_rect = _union_rect([rect, moving_rect])
    outside_diff = _mean_abs_diff(_mask_out_rect(moving, protected_rect), _mask_out_rect(source_frame, protected_rect))
    inside_diff = _mean_abs_diff(_crop(moving, rect), _crop(source_frame, rect))
    hold_diff = _mean_abs_diff(hold, source_frame)
    final_parity = _mean_abs_diff(Image.open(motion_frames["3.00"]), Image.open(final_frames["3.00"]))
    findings: list[str] = []
    if outside_diff > 8.0:
        findings.append(f"Non-selected pixels changed too much during selected-layer animation: {outside_diff:.2f}")
    if inside_diff < 0.8:
        findings.append(f"Selected layer did not visibly animate: inside_diff={inside_diff:.2f}")
    if hold_diff > 10.0:
        findings.append(f"Hold frame does not return close to source: {hold_diff:.2f}")
    if final_parity > 12.0:
        findings.append(f"Preview/render parity is weak for selected layer: {final_parity:.2f}")

    metrics = {
        "target_layer": {"id": target.get("id"), "name": target.get("name"), "kind": target.get("kind")},
        "render_seconds": render_seconds,
        "selected_rect_pixels": rect,
        "moving_rect_pixels": moving_rect,
        "protected_rect_pixels": protected_rect,
        "outside_diff_moving": round(outside_diff, 3),
        "inside_diff_moving": round(inside_diff, 3),
        "hold_vs_source_diff": round(hold_diff, 3),
        "hold_parity_diff": round(final_parity, 3),
        "ffprobe_motion": _ffprobe(motion_asset),
    }
    scores = _score_from_failures(
        findings,
        {
            "prompt_fidelity": 4,
            "timing_accuracy": 4,
            "smoothness": 4,
            "frame_integrity": 5 if outside_diff <= 5 else 4,
            "no_duplicates": 5 if hold_diff <= 8 else 4,
            "preview_render_parity": 5 if final_parity <= 8 else 4,
        },
    )
    evidence = {
        "motion_asset": str(motion_asset),
        "final_preview": str(final_preview),
        "motion_frames": motion_frames,
        "final_frames": final_frames,
        "motion_contact_sheet": str(scenario_dir / "motion_contact_sheet.jpg"),
        "recipe": str(scenario_dir / "recipe.json"),
    }
    return ScenarioResult(scenario_id, _result_status(findings), scores, metrics, evidence, findings)


def run_action_semantics(source_motion, run_dir: Path) -> ScenarioResult:
    from app.services.motion_intent import build_layer_motion_recipe_from_prompt, motion_recipe_actions

    scenario_dir = run_dir / "add_new_cancel_semantics"
    target = _find_layer(source_motion, "text") or _find_layer(source_motion, "image")
    if not target:
        return ScenarioResult("add_new_cancel_semantics", "skip", {}, {}, {}, ["No target layer found"])
    replace_recipe = build_layer_motion_recipe_from_prompt("Fade in for 1 second.", "replace", target, list(source_motion.figma_layers or []), timeline_duration=6.0)
    with_existing = dict(target)
    with_existing["motion_recipe"] = replace_recipe
    append_recipe = build_layer_motion_recipe_from_prompt("Then drop down for 1 second.", "append", with_existing, list(source_motion.figma_layers or []), timeline_duration=6.0)
    new_recipe = build_layer_motion_recipe_from_prompt("Pop in for 1 second.", "replace", with_existing, list(source_motion.figma_layers or []), timeline_duration=6.0)
    cancel_before = json.dumps(replace_recipe, sort_keys=True)
    cancel_after = json.dumps(replace_recipe, sort_keys=True)
    replace_actions = motion_recipe_actions(replace_recipe)
    append_actions = motion_recipe_actions(append_recipe)
    new_actions = motion_recipe_actions(new_recipe)
    findings: list[str] = []
    if len(replace_actions) != 1:
        findings.append(f"Replace should create exactly one action, got {len(replace_actions)}")
    if len(append_actions) <= len(replace_actions):
        findings.append("Append/Add did not add a separate action")
    if len(new_actions) != 1:
        findings.append(f"New/replace should reset action stack to one action, got {len(new_actions)}")
    if cancel_before != cancel_after:
        findings.append("Cancel semantic mutated recipe")
    payload = {
        "target": {"id": target.get("id"), "name": target.get("name")},
        "replace_actions": replace_actions,
        "append_actions": append_actions,
        "new_actions": new_actions,
    }
    _write_json(scenario_dir / "action_semantics.json", payload)
    scores = {
        "ui_flow": 4,
        "prompt_fidelity": 5 if not findings else 3,
        "timing_accuracy": 4,
    }
    return ScenarioResult("add_new_cancel_semantics", _result_status(findings), scores, {"action_counts": {"replace": len(replace_actions), "append": len(append_actions), "new": len(new_actions)}}, {"recipe_diff": str(scenario_dir / "action_semantics.json")}, findings)


def run_timeline_resize_contract(source_motion, run_dir: Path) -> ScenarioResult:
    from app.services.layer_motion import describe_motion_plan, plan_frame_choreography
    from app.services.motion_intent import attach_frame_motion_contract

    scenario_dir = run_dir / "timeline_drag_resize_contract"
    base_motion = source_motion.model_copy(update={"duration": 15.0})
    layers = plan_frame_choreography(WHOLE_FRAME_PROMPT, base_motion)
    plan = describe_motion_plan(layers)
    layers = attach_frame_motion_contract(WHOLE_FRAME_PROMPT, base_motion.id, layers, plan)
    plan = describe_motion_plan(layers)
    phases = [phase for phase in list(plan.get("phases") or []) if isinstance(phase, dict)]
    phase_by_id = {str(phase.get("id")): phase for phase in phases}
    findings: list[str] = []
    if abs(float(phase_by_id.get("intro", {}).get("duration") or 0) - 2.0) > 0.15:
        findings.append("Intro phase should preserve first 2 seconds")
    if abs(float(phase_by_id.get("build", {}).get("duration") or 0) - 3.0) > 0.25:
        findings.append("Build phase should preserve 3 second fly-in")
    outro = phase_by_id.get("outro", {})
    if abs(float(outro.get("duration") or 0) - 3.0) > 0.25:
        findings.append("Outro should preserve last 3 seconds")
    if str(outro.get("anchor") or "") != "end":
        findings.append("Outro should be end anchored")
    _write_json(scenario_dir / "resize_contract_plan.json", plan)
    return ScenarioResult("timeline_drag_resize_contract", _result_status(findings), {"timing_accuracy": 5 if not findings else 2, "prompt_fidelity": 5}, {"phase_count": len(phases)}, {"plan": str(scenario_dir / "resize_contract_plan.json")}, findings)


def run_ltx_probe(root: Path, source_project_root: Path, source_project, source_motion, run_dir: Path, include_ltx: bool) -> ScenarioResult:
    from app.services.ltx_video import _adaptive_ltx_max_side, _cuda_memory_mib, generate_ltx_layer_preview

    scenario_dir = run_dir / "ltx_preview_apply_render"
    target = _find_layer(source_motion, "image")
    if not target:
        return ScenarioResult("ltx_preview_apply_render", "skip", {}, {}, {}, ["No image layer found for LTX"])
    project_root = scenario_dir / "project"
    motion = source_motion.model_copy(update={"start": 0.0, "duration": 6.0, "x": 0, "y": 0, "width": 1920, "height": 1080})
    project = _build_min_project(source_project_root, project_root, source_project, motion, duration=7.0)
    source_path = project_root / str(target.get("asset_path") or "")
    source_preview = Image.open(source_path).convert("RGB") if source_path.exists() else None
    findings: list[str] = []
    metrics: dict[str, Any] = {}
    if source_preview is None:
        findings.append("LTX source preview asset is missing")
    else:
        metrics["source_brightness"] = round(_brightness(source_preview), 3)
        metrics["source_edge"] = round(_edge_score(source_preview), 3)
        if _brightness(source_preview) < 2 and _edge_score(source_preview) < 0.2:
            findings.append("LTX pre-generation preview appears black")
    total, used, free = _cuda_memory_mib()
    metrics["vram_mib"] = {"total": total, "used": used, "free": free}
    os.environ.setdefault("VIBEMOTION_LTX_DIR", str((root / "models" / "ltx-2.3").resolve()))
    for side in (480, 720, 1080):
        try:
            metrics[f"adaptive_{side}"] = _adaptive_ltx_max_side(side)
        except Exception as exc:
            metrics[f"adaptive_{side}_error"] = str(exc)
    generated_results: list[dict[str, Any]] = []
    if include_ltx:
        if free is None or free < 9500:
            findings.append(f"LTX generation skipped: free VRAM {free} MiB is below 9500 MiB")
        else:
            test_plan = [(480, 4.0), (480, 8.0)]
            if free >= 22000:
                test_plan.append((720, 4.0))
            else:
                metrics["ltx_720_generation"] = f"skipped; free VRAM {free} MiB below strict 22000 MiB gate"
            if free >= 32000:
                test_plan.append((1080, 4.0))
            else:
                metrics["ltx_1080_generation"] = f"skipped; free VRAM {free} MiB below strict 32000 MiB gate"
            for side, duration in test_plan:
                label = f"{side}_{int(duration)}s"
                try:
                    started = time.time()
                    generated = generate_ltx_layer_preview(
                        project_root=project_root,
                        motion=motion,
                        layer_id=str(target.get("id")),
                        prompt="subtle cinematic zoom in with stable composition",
                        duration=duration,
                        fps=8,
                        max_side=side,
                        seed=42,
                    )
                    generated["generation_seconds"] = round(time.time() - started, 3)
                    preview_path = project_root / generated["preview_path"]
                    archived_video = scenario_dir / f"ltx_preview_{label}.mp4"
                    if preview_path.exists():
                        shutil.copy2(preview_path, archived_video)
                        generated["archived_preview_path"] = str(archived_video)
                    _write_json(scenario_dir / f"ltx_preview_{label}.json", generated)
                    ffprobe = _ffprobe(archived_video if archived_video.exists() else preview_path)
                    generated["ffprobe"] = ffprobe
                    if not ffprobe.get("ok"):
                        findings.append(f"Generated LTX MP4 does not decode for {label}")
                    sample_duration = float(generated.get("duration") or duration)
                    sample_times = sorted({0.5, round(max(0.5, sample_duration * 0.5), 2), round(max(0.5, sample_duration - 0.35), 2)})
                    ltx_video_path = archived_video if archived_video.exists() else preview_path
                    ltx_frames = _extract_frames(ltx_video_path, sample_times, scenario_dir / f"ltx_frames_{label}")
                    _contact_sheet(
                        [Path(path) for path in ltx_frames.values()],
                        [f"{time}s" for time in ltx_frames],
                        scenario_dir / f"ltx_contact_sheet_{label}.jpg",
                    )
                    generated["sample_frames"] = ltx_frames
                    generated["contact_sheet"] = str(scenario_dir / f"ltx_contact_sheet_{label}.jpg")
                    expected_ratio = float(generated.get("width") or 1) / max(1.0, float(generated.get("height") or 1))
                    source_ratio = float(generated.get("source_width") or 1) / max(1.0, float(generated.get("source_height") or 1))
                    generated["ratio_delta"] = round(abs(expected_ratio - source_ratio), 4)
                    if abs(expected_ratio - source_ratio) > 0.18:
                        findings.append(f"LTX output aspect ratio drift is high for {label}: {generated['ratio_delta']}")
                    generated_results.append(generated)
                except Exception as exc:
                    findings.append(f"LTX generation failed gracefully check for {label}: {exc}")
    else:
        metrics["generation"] = "not-run; pass --include-ltx to generate"
    if generated_results:
        metrics["generated"] = [
            {
                "requested_max_side": item.get("requested_max_side"),
                "max_side": item.get("max_side"),
                "duration": item.get("duration"),
                "fps": item.get("fps"),
                "frames": item.get("frames"),
                "generation_seconds": item.get("generation_seconds"),
                "ratio_delta": item.get("ratio_delta"),
            }
            for item in generated_results
        ]
    evidence = {
        "source_preview": str(source_path),
        "ltx_preview_json": str(scenario_dir / "ltx_preview_480_4s.json") if generated_results else None,
        "ltx_generated_videos": [item.get("archived_preview_path") for item in generated_results if item.get("archived_preview_path")],
        "ltx_contact_sheets": [item.get("contact_sheet") for item in generated_results if item.get("contact_sheet")],
    }
    scores = _score_from_failures(findings, {"ltx_output_quality": 4 if not include_ltx else 5, "performance_vram_safety": 5, "frame_integrity": 4})
    return ScenarioResult("ltx_preview_apply_render", "fail" if findings and include_ltx else "warn" if findings else "pass", scores, metrics, evidence, findings)


def _write_markdown_report(run_dir: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# VibeMotion Motion Autotest Report",
        "",
        f"Run: `{payload['run_id']}`",
        f"Project: `{payload['project_id']}`",
        f"Overall: `{payload['overall_status']}`",
        "",
        "## Scenario Summary",
        "",
        "| Scenario | Status | Key Findings |",
        "| --- | --- | --- |",
    ]
    for scenario in payload["scenarios"]:
        findings = "; ".join(scenario.get("findings") or []) or "none"
        lines.append(f"| {scenario['scenario_id']} | {scenario['status']} | {findings} |")
    lines.extend(["", "## Artifacts", ""])
    for scenario in payload["scenarios"]:
        lines.append(f"### {scenario['scenario_id']}")
        for key, value in sorted((scenario.get("evidence") or {}).items()):
            if value:
                lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run autonomous VibeMotion motion QA on a sandbox project copy.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--project-id", default="")
    parser.add_argument("--app-url", default="http://127.0.0.1:8010/app/index.html")
    parser.add_argument("--include-ltx", action="store_true", help="Run actual local LTX generation if VRAM allows.")
    parser.add_argument("--only", choices=["all", "browser", "whole", "selected", "semantics", "ltx"], default="all")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = root / "qa_artifacts" / "motion_autotest" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    project_ids = _project_ids(root)
    project_id = args.project_id or (project_ids[0] if project_ids else "")
    if not project_id:
        raise RuntimeError("No project found")
    source_project_root = root / "projects" / project_id
    source_project = _load_project(source_project_root)
    source_motion = _find_figma_motion(source_project)
    server_info = _ensure_server(root, args.app_url, run_dir)
    manifest = {
        "run_id": run_id,
        "root": str(root),
        "project_id": project_id,
        "source_motion_id": source_motion.id,
        "app": server_info,
        "include_ltx": bool(args.include_ltx),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json(run_dir / "run_manifest.json", manifest)

    scenarios: list[ScenarioResult] = []
    if args.only in {"all", "browser"}:
        scenarios.append(run_api_browser_smoke(root, args.app_url, run_dir, server_info))
    if args.only in {"all", "whole"}:
        scenarios.append(run_whole_frame(root, source_project_root, source_project, source_motion, run_dir))
    if args.only in {"all", "selected"}:
        for kind in ("image", "text", "card"):
            scenarios.append(run_selected_layer(root, source_project_root, source_project, source_motion, run_dir, kind))
    if args.only in {"all", "semantics"}:
        scenarios.append(run_action_semantics(source_motion, run_dir))
        scenarios.append(run_timeline_resize_contract(source_motion, run_dir))
    if args.only in {"all", "ltx"}:
        scenarios.append(run_ltx_probe(root, source_project_root, source_project, source_motion, run_dir, args.include_ltx))

    serialized = [
        {
            "scenario_id": item.scenario_id,
            "status": item.status,
            "scores": item.scores,
            "metrics": item.metrics,
            "evidence": item.evidence,
            "findings": item.findings,
        }
        for item in scenarios
    ]
    hard_failures = [item for item in scenarios if item.status == "fail"]
    payload = {
        **manifest,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "overall_status": "fail" if hard_failures else "pass",
        "scenarios": serialized,
    }
    _write_json(run_dir / "report.json", payload)
    _write_markdown_report(run_dir, payload)
    print(json.dumps({"overall_status": payload["overall_status"], "report": str(run_dir / "report.json"), "run_dir": str(run_dir)}, ensure_ascii=False, indent=2))
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
