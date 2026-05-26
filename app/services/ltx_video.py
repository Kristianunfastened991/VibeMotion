from __future__ import annotations

import json
import math
import os
import atexit
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

from app.models.schemas import MotionSpec
from app.services.motion import _figma_layer_bounds, _find_visual_mask_for_layer, _render_single_figma_layer


LTX_MODEL_ID = os.environ.get("VIBEMOTION_LTX_MODEL", "Lightricks/LTX-2.3")
DEFAULT_MAX_SIDE = int(os.environ.get("VIBEMOTION_LTX_MAX_SIDE", "480"))
LTX_SAFE_MAX_SIDE = int(os.environ.get("VIBEMOTION_LTX_SAFE_MAX_SIDE", "320"))
LTX_FALLBACK_MAX_SIDE = int(os.environ.get("VIBEMOTION_LTX_FALLBACK_MAX_SIDE", "256"))
LTX_MIN_FALLBACK_MAX_SIDE = int(os.environ.get("VIBEMOTION_LTX_MIN_FALLBACK_MAX_SIDE", "128"))
LTX_STABLE_FPS = int(os.environ.get("VIBEMOTION_LTX_STABLE_FPS", "24"))
LTX_STABLE_DURATION = float(os.environ.get("VIBEMOTION_LTX_STABLE_DURATION", "20.0"))
LTX_MAX_REQUESTED_MAX_SIDE = int(os.environ.get("VIBEMOTION_LTX_MAX_REQUESTED_MAX_SIDE", "1080"))
LTX_MIN_FREE_VRAM_MIB = int(os.environ.get("VIBEMOTION_LTX_MIN_FREE_VRAM_MIB", "9000"))
LTX_FULL_RES_FREE_VRAM_MIB = int(os.environ.get("VIBEMOTION_LTX_FULL_RES_FREE_VRAM_MIB", "13500"))
LTX_480_FREE_VRAM_MIB = int(os.environ.get("VIBEMOTION_LTX_480_FREE_VRAM_MIB", "8500"))
LTX_720_FREE_VRAM_MIB = int(os.environ.get("VIBEMOTION_LTX_720_FREE_VRAM_MIB", "10500"))
LTX_1080_FREE_VRAM_MIB = int(os.environ.get("VIBEMOTION_LTX_1080_FREE_VRAM_MIB", "15000"))
LTX_IMAGE_CRF = int(os.environ.get("VIBEMOTION_LTX_IMAGE_CRF", "0"))


LTX_DIMENSION_MULTIPLE = 64
_LTX_WORKER_LOCK = threading.Lock()
_LTX_WORKER: subprocess.Popen[str] | None = None
_LTX_WORKER_KEY: tuple[str, str, str, str] | None = None
_LTX_ACTIVE_PROCESS: subprocess.Popen[str] | None = None


class LtxGenerationCancelled(RuntimeError):
    pass


def _ltx_child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:512")
    return env


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=5)
        except OSError:
            return


def _round_multiple(value: int, multiple: int = LTX_DIMENSION_MULTIPLE) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def _fit_ltx_size(width: int, height: int, max_side: int = DEFAULT_MAX_SIDE) -> tuple[int, int]:
    width = max(1, int(width))
    height = max(1, int(height))
    max_side = _round_multiple(max(64, int(max_side)))
    source_ratio = width / max(1, height)
    preferred: list[tuple[int, float, int, int]] = []
    fallback: tuple[float, int, int] | None = None
    for candidate_width in range(LTX_DIMENSION_MULTIPLE, max_side + 1, LTX_DIMENSION_MULTIPLE):
        for candidate_height in range(LTX_DIMENSION_MULTIPLE, max_side + 1, LTX_DIMENSION_MULTIPLE):
            if max(candidate_width, candidate_height) > max_side:
                continue
            ratio = candidate_width / max(1, candidate_height)
            aspect_error = abs(ratio - source_ratio) / max(0.001, source_ratio)
            area = candidate_width * candidate_height
            if aspect_error <= 0.06:
                preferred.append((area, aspect_error, candidate_width, candidate_height))
                continue
            max_side_penalty = abs(max(candidate_width, candidate_height) - max_side) / max(1, max_side)
            area_penalty = 1.0 / max(1, candidate_width * candidate_height)
            score = aspect_error * 8.0 + max_side_penalty + area_penalty
            if fallback is None or score < fallback[0]:
                fallback = (score, candidate_width, candidate_height)
    if preferred:
        _area, _aspect_error, selected_width, selected_height = max(preferred, key=lambda item: (item[0], -item[1]))
        return selected_width, selected_height
    if fallback is not None:
        return fallback[1], fallback[2]
    scale = max_side / max(width, height)
    return _round_multiple(width * scale), _round_multiple(height * scale)


def _cuda_memory_mib() -> tuple[int | None, int | None, int | None]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None, None, None
    line = (result.stdout or "").strip().splitlines()[0:1]
    if not line:
        return None, None, None
    try:
        total, used, free = [int(part.strip()) for part in line[0].split(",")[:3]]
    except (TypeError, ValueError):
        return None, None, None
    return total, used, free


