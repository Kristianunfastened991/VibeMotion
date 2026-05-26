from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def copy_upload(temp_path: Path, project_input_dir: Path, filename: str) -> Path:
    destination = project_input_dir / filename
    shutil.copyfile(temp_path, destination)
    return destination


def ffprobe_metadata(video_path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def detect_duration(video_path: Path) -> float:
    metadata = ffprobe_metadata(video_path)
    duration = metadata.get("format", {}).get("duration")
    if duration is None:
        return 0.0
    return float(duration)


def detect_video_size(video_path: Path) -> tuple[int, int]:
    metadata = ffprobe_metadata(video_path)
    for stream in metadata.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            rotation = 0
            tags = stream.get("tags") or {}
            side_data = stream.get("side_data_list") or []
            if "rotate" in tags:
                rotation = int(float(tags["rotate"]))
            for item in side_data:
                if "rotation" in item:
                    rotation = int(float(item["rotation"]))
            if abs(rotation) in {90, 270}:
                width, height = height, width
            return width, height
    return 0, 0
