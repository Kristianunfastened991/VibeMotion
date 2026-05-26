from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

import torch


def _write(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def _quantization_policy(name: str):
    if not name:
        return None
    from ltx_core.quantization import QuantizationPolicy

    if name == "fp8-cast":
        return QuantizationPolicy.fp8_cast()
    if name == "fp8-scaled-mm":
        return QuantizationPolicy.fp8_scaled_mm()
    raise ValueError(f"Unsupported quantization policy: {name}")


def _patch_ltx_gemma_runtime() -> None:
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

    import safetensors
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


def _encode_video_ffmpeg(video, fps, audio, output_path, video_chunks_number) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--spatial-upsampler", required=True)
    parser.add_argument("--gemma-root", required=True)
    parser.add_argument("--quantization", default="fp8-cast")
    args = parser.parse_args()

    logging.getLogger().setLevel(logging.INFO)
    if not torch.cuda.is_available():
        raise RuntimeError("LTX worker requires CUDA.")

    _patch_ltx_gemma_runtime()

    from ltx_core.model.video_vae import TilingConfig, get_video_chunks_number
    import ltx_pipelines.distilled as distilled
    from ltx_pipelines.distilled import DistilledPipeline
    from ltx_pipelines.utils.args import ImageConditioningInput

    distilled.vae_decode_audio = lambda *args, **kwargs: None
    distilled.vae_decode_video = _decode_video_on_decoder_device
    distilled.encode_prompts = _encode_prompts_low_vram
    pipeline = DistilledPipeline(
        distilled_checkpoint_path=str(Path(args.checkpoint)),
        spatial_upsampler_path=str(Path(args.spatial_upsampler)),
        gemma_root=str(Path(args.gemma_root)),
        loras=(),
        quantization=_quantization_policy(args.quantization),
    )
    _write({"status": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
            tiling_config = TilingConfig.default()
            frames = int(job["frames"])
            fps = float(job["fps"])
            video_chunks_number = get_video_chunks_number(frames, tiling_config)
            image_crf = max(0, int(job.get("image_crf", 0)))
            images = [ImageConditioningInput(str(job["image"]), 0, 1.0, image_crf)]
            video, audio = pipeline(
                prompt=str(job["prompt"]),
                seed=int(job.get("seed") or 42),
                height=int(job["height"]),
                width=int(job["width"]),
                num_frames=frames,
                frame_rate=fps,
                images=images,
                tiling_config=tiling_config,
                enhance_prompt=False,
            )
            _encode_video_ffmpeg(
                video=video,
                fps=fps,
                audio=audio,
                output_path=str(job["output"]),
                video_chunks_number=video_chunks_number,
            )
            _write({"status": "done", "output": str(job["output"])})
        except Exception as exc:
            _write({"status": "error", "error": str(exc), "traceback": traceback.format_exc()[-4000:]})


if __name__ == "__main__":
    main()