def _adaptive_ltx_max_side(requested_max_side: int) -> int:
    requested_max_side = min(max(320, int(requested_max_side)), LTX_MAX_REQUESTED_MAX_SIDE)
    _total, _used, free = _cuda_memory_mib()
    if free is None:
        return requested_max_side
    if free < LTX_MIN_FREE_VRAM_MIB:
        raise RuntimeError(
            f"LTX needs more free GPU memory. Free VRAM: {free} MiB. "
            "Close other GPU apps such as Ollama or browser GPU tasks and try again."
        )
    if requested_max_side >= 1080:
        if free >= LTX_1080_FREE_VRAM_MIB:
            return requested_max_side
        if free >= LTX_720_FREE_VRAM_MIB:
            return 720
        if free >= LTX_480_FREE_VRAM_MIB:
            return 480
        return LTX_SAFE_MAX_SIDE
    if requested_max_side >= 720:
        if free >= LTX_720_FREE_VRAM_MIB:
            return requested_max_side
        if free >= LTX_480_FREE_VRAM_MIB:
            return 480
        return LTX_SAFE_MAX_SIDE
    if requested_max_side >= 480 and free < LTX_480_FREE_VRAM_MIB:
        return LTX_SAFE_MAX_SIDE
    return requested_max_side


def _ltx_frame_count(duration: float, fps: int) -> int:
    target = max(8, int(math.ceil(float(duration) * int(fps))))
    return (math.ceil(target / 8) * 8) + 1


def _ltx_effective_prompt(user_prompt: str) -> str:
    prompt = " ".join(str(user_prompt or "").split())
    if not prompt:
        return prompt
    words = [word for word in prompt.replace(",", " ").replace(".", " ").split() if word]
    lower = prompt.casefold()
    has_camera_language = any(
        token in lower
        for token in (
            "camera",
            "dolly",
            "push",
            "zoom",
            "pan",
            "tilt",
            "tracking",
            "handheld",
            "close-up",
            "medium shot",
            "wide shot",
        )
    )
    has_scene_detail = len(words) >= 18
    preservation = (
        "Preserve the exact person, face, body proportions, clothing, background, lighting, "
        "colors, and composition from the source image. Do not add text, logos, extra people, "
        "extra limbs, warping, melting, or identity changes."
    )
    if has_scene_detail and has_camera_language:
        return f"{prompt} {preservation}"
    if "zoom" in lower or "push" in lower:
        movement = (
            "The shot starts from the source image and performs a slow, smooth cinematic dolly-in "
            "toward the subject over the full clip, but it must not look like a flat 2D crop, "
            "resize, or simple digital zoom. Add real image-to-video motion: subtle breathing, "
            "small gaze and head micro-movement, natural hair and fabric movement, and gentle "
            "foreground/background parallax. The subject remains stable, centered, realistic, "
            "and sharp while the background keeps the same perspective and light direction."
        )
    else:
        movement = (
            "The shot starts from the source image and adds controlled natural motion that follows "
            f"this intent: {prompt}. Keep the movement subtle, realistic, smooth, and physically plausible."
        )
    return f"{movement} {preservation}"


def _ltx_retry_plans(
    source_width: int,
    source_height: int,
    current_width: int,
    current_height: int,
    current_max_side: int,
    current_frames: int,
    fps: int,
) -> list[tuple[int, int, int, int]]:
    plans: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    current_key = (int(current_width), int(current_height), int(current_frames))

    def add_plan(max_side: int, frames: int) -> None:
        max_side = _round_multiple(max(64, int(max_side)))
        frames = max(9, int(frames))
        width, height = _fit_ltx_size(source_width, source_height, max_side=max_side)
        key = (width, height, frames)
        if key == current_key or key in seen:
            return
        seen.add(key)
        plans.append((max_side, width, height, frames))

    retry_sides = [
        LTX_SAFE_MAX_SIDE,
        LTX_FALLBACK_MAX_SIDE,
        min(192, LTX_FALLBACK_MAX_SIDE),
        LTX_MIN_FALLBACK_MAX_SIDE,
    ]
    for retry_side in retry_sides:
        retry_side = _round_multiple(max(64, int(retry_side)))
        if retry_side >= max(current_width, current_height):
            continue
        add_plan(retry_side, current_frames)
    return plans


def _is_retryable_ltx_failure(exc: subprocess.CalledProcessError) -> bool:
    detail = f"{exc.stderr or ''}\n{exc.stdout or ''}".casefold()
    return (
        exc.returncode == 3221225477
        or "out of memory" in detail
        or "cuda" in detail and ("memory" in detail or "alloc" in detail)
        or "cublas" in detail
        or "cudnn" in detail
    )


