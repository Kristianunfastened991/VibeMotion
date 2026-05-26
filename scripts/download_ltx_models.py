from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import hf_hub_download, hf_hub_url, snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError


LTX_REPO = "Lightricks/LTX-2.3"
GEMMA_REPO = "google/gemma-3-12b-it-qat-q4_0-unquantized"
PUBLIC_GEMMA_REPO = "DeepBeepMeep/LTX-2"
PUBLIC_GEMMA_PREFIX = "gemma-3-12b-it-qat-q4_0-unquantized"
LTX_FILES = [
    "ltx-2.3-22b-distilled-1.1.safetensors",
    "ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
]
GEMMA_PUBLIC_FILES = {
    f"{PUBLIC_GEMMA_PREFIX}/added_tokens.json": "added_tokens.json",
    f"{PUBLIC_GEMMA_PREFIX}/chat_template.json": "chat_template.json",
    f"{PUBLIC_GEMMA_PREFIX}/config.json": "config.json",
    f"{PUBLIC_GEMMA_PREFIX}/generation_config.json": "generation_config.json",
    f"{PUBLIC_GEMMA_PREFIX}/gemma-3-12b-it-qat-q4_0-unquantized.safetensors": "model.safetensors",
    f"{PUBLIC_GEMMA_PREFIX}/preprocessor_config.json": "preprocessor_config.json",
    f"{PUBLIC_GEMMA_PREFIX}/processor_config.json": "processor_config.json",
    f"{PUBLIC_GEMMA_PREFIX}/special_tokens_map.json": "special_tokens_map.json",
    f"{PUBLIC_GEMMA_PREFIX}/tokenizer.json": "tokenizer.json",
    f"{PUBLIC_GEMMA_PREFIX}/tokenizer.model": "tokenizer.model",
    f"{PUBLIC_GEMMA_PREFIX}/tokenizer_config.json": "tokenizer_config.json",
}
GATED_GEMMA_PATTERNS = [
    "config.json",
    "generation_config.json",
    "model*.safetensors",
    "model.safetensors.index.json",
    "preprocessor_config.json",
    "processor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer.model",
    "tokenizer_config.json",
]
MIN_FREE_GB = 80


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _token_path(root: Path) -> Path:
    return root / ".secrets" / "huggingface_token.txt"


def _read_token(root: Path) -> str | None:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        return token.strip()
    path = _token_path(root)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    return None


def _prompt_for_token(root: Path) -> str | None:
    if os.environ.get("VIBEMOTION_NONINTERACTIVE") == "1" or not sys.stdin.isatty():
        return None
    print()
    print("Gemma 3 text encoder is gated on Hugging Face.")
    print("Accept access for the model in your browser, then paste a Hugging Face token with read access.")
    print(f"Model page: https://huggingface.co/{GEMMA_REPO}")
    print("Token page: https://huggingface.co/settings/tokens")
    token = getpass.getpass("HF token (hidden, Enter to skip): ").strip()
    if not token:
        return None
    token_file = _token_path(root)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token, encoding="utf-8")
    return token


def _has_ltx(model_dir: Path) -> bool:
    return all((model_dir / name).exists() for name in LTX_FILES)


def _has_gemma(gemma_dir: Path) -> bool:
    return (
        any(gemma_dir.rglob("model*.safetensors"))
        and any(gemma_dir.rglob("tokenizer.model"))
        and any(gemma_dir.rglob("preprocessor_config.json"))
    )


def _free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1024**3


def _download_with_curl(repo_id: str, filename: str, output: Path, token: str | None) -> bool:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        return False
    part = output.with_suffix(output.suffix + ".part")
    url = hf_hub_url(repo_id, filename=filename)
    command = [
        curl,
        "-L",
        "--fail",
        "--retry",
        "8",
        "--retry-delay",
        "5",
        "-C",
        "-",
        "-o",
        str(part),
    ]
    if token:
        command.extend(["-H", f"Authorization: Bearer {token}"])
    command.append(url)
    subprocess.run(command, check=True)
    part.replace(output)
    return True


