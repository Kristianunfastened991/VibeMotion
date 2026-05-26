from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    project_root: Path
    projects_root: Path
    ollama_base_url: str
    ollama_model: str
    ollama_vision_model: str
    vision_frame_count: int
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_cpu_compute_type: str
    whisper_beam_size: int
    whisper_batch_size: int
    default_language: str | None
    ffmpeg_encoder: str
    ffmpeg_cpu_encoder: str


def _build_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    projects_root = project_root / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    return Settings(
        project_root=project_root,
        projects_root=projects_root,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3.5:9b"),
        ollama_vision_model=os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b"),
        vision_frame_count=int(os.getenv("VISION_FRAME_COUNT", "8")),
        whisper_model=os.getenv("WHISPER_MODEL", "turbo"),
        whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
        whisper_cpu_compute_type=os.getenv("WHISPER_CPU_COMPUTE_TYPE", "int8"),
        whisper_beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "1")),
        whisper_batch_size=int(os.getenv("WHISPER_BATCH_SIZE", "16")),
        default_language=os.getenv("TRANSCRIPT_LANGUAGE") or None,
        ffmpeg_encoder=os.getenv("FFMPEG_ENCODER", "auto"),
        ffmpeg_cpu_encoder=os.getenv("FFMPEG_CPU_ENCODER", "libx264"),
    )


settings = _build_settings()
