from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from app.core.config import settings
from app.services.media import detect_duration
from app.services.ollama import OllamaError, chat_json_images


VISION_SYSTEM_PROMPT = """You are a visual analyst for an automatic video editor.
Return JSON only.
Describe what is visible in the video frames and where overlays should go.
Focus on: subject, objects, text already on screen, pointing hands/fingers, gaze direction, empty/safe overlay areas, and useful graphic ideas.
Return shape:
{
  "summary": "string",
  "frames": [
    {
      "time": number,
      "description": "string",
      "subjects": ["string"],
      "existing_text": ["string"],
      "gestures": ["pointing to top-right|pointing to left|no clear pointing gesture"],
      "safe_zones": ["top-left|top-right|bottom-left|bottom-right|center-left|center-right"],
      "graphic_ideas": ["string"]
    }
  ],
  "global_graphic_ideas": ["string"]
}
"""


def ollama_model_available(model: str) -> bool:
    request = urllib.request.Request(f"{settings.ollama_base_url}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        return False
    return any(item.get("name") == model or item.get("model") == model for item in data.get("models", []))


def extract_vision_frames(video_path: Path, project_root: Path, count: int | None = None) -> list[Path]:
    frame_count = max(1, count or settings.vision_frame_count)
    duration = max(0.1, detect_duration(video_path))
    frames_dir = project_root / "assets" / "vision_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frames: list[Path] = []
    for index in range(frame_count):
        timestamp = min(duration - 0.05, duration * (index + 1) / (frame_count + 1))
        output = frames_dir / f"vision_{index:02d}_{timestamp:.2f}.jpg"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(768,iw)':-2",
            "-q:v",
            "4",
            str(output),
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if output.exists():
            frames.append(output)
    return frames


def analyze_video_with_vision(video_path: Path, project_root: Path) -> dict:
    model = settings.ollama_vision_model
    if not ollama_model_available(model):
        raise OllamaError(f"Vision model is not installed in Ollama: {model}")

    frames = extract_vision_frames(video_path, project_root)
    frame_times = []
    for frame in frames:
        parts = frame.stem.split("_")
        frame_times.append(float(parts[-1]) if parts else 0.0)

    user = json.dumps(
        {
            "video_duration": detect_duration(video_path),
            "frame_times": frame_times,
            "task": "Analyze these frames for automatic motion graphics placement and design.",
        },
        ensure_ascii=False,
    )
    result = chat_json_images(VISION_SYSTEM_PROMPT, user, frames, model=model)
    result["model"] = model
    result["frame_paths"] = [str(path.relative_to(project_root)) for path in frames]
    return result