def _ltx_model_paths(project_root: Path) -> dict[str, Path]:
    default_dir = project_root.parent.parent / "models" / "ltx-2.3"
    model_dir = Path(os.environ.get("VIBEMOTION_LTX_DIR") or default_dir).expanduser().resolve()
    return {
        "checkpoint": Path(os.environ.get("VIBEMOTION_LTX_CHECKPOINT") or model_dir / "ltx-2.3-22b-distilled-1.1.safetensors").expanduser().resolve(),
        "spatial_upsampler": Path(os.environ.get("VIBEMOTION_LTX_SPATIAL_UPSAMPLER") or model_dir / "ltx-2.3-spatial-upscaler-x2-1.1.safetensors").expanduser().resolve(),
        "gemma_root": Path(os.environ.get("VIBEMOTION_LTX_GEMMA_ROOT") or model_dir / "gemma").expanduser().resolve(),
    }


def _gemma_complete(gemma_root: Path) -> bool:
    return (
        gemma_root.exists()
        and any(gemma_root.rglob("model*.safetensors"))
        and any(gemma_root.rglob("tokenizer.model"))
        and any(gemma_root.rglob("preprocessor_config.json"))
    )


def _missing_ltx_model_files(model_paths: dict[str, Path]) -> list[str]:
    missing = [
        f"checkpoint: {model_paths['checkpoint']}" if not model_paths["checkpoint"].exists() else "",
        f"spatial upsampler: {model_paths['spatial_upsampler']}" if not model_paths["spatial_upsampler"].exists() else "",
    ]
    if not _gemma_complete(model_paths["gemma_root"]):
        missing.append(f"Gemma text encoder files under: {model_paths['gemma_root']}")
    return [item for item in missing if item]


def _ensure_ltx_model_files(project_root: Path, model_paths: dict[str, Path]) -> None:
    missing = _missing_ltx_model_files(model_paths)
    if not missing:
        return
    app_root = project_root.parent.parent
    downloader = app_root / "scripts" / "download_ltx_models.py"
    if downloader.exists():
        env = dict(os.environ)
        env["VIBEMOTION_NONINTERACTIVE"] = "1"
        result = subprocess.run(
            [sys.executable, str(downloader), "--root", str(app_root)],
            cwd=app_root,
            text=True,
            capture_output=True,
            env=env,
        )
        if result.returncode == 0 and not _missing_ltx_model_files(model_paths):
            return
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            detail = detail.replace("[models] ERROR ", "").strip()
            raise RuntimeError(detail[-4000:])
    raise RuntimeError(
        "LTX 2.3 model setup is incomplete. Missing: "
        + "; ".join(_missing_ltx_model_files(model_paths) or missing)
        + ". Run the VibeMotion launcher again to resume the public local model-pack download."
    )


def _stop_ltx_worker() -> None:
    global _LTX_WORKER, _LTX_WORKER_KEY
    worker = _LTX_WORKER
    _LTX_WORKER = None
    _LTX_WORKER_KEY = None
    if worker and worker.poll() is None:
        _terminate_process(worker)


def cancel_ltx_generation() -> None:
    global _LTX_ACTIVE_PROCESS
    process = _LTX_ACTIVE_PROCESS
    if process and process.poll() is None:
        _terminate_process(process)
    _LTX_ACTIVE_PROCESS = None
    _stop_ltx_worker()


atexit.register(_stop_ltx_worker)


def _read_ltx_worker_message(worker: subprocess.Popen[str], timeout: float, cancel_event: threading.Event | None = None) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            cancel_ltx_generation()
            raise LtxGenerationCancelled("LTX generation cancelled")
        if worker.poll() is not None:
            if cancel_event is not None and cancel_event.is_set():
                raise LtxGenerationCancelled("LTX generation cancelled")
            raise RuntimeError(f"LTX worker exited with code {worker.returncode}")
        line = worker.stdout.readline() if worker.stdout else ""
        if cancel_event is not None and cancel_event.is_set():
            cancel_ltx_generation()
            raise LtxGenerationCancelled("LTX generation cancelled")
        if not line:
            time.sleep(0.1)
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise TimeoutError("Timed out waiting for LTX worker")


def _ensure_ltx_worker(
    project_root: Path,
    model_paths: dict[str, Path],
    cancel_event: threading.Event | None = None,
) -> subprocess.Popen[str]:
    global _LTX_WORKER, _LTX_WORKER_KEY
    if cancel_event is not None and cancel_event.is_set():
        raise LtxGenerationCancelled("LTX generation cancelled")
    quantization = os.environ.get("VIBEMOTION_LTX_QUANTIZATION", "fp8-cast").strip()
    key = (
        str(model_paths["checkpoint"]),
        str(model_paths["spatial_upsampler"]),
        str(model_paths["gemma_root"]),
        quantization,
    )
    if _LTX_WORKER and _LTX_WORKER.poll() is None and _LTX_WORKER_KEY == key:
        return _LTX_WORKER

    _stop_ltx_worker()
    app_root = Path(__file__).resolve().parents[2]
    worker_script = app_root / "scripts" / "ltx_worker.py"
    command = [
        sys.executable,
        str(worker_script),
        "--checkpoint",
        str(model_paths["checkpoint"]),
        "--spatial-upsampler",
        str(model_paths["spatial_upsampler"]),
        "--gemma-root",
        str(model_paths["gemma_root"]),
    ]
    if quantization:
        command.extend(["--quantization", quantization])
    env = _ltx_child_env()
    worker = subprocess.Popen(
        command,
        cwd=app_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=env,
    )
    _LTX_WORKER = worker
    message = _read_ltx_worker_message(
        worker,
        timeout=float(os.environ.get("VIBEMOTION_LTX_WORKER_LOAD_TIMEOUT", "600")),
        cancel_event=cancel_event,
    )
    if message.get("status") != "ready":
        worker.kill()
        _LTX_WORKER = None
        raise RuntimeError(f"LTX worker failed to start: {message}")
    _LTX_WORKER_KEY = key
    return worker


