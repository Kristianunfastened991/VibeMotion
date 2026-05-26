from __future__ import annotations

import argparse
from pathlib import Path


LTX_FILES = [
    "ltx-2.3-22b-distilled-1.1.safetensors",
    "ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
]


def missing_files(root: Path) -> list[str]:
    model_dir = root / "models" / "ltx-2.3"
    gemma_dir = model_dir / "gemma"
    missing = [str(model_dir / name) for name in LTX_FILES if not (model_dir / name).exists()]
    if not any(gemma_dir.rglob("model*.safetensors")):
        missing.append(str(gemma_dir / "model*.safetensors"))
    if not any(gemma_dir.rglob("tokenizer.model")):
        missing.append(str(gemma_dir / "tokenizer.model"))
    if not any(gemma_dir.rglob("preprocessor_config.json")):
        missing.append(str(gemma_dir / "preprocessor_config.json"))
    return missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    missing = missing_files(args.root.resolve())
    if missing:
        print("[models] LTX 2.3 local model pack is incomplete.")
        for item in missing:
            print(f"[models] Missing: {item}")
        raise SystemExit(1)
    print(f"[models] LTX 2.3 local model pack ready under {args.root.resolve() / 'models' / 'ltx-2.3'}")


if __name__ == "__main__":
    main()
