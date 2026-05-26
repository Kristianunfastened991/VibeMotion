from __future__ import annotations

import json
import logging
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.schemas import TranscriptData, TranscriptSegment, WordToken
from app.services.hardware import preferred_whisper_device
from app.services.media import detect_duration


logger = logging.getLogger(__name__)


class TranscriptionRuntimeUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _get_whisper_runtime(device: str, compute_type: str) -> tuple[object, bool]:
    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
    except ModuleNotFoundError as exc:
        if exc.name == "faster_whisper":
            raise TranscriptionRuntimeUnavailable(
                "Transcription runtime is not installed. Install faster-whisper to enable video analysis transcription."
            ) from exc
        raise

    model = WhisperModel(
        settings.whisper_model,
        device=device,
        compute_type=compute_type,
    )
    if device == "cuda":
        return BatchedInferencePipeline(model=model), True
    return model, False


def prewarm_transcription_runtime() -> None:
    device, compute_type = preferred_whisper_device()
    try:
        _get_whisper_runtime(device, compute_type)
    except TranscriptionRuntimeUnavailable as exc:
        logger.info("%s", exc)
    except Exception:
        try:
            _get_whisper_runtime("cpu", settings.whisper_cpu_compute_type)
        except TranscriptionRuntimeUnavailable as exc:
            logger.info("%s", exc)
        except Exception as exc:
            logger.warning("Transcription runtime prewarm failed: %s", exc)


def _run_transcription(video_path: Path, device: str, compute_type: str) -> tuple[list[Any], Any]:
    runtime, is_batched = _get_whisper_runtime(device, compute_type)
    transcribe_kwargs = {
        "beam_size": settings.whisper_beam_size,
        "word_timestamps": True,
        "vad_filter": True,
        "language": settings.default_language,
        "condition_on_previous_text": False,
    }
    if is_batched:
        transcribe_kwargs["batch_size"] = settings.whisper_batch_size
        segments_iter, info = runtime.transcribe(  # type: ignore[attr-defined]
            str(video_path),
            **transcribe_kwargs,
        )
    else:
        segments_iter, info = runtime.transcribe(  # type: ignore[attr-defined]
            str(video_path),
            **transcribe_kwargs,
        )
    # Materialize here so CUDA runtime failures are caught before we return.
    return list(segments_iter), info


def _prepare_audio_for_whisper(video_path: Path, output_path: Path) -> Path:
    audio_path = output_path.with_suffix(".16k.wav")
    if audio_path.exists() and audio_path.stat().st_mtime >= video_path.stat().st_mtime:
        return audio_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return audio_path


def transcribe_with_faster_whisper(video_path: Path, output_path: Path) -> TranscriptData:
    whisper_input = _prepare_audio_for_whisper(video_path, output_path)
    device, compute_type = preferred_whisper_device()
    used_device = device
    try:
        raw_segments, info = _run_transcription(whisper_input, device, compute_type)
    except Exception as exc:
        message = str(exc).lower()
        cuda_runtime_missing = (
            "cublas" in message
            or "cudnn" in message
            or "cuda" in message
            or "library" in message and ".dll" in message
        )
        if device != "cpu" and cuda_runtime_missing:
            used_device = "cpu"
            raw_segments, info = _run_transcription(
                whisper_input,
                "cpu",
                settings.whisper_cpu_compute_type,
            )
        else:
            raise

    segments: list[TranscriptSegment] = []
    words: list[WordToken] = []
    full_text_parts: list[str] = []

    for segment in raw_segments:
        segment_words: list[WordToken] = []
        for word in segment.words or []:
            token = WordToken(
                start=round(word.start, 3),
                end=round(word.end, 3),
                text=word.word.strip(),
                probability=round(word.probability, 4) if word.probability is not None else None,
            )
            if token.text:
                segment_words.append(token)
                words.append(token)

        text = segment.text.strip()
        if text:
            full_text_parts.append(text)
        segments.append(
            TranscriptSegment(
                start=round(segment.start, 3),
                end=round(segment.end, 3),
                text=text,
                words=segment_words,
            )
        )

    transcript = TranscriptData(
        language=getattr(info, "language", None),
        duration=detect_duration(video_path),
        text=" ".join(full_text_parts).strip(),
        segments=segments,
        words=words,
    )
    output_path.write_text(
        json.dumps(transcript.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    if used_device == "cpu" and device != "cpu":
        note_path = output_path.with_suffix(".runtime.txt")
        note_path.write_text(
            "CUDA transcription failed because required NVIDIA runtime DLLs are missing. "
            "Used CPU fallback for this transcript.\n",
            encoding="utf-8",
        )
    return transcript


def load_cached_transcript(path: Path) -> TranscriptData | None:
    if not path.exists():
        return None
    return TranscriptData.model_validate_json(path.read_text(encoding="utf-8"))
