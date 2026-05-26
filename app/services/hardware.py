from __future__ import annotations

import subprocess
from functools import lru_cache

from app.core.config import settings


def _command_ok(command: list[str]) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@lru_cache(maxsize=1)
def has_nvidia_gpu() -> bool:
    return _command_ok(["nvidia-smi"])


@lru_cache(maxsize=1)
def ffmpeg_has_encoder(name: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and name in result.stdout


def preferred_ffmpeg_encoder() -> str:
    configured = settings.ffmpeg_encoder
    if configured not in {"", "auto"}:
        return configured
    if has_nvidia_gpu() and ffmpeg_has_encoder("h264_nvenc"):
        return "h264_nvenc"
    return settings.ffmpeg_cpu_encoder


def preferred_whisper_device() -> tuple[str, str]:
    configured = settings.whisper_device
    if configured == "cuda":
        return "cuda", settings.whisper_compute_type
    if configured == "cpu":
        return "cpu", settings.whisper_cpu_compute_type
    if has_nvidia_gpu():
        return "cuda", settings.whisper_compute_type
    return "cpu", settings.whisper_cpu_compute_type