def _run_ltx_worker_job(project_root: Path, model_paths: dict[str, Path], payload: dict, cancel_event: threading.Event | None = None) -> None:
    with _LTX_WORKER_LOCK:
        if cancel_event is not None and cancel_event.is_set():
            raise LtxGenerationCancelled("LTX generation cancelled")
        worker = _ensure_ltx_worker(project_root, model_paths, cancel_event=cancel_event)
        if not worker.stdin:
            raise RuntimeError("LTX worker stdin is not available")
        worker.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        worker.stdin.flush()
        message = _read_ltx_worker_message(worker, timeout=float(os.environ.get("VIBEMOTION_LTX_WORKER_JOB_TIMEOUT", "1800")), cancel_event=cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise LtxGenerationCancelled("LTX generation cancelled")
        if message.get("status") == "done":
            return
        if message.get("status") == "error":
            raise RuntimeError(message.get("traceback") or message.get("error") or "LTX worker failed")
        raise RuntimeError(f"Unexpected LTX worker response: {message}")


def _extract_layer_image(project_root: Path, motion: MotionSpec, layer_id: str, output: Path) -> tuple[int, int]:
    layers = list(motion.figma_layers or [])
    layer = next((item for item in layers if str(item.get("id") or "") == str(layer_id)), None)
    if not layer:
        raise ValueError("Figma layer not found")
    bounds_width, bounds_height = _figma_layer_bounds(motion, layers)
    scale_x = float(motion.width) / max(1.0, bounds_width)
    scale_y = float(motion.height) / max(1.0, bounds_height)
    visual_rect = _find_visual_mask_for_layer(layer, layers) or layer
    if layer.get("kind") == "image" and layer.get("asset_path"):
        source = project_root / str(layer["asset_path"])
        if not source.exists():
            raise FileNotFoundError("Layer PNG asset is missing")
        with Image.open(source).convert("RGBA") as image:
            image.save(output)
            return image.size
    sprite = _render_single_figma_layer(layer, visual_rect, scale_x, scale_y, project_root)
    if sprite is None:
        raise ValueError("Selected layer cannot be rendered as an image")
    background = Image.new("RGBA", sprite.size, (255, 255, 255, 255))
    background.alpha_composite(sprite)
    background.save(output)
    return sprite.size


def _write_runner_script(path: Path) -> None:
    path.write_text(
        r'''
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import torch
from diffusers import DiffusionPipeline
from diffusers.utils import export_to_video, load_image


def _encode_video_ffmpeg(video, fps, audio, output_path, video_chunks_number):
    if isinstance(video, torch.Tensor):
        video = iter([video])
    else:
        video = iter(video)

    first_chunk = next(video)
    _, height, width, _ = first_chunk.shape
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.stem + ".tmp" + output.suffix)
    if temp_output.exists():
        temp_output.unlink()

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{int(width)}x{int(height)}",
        "-r",
        str(float(fps)),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(temp_output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write_chunk(chunk):
        tensor = chunk.detach().to("cpu")
        if torch.is_floating_point(tensor):
            max_value = float(tensor.max().item()) if tensor.numel() else 255.0
            if max_value <= 1.0:
                tensor = tensor * 255.0
            tensor = tensor.clamp(0, 255).to(torch.uint8)
        elif tensor.dtype != torch.uint8:
            tensor = tensor.clamp(0, 255).to(torch.uint8)
        process.stdin.write(tensor.contiguous().numpy().tobytes())

    try:
        write_chunk(first_chunk)
        for video_chunk in video:
            write_chunk(video_chunk)
    finally:
        if process.stdin:
            process.stdin.close()

    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    stdout = process.stdout.read().decode("utf-8", errors="replace") if process.stdout else ""
    return_code = process.wait()
    if return_code:
        if temp_output.exists():
            temp_output.unlink()
        raise RuntimeError((stderr or stdout or f"ffmpeg exited with code {return_code}")[-4000:])
    os.replace(temp_output, output)


def _patch_ltx_gemma_runtime():
    import torch
    import safetensors
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

    import ltx_core.loader.sft_loader as sft_loader
    import ltx_core.text_encoders.gemma.encoders.encoder_configurator as encoder_configurator
    import ltx_pipelines.utils.model_ledger as model_ledger
    from ltx_core.loader import KeyValueOperationResult
    from ltx_core.loader.module_ops import ModuleOps
    from ltx_core.loader.primitives import StateDict
    from ltx_core.loader.sd_ops import SDOps
    from ltx_core.text_encoders.gemma.embeddings_processor import EmbeddingsProcessor
    from ltx_core.text_encoders.gemma.encoders.encoder_configurator import Gemma3ForConditionalGeneration

    original_safetensors_load = sft_loader.SafetensorsStateDictLoader.load

    def safe_cpu_first_load(self, path, sd_ops, device=None):
        if getattr(sd_ops, "name", "") == "EMBEDDINGS_PROCESSOR_KEY_OPS":
            return original_safetensors_load(self, path, sd_ops, device)
        sd = {}
        size = 0
        dtype = set()
        target_device = device or torch.device("cpu")
        debug_load = os.environ.get("VIBEMOTION_LTX_DEBUG_LOAD") == "1"
        model_paths = path if isinstance(path, list) else [path]
        for shard_path in model_paths:
            with safetensors.safe_open(shard_path, framework="pt", device="cpu") as f:
                for name in f.keys():
                    expected_name = name if sd_ops is None else sd_ops.apply_to_key(name)
                    if expected_name is None:
                        continue
                    value = f.get_tensor(name)
                    key_value_pairs = ((expected_name, value),)
                    if sd_ops is not None:
                        key_value_pairs = sd_ops.apply_to_key_value(expected_name, value)
                    for key, value in key_value_pairs:
                        if target_device.type != "cpu":
                            if debug_load:
                                shape = tuple(value.shape) if hasattr(value, "shape") else "?"
                                print(
                                    f"[ltx-load] {key} {shape} {getattr(value, 'dtype', '?')} "
                                    f"{getattr(value, 'nbytes', 0) / (1024 * 1024):.1f} MiB -> {target_device}",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            value = value.to(device=target_device, non_blocking=True, copy=True)
                        size += value.nbytes
                        dtype.add(value.dtype)
                        sd[key] = value
        return StateDict(sd=sd, device=target_device, size=size, dtype=dtype)

    sft_loader.SafetensorsStateDictLoader.load = safe_cpu_first_load

    original_process_hidden_states = EmbeddingsProcessor.process_hidden_states

    def process_hidden_states_on_processor_device(self, hidden_states, attention_mask, padding_side="left"):
        target_device = next(self.parameters()).device
        hidden_states = tuple(
            item.to(device=target_device, non_blocking=True) if getattr(item, "device", target_device) != target_device else item
            for item in hidden_states
        )
        if getattr(attention_mask, "device", target_device) != target_device:
            attention_mask = attention_mask.to(device=target_device, non_blocking=True)
        return original_process_hidden_states(self, hidden_states, attention_mask, padding_side)

    EmbeddingsProcessor.process_hidden_states = process_hidden_states_on_processor_device

    def text_encoder_on_cpu(self):
        if not hasattr(self, "text_encoder_builder"):
            raise ValueError(
                "Text encoder not initialized. Please provide a checkpoint path and gemma root path to the "
                "ModelLedger constructor."
            )
        return self.text_encoder_builder.build(device=torch.device("cpu"), dtype=self.dtype).to(torch.device("cpu")).eval()

    def video_decoder_on_cpu(self):
        if not hasattr(self, "vae_decoder_builder"):
            raise ValueError(
                "Video decoder not initialized. Please provide a checkpoint path to the ModelLedger constructor."
            )
        return self.vae_decoder_builder.build(device=torch.device("cpu"), dtype=self.dtype).to(torch.device("cpu")).eval()

    def gemma_embeddings_processor_on_cpu(self):
        if not hasattr(self, "embeddings_processor_builder"):
            raise ValueError(
                "Embeddings processor not initialized. Please provide a checkpoint path to the ModelLedger constructor."
            )
        return self.embeddings_processor_builder.build(device=torch.device("cpu"), dtype=self.dtype).to(torch.device("cpu")).eval()

    def no_audio_model(self):
        return None

    model_ledger.ModelLedger.text_encoder = text_encoder_on_cpu
    model_ledger.ModelLedger.video_decoder = video_decoder_on_cpu
    model_ledger.ModelLedger.gemma_embeddings_processor = gemma_embeddings_processor_on_cpu
    model_ledger.ModelLedger.audio_decoder = no_audio_model
    model_ledger.ModelLedger.vocoder = no_audio_model

    def create_and_populate(module):
        model = module.model
        vision_tower = model.model.vision_tower
        v_model = getattr(vision_tower, "vision_model", vision_tower)
        l_model = model.model.language_model

        config = model.config.text_config
        dim = getattr(config, "head_dim", config.hidden_size // config.num_attention_heads)
        positions_length = len(v_model.embeddings.position_ids[0])
        position_ids = torch.arange(positions_length, dtype=torch.long, device="cpu").unsqueeze(0)
        v_model.embeddings.register_buffer("position_ids", position_ids)
        embed_scale = torch.tensor(model.config.text_config.hidden_size**0.5, device="cpu")
        l_model.embed_tokens.register_buffer("embed_scale", embed_scale)
        if hasattr(config, "rope_local_base_freq") and hasattr(l_model, "rotary_emb_local"):
            base = config.rope_local_base_freq
            local_rope_freqs = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.int64).to(dtype=torch.float) / dim))
            l_model.rotary_emb_local.register_buffer("inv_freq", local_rope_freqs)
        if (
            hasattr(config, "rope_scaling")
            and isinstance(config.rope_scaling, dict)
            and "rope_type" in config.rope_scaling
            and hasattr(l_model, "rotary_emb")
        ):
            inv_freqs, _ = ROPE_INIT_FUNCTIONS[config.rope_scaling["rope_type"]](config)
            l_model.rotary_emb.register_buffer("inv_freq", inv_freqs)
        rotary = getattr(l_model, "rotary_emb", None)
        if rotary is not None and hasattr(config, "rope_parameters") and hasattr(rotary, "layer_types"):
            for layer_type in rotary.layer_types:
                rope_params = config.rope_parameters.get(layer_type)
                if not rope_params:
                    continue
                rope_type = rope_params["rope_type"]
                if rope_type == "default":
                    inv_freq, attention_scaling = rotary.compute_default_rope_parameters(
                        config, device=torch.device("cpu"), layer_type=layer_type
                    )
                else:
                    inv_freq, attention_scaling = ROPE_INIT_FUNCTIONS[rope_type](
                        config, device=torch.device("cpu"), layer_type=layer_type
                    )
                rotary.register_buffer(f"{layer_type}_inv_freq", inv_freq, persistent=False)
                rotary.register_buffer(f"{layer_type}_original_inv_freq", inv_freq.clone(), persistent=False)
                setattr(rotary, f"{layer_type}_attention_scaling", attention_scaling)

        return module

    gemma_key_ops = (
        SDOps("GEMMA_LLM_KEY_OPS_VIBEMOTION_COMPAT")
        .with_matching(prefix="model.language_model.")
        .with_matching(prefix="model.vision_tower.")
        .with_matching(prefix="model.multi_modal_projector.")
        .with_replacement("model.language_model.", "model.model.language_model.")
        .with_replacement("model.vision_tower.vision_model.", "model.model.vision_tower.")
        .with_replacement("model.multi_modal_projector.", "model.model.multi_modal_projector.")
        .with_kv_operation(
            operation=lambda key, value: [
                KeyValueOperationResult(key, value),
                KeyValueOperationResult("model.lm_head.weight", value),
            ],
            key_prefix="model.model.language_model.embed_tokens.weight",
        )
    )
    gemma_model_ops = ModuleOps(
        name="GemmaModelVibeMotionCompat",
        matcher=lambda module: hasattr(module, "model") and isinstance(module.model, Gemma3ForConditionalGeneration),
        mutator=create_and_populate,
    )
    encoder_configurator.create_and_populate = create_and_populate
    encoder_configurator.GEMMA_LLM_KEY_OPS = gemma_key_ops
    encoder_configurator.GEMMA_MODEL_OPS = gemma_model_ops
    model_ledger.GEMMA_LLM_KEY_OPS = gemma_key_ops
    model_ledger.GEMMA_MODEL_OPS = gemma_model_ops


def _decode_video_on_decoder_device(latent, video_decoder, tiling_config=None, generator=None):
    from ltx_core.model.video_vae import decode_video as original_decode_video

    try:
        parameter = next(video_decoder.parameters())
        target_device = parameter.device
        target_dtype = parameter.dtype
    except StopIteration:
        target_device = torch.device("cpu")
        target_dtype = latent.dtype
    if latent.device != target_device or latent.dtype != target_dtype:
        latent = latent.to(device=target_device, dtype=target_dtype)
    yield from original_decode_video(latent, video_decoder, tiling_config, generator)


def _encode_prompts_low_vram(prompts, model_ledger, *, enhance_prompt_image=None, enhance_prompt_seed=42, enhance_first_prompt=False):
    import gc
    from ltx_pipelines.utils.helpers import cleanup_memory, generate_enhanced_prompt

    embeddings_processor = model_ledger.gemma_embeddings_processor()
    text_encoder = model_ledger.text_encoder()
    if enhance_first_prompt:
        prompts = list(prompts)
        prompts[0] = generate_enhanced_prompt(text_encoder, prompts[0], enhance_prompt_image, seed=enhance_prompt_seed)
    raw_outputs = [text_encoder.encode(prompt) for prompt in prompts]
    del text_encoder
    gc.collect()
    cleanup_memory()
    target_device = model_ledger.device
    results = []
    for hidden_states, mask in raw_outputs:
        result = embeddings_processor.process_hidden_states(hidden_states, mask)
        audio_encoding = None if result.audio_encoding is None else result.audio_encoding.to(device=target_device)
        results.append(
            result._replace(
                video_encoding=result.video_encoding.to(device=target_device),
                audio_encoding=audio_encoding,
                attention_mask=result.attention_mask.to(device=target_device),
            )
        )
    del embeddings_processor
    gc.collect()
    cleanup_memory()
    return results


def _run_official_ltx(args):
    gemma_root = Path(args.gemma_root)
    missing = [
        ("checkpoint", args.checkpoint),
        ("spatial upsampler", args.spatial_upsampler),
        ("Gemma root", args.gemma_root),
    ]
    missing = [f"{name}: {path}" for name, path in missing if not Path(path).exists()]
    if gemma_root.exists() and not (
        any(gemma_root.rglob("model*.safetensors"))
        and any(gemma_root.rglob("tokenizer.model"))
        and any(gemma_root.rglob("preprocessor_config.json"))
    ):
        missing.append(f"Gemma model files under: {gemma_root}")
    if missing:
        raise RuntimeError(
            "LTX 2.3 official pipeline is installed, but model files are missing. "
            "Set VIBEMOTION_LTX_DIR or VIBEMOTION_LTX_CHECKPOINT / VIBEMOTION_LTX_SPATIAL_UPSAMPLER / VIBEMOTION_LTX_GEMMA_ROOT. "
            + " Missing: "
            + "; ".join(missing)
        )
    image_crf = max(0, int(os.environ.get("VIBEMOTION_LTX_IMAGE_CRF", "0")))
    command = [
        "ltx_pipelines.distilled",
        "--distilled-checkpoint-path",
        args.checkpoint,
        "--spatial-upsampler-path",
        args.spatial_upsampler,
        "--gemma-root",
        args.gemma_root,
        "--prompt",
        args.prompt,
        "--output-path",
        args.output,
        "--height",
        str(args.height),
        "--width",
        str(args.width),
        "--num-frames",
        str(args.frames),
        "--frame-rate",
        str(float(args.fps)),
        "--seed",
        str(args.seed if args.seed is not None else 42),
        "--image",
        args.image,
        "0",
        "1.0",
        str(image_crf),
    ]
    quantization = os.environ.get("VIBEMOTION_LTX_QUANTIZATION", "fp8-cast").strip()
    if quantization:
        command.extend(["--quantization", quantization])
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:512")
    _patch_ltx_gemma_runtime()
    import ltx_pipelines.distilled as distilled

    previous_argv = sys.argv
    try:
        sys.argv = command
        distilled.vae_decode_audio = lambda *args, **kwargs: None
        distilled.vae_decode_video = _decode_video_on_decoder_device
        distilled.encode_prompts = _encode_prompts_low_vram
        distilled.encode_video = _encode_video_ffmpeg
        distilled.main()
    finally:
        sys.argv = previous_argv


def _run_diffusers_ltx(args):
    image = load_image(args.image).convert("RGB").resize((args.width, args.height))
    pipe = DiffusionPipeline.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        device_map="cuda",
    )
    pipe.to("cuda")
    generator = torch.Generator(device="cuda").manual_seed(args.seed) if args.seed is not None else None
    output = pipe(
        image=image,
        prompt=args.prompt,
        height=args.height,
        width=args.width,
        num_frames=args.frames,
        frame_rate=float(args.fps),
        generator=generator,
    ).frames[0]
    export_to_video(output, args.output, fps=args.fps)
    print(json.dumps({"output": args.output, "frames": len(output), "fps": args.fps}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--model", default=os.environ.get("VIBEMOTION_LTX_MODEL", "Lightricks/LTX-2.3"))
    parser.add_argument("--backend", default=os.environ.get("VIBEMOTION_LTX_BACKEND", "official"))
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("--spatial-upsampler", default="")
    parser.add_argument("--gemma-root", default="")
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("LTX 2.3 local generation requires a CUDA GPU. CUDA is not available to torch.")

    if args.backend == "diffusers":
        _run_diffusers_ltx(args)
    else:
        _run_official_ltx(args)
        print(json.dumps({"output": args.output, "frames": args.frames, "fps": args.fps}))


if __name__ == "__main__":
    main()
'''.lstrip(),
        encoding="utf-8",
    )


def generate_ltx_layer_preview(
    project_root: Path,
    motion: MotionSpec,
    layer_id: str,
    prompt: str,
    duration: float,
    fps: int,
    max_side: int = DEFAULT_MAX_SIDE,
    seed: int | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    project_root = project_root.resolve()
    user_prompt = " ".join(str(prompt or "").split())
    if not user_prompt:
        raise ValueError("Prompt is empty")
    effective_prompt = _ltx_effective_prompt(user_prompt)
    effective_seed = int(seed) if seed is not None else random.SystemRandom().randint(1, 2_147_483_647)
    if cancel_event is not None and cancel_event.is_set():
        raise LtxGenerationCancelled("LTX generation cancelled")
    ltx_dir = project_root / "assets" / "ltx" / motion.id / str(layer_id).replace(":", "-").replace(";", "-")
    ltx_dir.mkdir(parents=True, exist_ok=True)
    input_path = ltx_dir / "input.png"
    source_width, source_height = _extract_layer_image(project_root, motion, layer_id, input_path)
    requested_max_side = int(max_side)
    duration = max(4.0, min(float(duration), LTX_STABLE_DURATION))
    fps = max(1, min(int(fps), LTX_STABLE_FPS))
    max_side = _adaptive_ltx_max_side(requested_max_side)
    width, height = _fit_ltx_size(source_width, source_height, max_side=max_side)
    frames = _ltx_frame_count(duration, fps)
    output_path = ltx_dir / "preview.mp4"
    model_paths = _ltx_model_paths(project_root)
    _ensure_ltx_model_files(project_root, model_paths)

    runner = ltx_dir / "run_ltx_i2v.py"
    _write_runner_script(runner)
    def build_command(run_width: int, run_height: int, run_frames: int) -> list[str]:
        command = [
            sys.executable,
            str(runner),
            "--image",
            str(input_path),
            "--output",
            str(output_path),
            "--prompt",
            effective_prompt,
            "--width",
            str(run_width),
            "--height",
            str(run_height),
            "--frames",
            str(run_frames),
            "--fps",
            str(fps),
            "--model",
            LTX_MODEL_ID,
            "--backend",
            os.environ.get("VIBEMOTION_LTX_BACKEND", "official"),
            "--checkpoint",
            str(model_paths["checkpoint"]),
            "--spatial-upsampler",
            str(model_paths["spatial_upsampler"]),
            "--gemma-root",
            str(model_paths["gemma_root"]),
        ]
        command.extend(["--seed", str(effective_seed)])
        return command

    command = build_command(width, height, frames)

    backend = os.environ.get("VIBEMOTION_LTX_BACKEND", "official")
    use_worker = backend == "official" and os.environ.get("VIBEMOTION_LTX_PERSISTENT", "0") == "1"
    if use_worker:
        _run_ltx_worker_job(
            project_root,
            model_paths,
            {
                "image": str(input_path),
                "output": str(output_path),
                "prompt": effective_prompt,
                "width": width,
                "height": height,
                "frames": frames,
                "fps": int(fps),
                "seed": effective_seed,
                "image_crf": LTX_IMAGE_CRF,
            },
            cancel_event=cancel_event,
        )
    else:
        def run_subprocess(run_command: list[str]) -> None:
            global _LTX_ACTIVE_PROCESS
            if cancel_event is not None and cancel_event.is_set():
                raise LtxGenerationCancelled("LTX generation cancelled")
            process = subprocess.Popen(
                run_command,
                cwd=ltx_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _LTX_ACTIVE_PROCESS = process
            stdout = ""
            stderr = ""
            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        _terminate_process(process)
                        raise LtxGenerationCancelled("LTX generation cancelled")
                    try:
                        stdout, stderr = process.communicate(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if cancel_event is not None and cancel_event.is_set():
                    raise LtxGenerationCancelled("LTX generation cancelled")
                if process.returncode:
                    raise subprocess.CalledProcessError(
                        process.returncode,
                        run_command,
                        output=stdout,
                        stderr=stderr,
                    )
            finally:
                if _LTX_ACTIVE_PROCESS is process:
                    _LTX_ACTIVE_PROCESS = None

        try:
            run_subprocess(command)
        except subprocess.CalledProcessError as exc:
            for retry_side, retry_width, retry_height, retry_frames in _ltx_retry_plans(
                source_width,
                source_height,
                width,
                height,
                max_side,
                frames,
                fps,
            ):
                if not _is_retryable_ltx_failure(exc):
                    break
                width, height, frames = retry_width, retry_height, retry_frames
                max_side = retry_side
                output_path.unlink(missing_ok=True)
                try:
                    run_subprocess(build_command(width, height, frames))
                except subprocess.CalledProcessError as retry_exc:
                    exc = retry_exc
                else:
                    exc = None
                    break
            if exc is None:
                pass
            else:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                if exc.returncode == 3221225477:
                    _total, used, free = _cuda_memory_mib()
                    detail += (
                        "\nLTX crashed inside native CUDA/PyTorch code. This usually means GPU memory pressure. "
                        f"Current GPU memory: used={used} MiB, free={free} MiB. "
                        f"Close other GPU apps such as Ollama and try again. Safe LTX preview is capped at {LTX_SAFE_MAX_SIDE}px "
                        f"with emergency fallback down to {LTX_MIN_FALLBACK_MAX_SIDE}px while preserving the selected duration."
                    )
                if "No module named" in detail or "ModuleNotFoundError" in detail:
                    detail += "\nInstall LTX runtime dependencies through scripts\\bootstrap.ps1 so the CUDA PyTorch version stays pinned for LTX."
                raise RuntimeError(detail[-4000:]) from exc

    return {
        "prompt": user_prompt,
        "effective_prompt": effective_prompt,
        "duration": float((frames - 1) / fps),
        "fps": int(fps),
        "input_path": str(input_path.relative_to(project_root)),
        "preview_path": str(output_path.relative_to(project_root)),
        "width": width,
        "height": height,
        "max_side": int(max_side),
        "requested_max_side": int(requested_max_side),
        "source_width": source_width,
        "source_height": source_height,
        "frames": frames,
        "seed": effective_seed,
        "image_crf": LTX_IMAGE_CRF,
    }