def _download_ltx(model_dir: Path, token: str | None) -> None:
    for filename in LTX_FILES:
        output = model_dir / filename
        if output.exists():
            print(f"[models] OK {filename}")
            continue
        print(f"[models] Downloading {LTX_REPO}/{filename}")
        if not _download_with_curl(LTX_REPO, filename, output, token):
            hf_hub_download(
                repo_id=LTX_REPO,
                filename=filename,
                local_dir=model_dir,
                token=token,
            )


def _download_public_gemma(gemma_dir: Path) -> None:
    for remote_name, local_name in GEMMA_PUBLIC_FILES.items():
        output = gemma_dir / local_name
        if output.exists():
            print(f"[models] OK Gemma {local_name}")
            continue
        print(f"[models] Downloading {PUBLIC_GEMMA_REPO}/{remote_name}")
        if not _download_with_curl(PUBLIC_GEMMA_REPO, remote_name, output, None):
            hf_hub_download(
                repo_id=PUBLIC_GEMMA_REPO,
                filename=remote_name,
                local_dir=gemma_dir,
                local_dir_use_symlinks=False,
            )
            downloaded = gemma_dir / remote_name
            if downloaded.exists() and downloaded != output:
                output.parent.mkdir(parents=True, exist_ok=True)
                downloaded.replace(output)


def _download_gemma(root: Path, gemma_dir: Path, token: str | None) -> None:
    if _has_gemma(gemma_dir):
        print("[models] OK Gemma text encoder")
        return
    use_gated = os.environ.get("VIBEMOTION_USE_GATED_GEMMA") == "1"
    if not use_gated:
        _download_public_gemma(gemma_dir)
        if _has_gemma(gemma_dir):
            print(f"[models] OK public Gemma text encoder from {PUBLIC_GEMMA_REPO}")
            return
    if not token:
        token = _prompt_for_token(root)
    if not token:
        raise RuntimeError(
            "Gemma text encoder is missing. The public model-pack download did not complete. "
            "Run Launch-VibeMotion.bat again to resume the download."
        )
    print(f"[models] Downloading {GEMMA_REPO} to {gemma_dir}")
    snapshot_download(
        repo_id=GEMMA_REPO,
        local_dir=gemma_dir,
        allow_patterns=GATED_GEMMA_PATTERNS,
        token=token,
    )


def ensure_models(root: Path, require: bool = True, ltx_only: bool = False) -> None:
    model_dir = root / "models" / "ltx-2.3"
    gemma_dir = model_dir / "gemma"
    model_dir.mkdir(parents=True, exist_ok=True)
    gemma_dir.mkdir(parents=True, exist_ok=True)

    missing_ltx = not _has_ltx(model_dir)
    missing_gemma = not _has_gemma(gemma_dir)
    if missing_ltx or missing_gemma:
        free = _free_gb(model_dir)
        if free < MIN_FREE_GB:
            raise RuntimeError(f"Need at least {MIN_FREE_GB} GB free for LTX models. Free now: {free:.1f} GB at {model_dir}")

    token = _read_token(root)
    try:
        _download_ltx(model_dir, token)
        if ltx_only:
            print("[models] Skipping Gemma text encoder by request.")
        else:
            _download_gemma(root, gemma_dir, token)
    except GatedRepoError as exc:
        raise RuntimeError(
            f"Hugging Face access is gated for {GEMMA_REPO}. Accept the model license, set HF_TOKEN, and rerun bootstrap."
        ) from exc
    except HfHubHTTPError as exc:
        raise RuntimeError(f"Hugging Face download failed: {exc}") from exc

    if require and (not _has_ltx(model_dir) or (not ltx_only and not _has_gemma(gemma_dir))):
        raise RuntimeError(f"LTX model setup is incomplete under {model_dir}")
    print(f"[models] LTX 2.3 ready in {model_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=_root())
    parser.add_argument("--optional", action="store_true", help="Do not fail if models are incomplete.")
    parser.add_argument("--ltx-only", action="store_true", help="Download only the public LTX safetensors, not gated Gemma files.")
    args = parser.parse_args()
    try:
        ensure_models(args.root.resolve(), require=not args.optional, ltx_only=args.ltx_only)
    except Exception as exc:
        print(f"[models] ERROR {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
