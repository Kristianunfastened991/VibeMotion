from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_URL = "http://127.0.0.1:8010/app/index.html"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_now()}] {message}\n")


def _latest_project_id() -> str:
    projects_dir = ROOT / "projects"
    candidates = [
        item
        for item in projects_dir.iterdir()
        if item.is_dir() and (item / "project.json").exists()
    ]
    if not candidates:
        raise RuntimeError("No projects with project.json found")
    candidates.sort(key=lambda item: (item / "project.json").stat().st_mtime, reverse=True)
    return candidates[0].name


def _app_is_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8010/api/projects", timeout=3) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _ensure_app(run_dir: Path, log_path: Path) -> dict[str, Any]:
    if _app_is_up():
        return {"status": "already-running", "url": DEFAULT_APP_URL}
    python_exe = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)
    server_log = run_dir / "server.log"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with server_log.open("ab") as handle:
        proc = subprocess.Popen(
            [
                str(python_exe),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8010",
            ],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    deadline = time.time() + 15
    while time.time() < deadline:
        if _app_is_up():
            _append_log(log_path, f"Started app server pid={proc.pid}")
            return {"status": "started", "pid": proc.pid, "url": DEFAULT_APP_URL, "log": str(server_log)}
        time.sleep(1)
    return {"status": "start-timeout", "pid": proc.pid, "url": DEFAULT_APP_URL, "log": str(server_log)}


def _base_env(project_id: str, app_url: str, artifact_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["VIBEMOTION_QA_PROJECT"] = project_id
    env["VIBEMOTION_QA_URL"] = f"{app_url}?project={project_id}&fresh={int(time.time())}"
    env["VIBEMOTION_QA_ARTIFACT_DIR"] = str(artifact_dir)
    env.setdefault("VIBEMOTION_LTX_DIR", str((ROOT / "models" / "ltx-2.3").resolve()))
    return env


def _run_command(
    *,
    name: str,
    command: list[str],
    run_dir: Path,
    project_id: str,
    app_url: str,
    timeout: int,
) -> dict[str, Any]:
    step_dir = run_dir / "steps" / f"{int(time.time())}_{name}"
    step_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result: dict[str, Any] = {
        "name": name,
        "kind": "command",
        "command": command,
        "started_at": _now(),
        "step_dir": str(step_dir),
    }
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=_base_env(project_id, app_url, step_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        (step_dir / "stdout.txt").write_text(completed.stdout or "", encoding="utf-8", errors="replace")
        (step_dir / "stderr.txt").write_text(completed.stderr or "", encoding="utf-8", errors="replace")
        result.update(
            {
                "ok": completed.returncode == 0,
                "returncode": completed.returncode,
                "stdout_tail": (completed.stdout or "")[-2500:],
                "stderr_tail": (completed.stderr or "")[-2500:],
            }
        )
    except subprocess.TimeoutExpired as exc:
        (step_dir / "stdout.txt").write_text(exc.stdout or "", encoding="utf-8", errors="replace")
        (step_dir / "stderr.txt").write_text(exc.stderr or "", encoding="utf-8", errors="replace")
        result.update({"ok": False, "timeout": timeout, "error": f"timeout after {timeout}s"})
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
    result["seconds"] = round(time.time() - started, 3)
    result["finished_at"] = _now()
    _write_json(step_dir / "result.json", result)
    return result


def _run_current_ltx_composite_check(run_dir: Path, project_id: str) -> dict[str, Any]:
    step_dir = run_dir / "steps" / f"{int(time.time())}_current_ltx_composite"
    step_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result: dict[str, Any] = {
        "name": "current_ltx_composite",
        "kind": "python-check",
        "started_at": _now(),
        "step_dir": str(step_dir),
    }
    try:
        project_root = ROOT / "projects" / project_id
        payload = json.loads((project_root / "project.json").read_text(encoding="utf-8"))
        motion = next(
            item
            for item in payload.get("motions", [])
            if item.get("source_type") == "figma"
            and item.get("asset_path")
            and item.get("video_asset_path")
            and any(layer.get("ltx_video_path") for layer in item.get("figma_layers", []))
        )
        source_path = project_root / motion["asset_path"]
        video_path = project_root / motion["video_asset_path"]
        frame_path = step_dir / "ltx_composite_frame.png"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "0.300", "-i", str(video_path), "-frames:v", "1", str(frame_path)],
            check=True,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        with Image.open(source_path).convert("RGB") as source, Image.open(frame_path).convert("RGB") as frame:
            if frame.size != source.size:
                frame = frame.resize(source.size, Image.Resampling.LANCZOS)
            # Text-heavy left side must stay pixel-faithful after LTX compositing.
            crop = (0, 0, min(920, source.width), min(1020, source.height))
            diff = ImageChops.difference(source.crop(crop), frame.crop(crop))
            stat = ImageStat.Stat(diff)
            text_region_mean_diff = float(sum(stat.mean) / max(1, len(stat.mean)))
        metrics = {
            "motion_id": motion.get("id"),
            "source": str(source_path),
            "video": str(video_path),
            "frame": str(frame_path),
            "text_region_mean_diff": round(text_region_mean_diff, 4),
        }
        result.update({"ok": text_region_mean_diff <= 5.0, "metrics": metrics})
        if not result["ok"]:
            result["error"] = "LTX composite changed the static text region too much"
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
    result["seconds"] = round(time.time() - started, 3)
    result["finished_at"] = _now()
    _write_json(step_dir / "result.json", result)
    return result


def _run_current_browser_preview_check(run_dir: Path, project_id: str, app_url: str) -> dict[str, Any]:
    step_dir = run_dir / "steps" / f"{int(time.time())}_current_browser_preview"
    step_dir.mkdir(parents=True, exist_ok=True)
    script = step_dir / "browser_preview_check.js"
    script.write_text(
        r'''
const fs = require("node:fs");
const path = require("node:path");
const ROOT = process.cwd();
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (_error) {
  ({ chromium } = require(path.join(ROOT, "tmp", "pw", "node_modules", "playwright")));
}
const projectId = process.env.VIBEMOTION_QA_PROJECT;
const appUrl = process.env.VIBEMOTION_QA_APP_URL || "http://127.0.0.1:8010/app/index.html";
const outDir = process.env.VIBEMOTION_QA_ARTIFACT_DIR;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
    await page.goto(`${appUrl}?project=${projectId}&fresh=${Date.now()}`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForFunction(() => typeof project !== "undefined" && project?.motions?.length > 0, null, { timeout: 15000 });
    await page.waitForFunction(() => {
      const box = document.querySelector("#previewMotionBox");
      const video = box?.querySelector("video[data-figma-motion-video]");
      return video && video.readyState >= 1;
    }, null, { timeout: 12000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const data = await page.evaluate(() => {
      const box = document.querySelector("#previewMotionBox");
      const video = box?.querySelector("video[data-figma-motion-video]");
      return {
        boxClass: box?.className || "",
        boxMotionId: box?.dataset.motionId || "",
        figmaMotionVideoCount: box?.querySelectorAll("video[data-figma-motion-video]").length || 0,
        directLtxVideoCount: box?.querySelectorAll("video[data-ltx-video]").length || 0,
        textLayerCount: box?.querySelectorAll(".editor-figma-layer.text, .editor-figma-animated-layer.text").length || 0,
        videoReadyState: video?.readyState || 0,
        videoWidth: video?.videoWidth || 0,
        videoHeight: video?.videoHeight || 0,
        videoSrc: video?.currentSrc || video?.src || "",
      };
    });
    await page.screenshot({ path: path.join(outDir, "browser_preview.png"), fullPage: false });
    fs.writeFileSync(path.join(outDir, "browser_preview.json"), JSON.stringify(data, null, 2));
    const failed = [];
    if (data.figmaMotionVideoCount < 1) failed.push("missing data-figma-motion-video preview");
    if (data.directLtxVideoCount > 0) failed.push("preview uses direct LTX layer instead of composed motion video");
    if (data.textLayerCount > 0) failed.push("preview reconstructed Figma text layers");
    if (data.videoReadyState < 1) failed.push("motion video metadata did not load");
    console.log(JSON.stringify({ failed, data }, null, 2));
    process.exitCode = failed.length ? 1 : 0;
  } finally {
    await browser.close().catch(() => {});
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
''',
        encoding="utf-8",
    )
    env = _base_env(project_id, app_url, step_dir)
    env["VIBEMOTION_QA_APP_URL"] = app_url
    started = time.time()
    result = {
        "name": "current_browser_preview",
        "kind": "node-check",
        "started_at": _now(),
        "step_dir": str(step_dir),
    }
    try:
        completed = subprocess.run(
            ["node", str(script)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        (step_dir / "stdout.txt").write_text(completed.stdout or "", encoding="utf-8", errors="replace")
        (step_dir / "stderr.txt").write_text(completed.stderr or "", encoding="utf-8", errors="replace")
        result.update(
            {
                "ok": completed.returncode == 0,
                "returncode": completed.returncode,
                "stdout_tail": (completed.stdout or "")[-2500:],
                "stderr_tail": (completed.stderr or "")[-2500:],
            }
        )
    except subprocess.TimeoutExpired:
        result.update({"ok": False, "timeout": 45, "error": "timeout after 45s"})
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
    result["seconds"] = round(time.time() - started, 3)
    result["finished_at"] = _now()
    _write_json(step_dir / "result.json", result)
    return result


def _step_matrix(include_ltx: bool) -> list[dict[str, Any]]:
    node_js_syntax = (
        "const fs=require('fs');"
        "const html=fs.readFileSync('app/static/index.html','utf8');"
        "const scripts=[...html.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/gi)].map(m=>m[1]).join('\\n');"
        "new Function(scripts);"
        "console.log('index inline JS syntax OK');"
    )
    steps: list[dict[str, Any]] = [
        {
            "name": "py_compile_core",
            "command": [
                sys.executable,
                "-m",
                "py_compile",
                "app/services/motion.py",
                "app/services/ltx_video.py",
                "app/api/routes.py",
                "app/models/schemas.py",
                "scripts/qa_ltx_contract.py",
                "scripts/qa_timeline_clip_edit.py",
            ],
            "timeout": 45,
        },
        {"name": "index_js_syntax", "command": ["node", "-e", node_js_syntax], "timeout": 45},
        {"name": "ltx_contract", "command": [sys.executable, "scripts/qa_ltx_contract.py"], "timeout": 60},
        {"name": "timeline_clip_edit", "command": [sys.executable, "scripts/qa_timeline_clip_edit.py"], "timeout": 60},
        {"name": "motion_autotest_semantics", "command": [sys.executable, "scripts/motion_autotest.py", "--only", "semantics"], "timeout": 180},
        {"name": "motion_autotest_selected", "command": [sys.executable, "scripts/motion_autotest.py", "--only", "selected"], "timeout": 420},
        {"name": "motion_autotest_whole", "command": [sys.executable, "scripts/motion_autotest.py", "--only", "whole"], "timeout": 360},
        {"name": "motion_autotest_ltx_probe", "command": [sys.executable, "scripts/motion_autotest.py", "--only", "ltx"], "timeout": 180},
        {"name": "browser_smoke_ltx_ui", "command": ["node", "scripts/qa_vibemotion_browser.js"], "timeout": 120},
        {"name": "responsive_ltx_modal", "command": ["node", "scripts/qa_responsive_ltx.js"], "timeout": 120},
        {"name": "failure_and_timeline_interactions", "command": ["node", "scripts/qa_vibemotion_interactions.js"], "timeout": 180},
        {"name": "native_motion_cue_ui", "command": ["node", "scripts/qa_native_motion_cue_ui.js"], "timeout": 240},
        {"name": "minimal_regression", "command": [sys.executable, "scripts/qa_vibemotion_minimal_regression.py", "--asset-limit", "4", "--render-limit", "3"], "timeout": 360},
    ]
    if include_ltx:
        steps.append(
            {
                "name": "motion_autotest_ltx_generation_once",
                "command": [sys.executable, "scripts/motion_autotest.py", "--only", "ltx", "--include-ltx"],
                "timeout": 3000,
                "once": True,
            }
        )
    return steps


def _write_progress(run_dir: Path, progress: dict[str, Any]) -> None:
    _write_json(run_dir / "progress.json", progress)


def _write_report(run_dir: Path, progress: dict[str, Any], results: list[dict[str, Any]]) -> None:
    failures = [item for item in results if not item.get("ok")]
    lines = [
        "# One Hour VibeMotion QA Soak",
        "",
        f"Started: `{progress['started_at']}`",
        f"Finished: `{progress.get('finished_at', '')}`",
        f"Project: `{progress['project_id']}`",
        f"Elapsed seconds: `{progress.get('elapsed_seconds', 0)}`",
        f"Steps: `{len(results)}`",
        f"Failures: `{len(failures)}`",
        "",
        "## Results",
        "",
        "| # | Step | OK | Seconds | Artifact |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(results, start=1):
        lines.append(
            f"| {index} | `{item.get('name')}` | `{item.get('ok')}` | `{item.get('seconds')}` | `{item.get('step_dir')}` |"
        )
    if failures:
        lines.extend(["", "## Failures", ""])
        for item in failures:
            detail = item.get("error") or item.get("stderr_tail") or item.get("stdout_tail") or "unknown"
            lines.append(f"- `{item.get('name')}`: {str(detail).strip()[:700]}")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    _write_json(run_dir / "summary.json", {**progress, "failure_count": len(failures), "results": results})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an uninterrupted VibeMotion QA soak for a fixed duration.")
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--project-id", default="")
    parser.add_argument("--app-url", default=DEFAULT_APP_URL)
    parser.add_argument("--include-ltx", action="store_true")
    args = parser.parse_args()

    project_id = args.project_id or _latest_project_id()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "qa_artifacts" / "one_hour_soak" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "live.log"
    deadline = time.time() + max(1.0, args.minutes * 60.0)
    app = _ensure_app(run_dir, log_path)
    steps = _step_matrix(args.include_ltx)
    completed_once: set[str] = set()
    results: list[dict[str, Any]] = []
    progress = {
        "run_id": run_id,
        "project_id": project_id,
        "run_dir": str(run_dir),
        "started_at": _now(),
        "deadline_epoch": deadline,
        "deadline_local": datetime.fromtimestamp(deadline).strftime("%Y-%m-%dT%H:%M:%S"),
        "include_ltx": bool(args.include_ltx),
        "app": app,
        "status": "running",
        "elapsed_seconds": 0,
        "step_count": 0,
        "failure_count": 0,
        "current_step": None,
        "last_step": None,
    }
    _write_progress(run_dir, progress)
    _append_log(log_path, f"QA soak started for project={project_id}, minutes={args.minutes}, include_ltx={args.include_ltx}")

    step_index = 0
    try:
        while time.time() < deadline:
            step = steps[step_index % len(steps)]
            step_index += 1
            if step.get("once") and step["name"] in completed_once:
                continue
            remaining = deadline - time.time()
            if remaining < 10:
                break
            progress["current_step"] = step["name"]
            progress["elapsed_seconds"] = round(time.time() - (deadline - max(1.0, args.minutes * 60.0)), 1)
            _write_progress(run_dir, progress)
            _append_log(log_path, f"START {step['name']}")
            if step["name"] == "current_ltx_composite":
                result = _run_current_ltx_composite_check(run_dir, project_id)
            else:
                timeout = int(min(float(step.get("timeout", 120)), max(10.0, remaining - 2)))
                result = _run_command(
                    name=step["name"],
                    command=list(step["command"]),
                    run_dir=run_dir,
                    project_id=project_id,
                    app_url=args.app_url,
                    timeout=timeout,
                )
            completed_once.add(step["name"])
            results.append(result)
            progress["last_step"] = {
                "name": result.get("name"),
                "ok": result.get("ok"),
                "seconds": result.get("seconds"),
                "step_dir": result.get("step_dir"),
                "error": result.get("error"),
            }
            progress["current_step"] = None
            progress["step_count"] = len(results)
            progress["failure_count"] = len([item for item in results if not item.get("ok")])
            progress["elapsed_seconds"] = round(time.time() - (deadline - max(1.0, args.minutes * 60.0)), 1)
            _write_progress(run_dir, progress)
            _append_log(log_path, f"END {result.get('name')} ok={result.get('ok')} seconds={result.get('seconds')}")

            if step["name"] == "timeline_clip_edit":
                # Keep the regression tied to the live current LTX case after core contracts pass.
                for direct in (
                    _run_current_ltx_composite_check(run_dir, project_id),
                    _run_current_browser_preview_check(run_dir, project_id, args.app_url),
                ):
                    results.append(direct)
                    progress["last_step"] = {
                        "name": direct.get("name"),
                        "ok": direct.get("ok"),
                        "seconds": direct.get("seconds"),
                        "step_dir": direct.get("step_dir"),
                        "error": direct.get("error"),
                    }
                    progress["step_count"] = len(results)
                    progress["failure_count"] = len([item for item in results if not item.get("ok")])
                    _write_progress(run_dir, progress)
                    _append_log(log_path, f"END {direct.get('name')} ok={direct.get('ok')} seconds={direct.get('seconds')}")

            _write_report(run_dir, progress, results)
    finally:
        progress["status"] = "finished"
        progress["finished_at"] = _now()
        progress["elapsed_seconds"] = round(time.time() - (deadline - max(1.0, args.minutes * 60.0)), 1)
        progress["step_count"] = len(results)
        progress["failure_count"] = len([item for item in results if not item.get("ok")])
        _write_progress(run_dir, progress)
        _write_report(run_dir, progress, results)
        _append_log(log_path, f"QA soak finished steps={progress['step_count']} failures={progress['failure_count']}")
        print(json.dumps({"run_dir": str(run_dir), **progress}, ensure_ascii=False, indent=2))

    return 1 if progress["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
