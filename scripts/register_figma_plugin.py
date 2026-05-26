from __future__ import annotations

import json
import os
from pathlib import Path


PLUGIN_NAME = "VibeMotion Export"
LEGACY_PLUGIN_NAMES = {"VibeMotion Export"}


def _settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot locate Figma settings.json")
    return Path(appdata) / "Figma" / "settings.json"


def _same_plugin_entry(entry: dict, plugin_dir: Path) -> bool:
    if entry.get("lastKnownName") in LEGACY_PLUGIN_NAMES:
        return True
    manifest_path = str(entry.get("manifestPath") or "")
    if not manifest_path:
        return False
    try:
        path = Path(manifest_path).resolve()
        if plugin_dir in path.parents:
            return True
    except OSError:
        return False
    return False


def register(root: Path | None = None) -> Path:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    plugin_dir = root / "figma-plugin"
    manifest = plugin_dir / "manifest.json"
    code = plugin_dir / "code.js"
    ui = plugin_dir / "ui.html"
    missing = [str(path) for path in (manifest, code, ui) if not path.exists()]
    if missing:
        raise RuntimeError("Figma plugin files are missing: " + ", ".join(missing))

    settings_path = _settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = settings_path.with_suffix(".json.bak")
            backup.write_bytes(settings_path.read_bytes())
            settings = {}
    else:
        settings = {}

    entries = settings.get("localFileExtensions")
    if not isinstance(entries, list):
        entries = []

    clean_entries = [entry for entry in entries if not _same_plugin_entry(entry, plugin_dir)]
    max_id = 0
    for entry in clean_entries:
        try:
            max_id = max(max_id, int(entry.get("id") or 0))
        except (TypeError, ValueError):
            pass

    manifest_id = max_id + 1
    code_id = max_id + 2
    ui_id = max_id + 3
    clean_entries.extend(
        [
            {
                "id": code_id,
                "manifestPath": str(code),
                "fileMetadata": {"type": "code", "manifestFileId": manifest_id},
            },
            {
                "id": ui_id,
                "manifestPath": str(ui),
                "fileMetadata": {"type": "ui", "manifestFileId": manifest_id},
            },
            {
                "id": manifest_id,
                "manifestPath": str(manifest),
                "lastKnownName": PLUGIN_NAME,
                "lastKnownPluginId": "",
                "fileMetadata": {"type": "manifest", "codeFileId": code_id, "uiFileIds": [ui_id]},
                "cachedContainsWidget": False,
            },
        ]
    )

    settings["localFileExtensions"] = clean_entries
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return settings_path


if __name__ == "__main__":
    print(register())
