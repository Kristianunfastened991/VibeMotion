from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT_EXCLUDES = {
    ".git",
    ".venv",
    ".secrets",
    "models",
    "projects",
    "output",
    "qa_artifacts",
    "qa-artifacts",
    "tmp",
    "vendor",
    "test-results",
    ".playwright-cli",
    ".qa_playwright",
    "vibemotion.egg-info",
    "vibemotion.egg-info",
}

ANYWHERE_EXCLUDES = {
    "__pycache__",
}

TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}

PATTERNS = {
    "figma_token": re.compile(r"\bfigd_[A-Za-z0-9_-]{20,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "bearer_token": re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9_.-]{12,}", re.IGNORECASE),
    "windows_user_path": re.compile(r"C:[/\\]Users[/\\][^/\\\r\n]+", re.IGNORECASE),
    "local_vibemotion_path": re.compile(r"\b[A-Z]:[/\\]VibeMotion\b", re.IGNORECASE),
    "huggingface_token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
}


def should_skip(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if rel.parts and rel.parts[0] in ROOT_EXCLUDES:
        return True
    return any(part in ANYWHERE_EXCLUDES for part in rel.parts)


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or should_skip(path, root):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in {".gitignore", ".gitattributes"}:
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan publishable source files for common secrets and local paths.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    findings: list[tuple[str, Path, int, str]] = []

    for path in iter_text_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((name, path, line_number, line.strip()[:180]))

    if findings:
        print("Publication audit failed:")
        for name, path, line_number, excerpt in findings:
            print(f"- {name}: {path.relative_to(root)}:{line_number}: {excerpt}")
        return 1
    print("Publication audit passed: no common secrets or local absolute paths found in publishable sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
