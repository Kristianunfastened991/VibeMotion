from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from app.models.schemas import MotionSpec


def _static_assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "static" / "assets"


def _plugin_dir() -> Path:
    path = _static_assets_dir() / "figma-plugin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return _plugin_dir() / "assets.json"


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")[:90] or "asset"


def _read_index() -> list[dict]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_index(items: list[dict]) -> None:
    _index_path().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _force_png_opaque(path: Path) -> None:
    try:
        with Image.open(path).convert("RGBA") as image:
            alpha = image.getchannel("A")
            if alpha.getextrema() == (255, 255):
                return
            image.putalpha(alpha.point(lambda value: 255 if value > 0 else 0))
            image.save(path)
    except OSError:
        return


def _restore_logo_transparency(path: Path, layer_name: str) -> None:
    name = str(layer_name or "").lower()
    if not any(token in name for token in ("logo", " icon", "icon")):
        return
    try:
        with Image.open(path).convert("RGBA") as image:
            alpha = image.getchannel("A")
            if alpha.getextrema() != (255, 255):
                return
            corners = [
                image.getpixel((0, 0)),
                image.getpixel((image.width - 1, 0)),
                image.getpixel((0, image.height - 1)),
                image.getpixel((image.width - 1, image.height - 1)),
            ]
            if not all(max(pixel[:3]) <= 12 for pixel in corners):
                return
            pixels = image.load()
            changed = False
            for y in range(image.height):
                for x in range(image.width):
                    r, g, b, a = pixels[x, y]
                    if a == 0:
                        continue
                    value = max(r, g, b)
                    if value <= 10:
                        pixels[x, y] = (r, g, b, 0)
                        changed = True
                    elif value <= 48:
                        next_alpha = int(a * (value - 10) / 38)
                        pixels[x, y] = (r, g, b, max(0, min(255, next_alpha)))
                        changed = True
            if changed:
                image.save(path)
    except OSError:
        return


def _decode_png(value: str) -> bytes:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("PNG data is empty")
    if "," in raw and raw.lower().startswith("data:image"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw, validate=False)


def _force_opaque_rgba(value: str) -> str:
    text = str(value or "")
    match = re.match(r"rgba\(([^,]+),([^,]+),([^,]+),([^)]+)\)", text)
    if not match:
        return text
    return f"rgba({match.group(1).strip()}, {match.group(2).strip()}, {match.group(3).strip()}, 1.0000)"


def _save_layer_assets(layers: list[dict], root: Path, asset_id: str) -> list[dict]:
    saved_layers: list[dict] = []
    layer_dir = root / f"{asset_id}-layers"
    layer_dir.mkdir(parents=True, exist_ok=True)
    for index, layer in enumerate(layers or []):
        clean = {key: value for key, value in dict(layer).items() if key not in {"png_base64", "data_url", "asset_file"}}
        if index == 0 and str(clean.get("node_type") or "").upper() in {"FRAME", "COMPONENT", "INSTANCE"}:
            clean["opacity"] = 1
            if clean.get("kind") == "shape" and clean.get("fill"):
                clean["fill"] = _force_opaque_rgba(str(clean["fill"]))
        png_value = str(layer.get("png_base64") or layer.get("data_url") or "")
        if png_value:
            filename = f"{index:03d}-{_safe_name(str(layer.get('id') or layer.get('name') or 'layer'))}.png"
            output = layer_dir / filename
            output.write_bytes(_decode_png(png_value))
            if clean.get("kind") == "image":
                _restore_logo_transparency(output, str(layer.get("name") or ""))
            clean["asset_file"] = f"{asset_id}-layers/{filename}"
        elif clean.get("kind") == "image" and not clean.get("asset_file"):
            continue
        saved_layers.append(clean)
    return saved_layers


def save_plugin_assets(
    assets: list[dict],
    scope: str | None = None,
    page: str | None = None,
    session_id: str | None = None,
    total: int | None = None,
    complete: bool = False,
) -> dict:
    existing = {str(item.get("id") or ""): item for item in _read_index() if item.get("id")}
    saved: list[dict] = []
    root = _plugin_dir()
    normalized_scope = str(scope or "").lower()
    page_name = str(page or "")
    sync_session = str(session_id or "")
    for asset in assets:
        source_id = str(asset.get("id") or uuid.uuid4().hex)
        asset_id = _safe_name(source_id)
        name = str(asset.get("name") or "Figma frame")
        width = max(1, int(round(float(asset.get("width") or 1920))))
        height = max(1, int(round(float(asset.get("height") or 1080))))
        png_value = str(asset.get("png_base64") or asset.get("data_url") or "")
        png_bytes = _decode_png(png_value)
        filename = f"{asset_id}.png"
        output = root / filename
        output.write_bytes(png_bytes)
        _force_png_opaque(output)
        figma_layers = _save_layer_assets(list(asset.get("figma_layers") or []), root, asset_id)
        record = {
            "id": asset_id,
            "node_id": source_id,
            "name": name,
            "path": str(asset.get("path") or name),
            "page": str(asset.get("page") or "Figma Plugin"),
            "node_type": str(asset.get("node_type") or "FRAME"),
            "kind": "composition",
            "width": width,
            "height": height,
            "canvas_x": float(asset.get("canvas_x") or 0),
            "canvas_y": float(asset.get("canvas_y") or 0),
            "export_index": int(asset.get("export_index") or 0),
            "children_count": int(asset.get("children_count") or 0),
            "figma_session": sync_session,
            "figma_total": int(total or 0),
            "source": "figma-plugin",
            "thumbnail_url": f"/app/assets/figma-plugin/{filename}?v={int(time.time())}",
            "asset_file": filename,
            "figma_layers": figma_layers,
            "updated_at": time.time(),
        }
        existing[asset_id] = record
        saved.append(record)
    if normalized_scope == "page" and page_name and sync_session and complete:
        existing = {
            asset_id: item
            for asset_id, item in existing.items()
            if str(item.get("page") or "") != page_name or str(item.get("figma_session") or "") == sync_session
        }
    items = sorted(
        existing.values(),
        key=lambda item: (
            str(item.get("page") or ""),
            float(item.get("canvas_y") or 0),
            float(item.get("canvas_x") or 0),
            int(item.get("export_index") or 0),
            str(item.get("name") or ""),
        ),
    )
    _write_index(items)
    return {"assets": saved, "total": len(items), "session_id": sync_session, "complete": bool(complete)}


def list_plugin_assets() -> dict:
    return {"assets": _read_index()}


def _plugin_record(asset_id: str | None = None, node_id: str | None = None) -> dict | None:
    assets = _read_index()
    asset_key = str(asset_id or "")
    node_key = str(node_id or "")
    for item in assets:
        if asset_key and str(item.get("id") or "") == asset_key:
            return item
        if node_key and str(item.get("node_id") or item.get("id") or "") == node_key:
            return item
    return None


def _normalize_rect(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("@{") and text.endswith("}"):
        text = text[2:-1]
    result: dict = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        raw_value = raw_value.strip()
        try:
            result[key] = float(raw_value)
        except ValueError:
            result[key] = raw_value
    return result if "width" in result and "height" in result else None


def _import_layers_from_record(project_root: Path, target_dir: Path, record: dict, previous_layers: list[dict] | None = None) -> list[dict]:
    previous_by_id = {
        str(layer.get("id") or ""): layer
        for layer in (previous_layers or [])
        if layer.get("id")
    }
    manual_mask_ids = {
        str(layer.get("visual_mask_id") or "")
        for layer in (previous_layers or [])
        if layer.get("manual_transform") and layer.get("visual_mask_id")
    }
    manual_fields = {
        "x",
        "y",
        "width",
        "height",
        "manual_transform",
        "original_geometry",
        "original_visual_rect",
        "visual_mask_id",
        "ltx_prompt",
        "ltx_preview",
        "ltx_video_path",
        "ltx_duration",
        "ltx_fps",
    }
    manual_mask_fields = {"x", "y", "width", "height"}
    persistent_layer_fields = {"ltx_prompt", "ltx_preview", "ltx_video_path", "ltx_duration", "ltx_fps"}
    imported_layers: list[dict] = []
    for index, layer in enumerate(list(record.get("figma_layers") or [])):
        clean = dict(layer)
        layer_id = str(clean.get("id") or "")
        asset_file = str(clean.pop("asset_file", "") or "")
        if asset_file:
            source_layer = _plugin_dir() / asset_file
            if not source_layer.exists():
                if clean.get("kind") == "image":
                    continue
            else:
                layer_target_dir = target_dir / "layers"
                layer_target_dir.mkdir(parents=True, exist_ok=True)
                layer_target = layer_target_dir / f"{index:03d}-{Path(asset_file).name}"
                layer_target.write_bytes(source_layer.read_bytes())
                if clean.get("kind") == "image":
                    _restore_logo_transparency(layer_target, str(clean.get("name") or ""))
                clean["asset_path"] = str(layer_target.relative_to(project_root))
        elif clean.get("kind") == "image":
            continue
        previous = previous_by_id.get(layer_id)
        if previous and isinstance(previous.get("motion_recipe"), dict):
            clean["motion_recipe"] = previous["motion_recipe"]
        if previous:
            for key in persistent_layer_fields:
                if key in previous:
                    clean[key] = previous[key]
        if previous and previous.get("manual_transform"):
            for key in manual_fields:
                if key in previous:
                    if key in {"original_geometry", "original_visual_rect"}:
                        rect = _normalize_rect(previous[key])
                        if rect is not None:
                            clean[key] = rect
                    else:
                        clean[key] = previous[key]
        if previous and layer_id in manual_mask_ids:
            for key in manual_mask_fields:
                if key in previous:
                    clean[key] = previous[key]
        imported_layers.append(clean)
    existing_ids = {str(layer.get("id") or "") for layer in imported_layers if layer.get("id")}
    for previous in previous_layers or []:
        previous_id = str(previous.get("id") or "")
        if not previous_id or previous_id in existing_ids:
            continue
        if previous_id.startswith("__"):
            imported_layers.append(dict(previous))
    return imported_layers


def motion_from_plugin_asset(project_root: Path, asset_id: str, start: float, duration: float) -> MotionSpec:
    record = _plugin_record(asset_id=asset_id)
    if not record:
        raise FileNotFoundError("Figma plugin asset not found")
    source = _plugin_dir() / str(record.get("asset_file") or "")
    if not source.exists():
        raise FileNotFoundError("Figma plugin asset file is missing")

    motion_id = f"figma-plugin-{uuid.uuid4().hex[:8]}"
    target_dir = project_root / "assets" / "figma-plugin" / motion_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "frame.png"
    target.write_bytes(source.read_bytes())
    imported_layers = _import_layers_from_record(project_root, target_dir, record)
    width = max(80, int(record.get("width") or 760))
    height = max(60, int(record.get("height") or 240))
    name = str(record.get("name") or "Figma frame")
    return MotionSpec(
        id=motion_id,
        kind="glass-card",
        design_preset="creator-vibe",
        text=name,
        start=max(0.0, float(start)),
        duration=max(0.25, float(duration)),
        x=80,
        y=80,
        width=width,
        height=height,
        text_scale=1.0,
        accent="#ffffff",
        background="rgba(255, 255, 255, 0.0)",
        animation="fade",
        enter_animation="none",
        exit_animation="none",
        enter_from="center",
        exit_to="center",
        enter_duration=0.05,
        exit_duration=0.05,
        easing="expo",
        prompt=f"Imported from Figma Plugin: {name}.",
        source_type="figma",
        asset_path=str(target.relative_to(project_root)),
        figma_node_id=str(record.get("node_id") or asset_id),
        figma_node_name=name,
        figma_layers=imported_layers,
    )


def refresh_motion_from_plugin_asset(project_root: Path, motion: MotionSpec) -> MotionSpec | None:
    record = _plugin_record(node_id=motion.figma_node_id, asset_id=motion.figma_node_id)
    if not record:
        return None
    source = _plugin_dir() / str(record.get("asset_file") or "")
    if not source.exists():
        raise FileNotFoundError("Figma plugin asset file is missing")

    target_dir = project_root / "assets" / "figma-plugin" / motion.id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "frame.png"
    target.write_bytes(source.read_bytes())
    name = str(record.get("name") or motion.figma_node_name or motion.text)
    updated_layers = _import_layers_from_record(project_root, target_dir, record, motion.figma_layers)
    return motion.model_copy(
        update={
            "text": name,
            "asset_path": str(target.relative_to(project_root)),
            "figma_node_id": str(record.get("node_id") or record.get("id") or motion.figma_node_id),
            "figma_node_name": name,
            "figma_layers": updated_layers,
        }
    )


def compare_plugin_asset_to_render(project_root: Path, motion: MotionSpec) -> dict:
    source_path = project_root / str(motion.asset_path or "")
    render_path = project_root / "assets" / f"{motion.id}.png"
    if not source_path.exists() or not render_path.exists():
        return {"available": False, "reason": "missing-source-or-render"}
    try:
        with Image.open(source_path).convert("RGB") as source, Image.open(render_path).convert("RGB") as rendered:
            target_size = (max(1, int(motion.width)), max(1, int(motion.height)))
            source = source.resize(target_size, Image.Resampling.LANCZOS)
            rendered = rendered.resize(target_size, Image.Resampling.LANCZOS)
            diff = ImageChops.difference(source, rendered)
            stat = ImageStat.Stat(diff)
            mean_delta = sum(stat.mean) / max(1, len(stat.mean))
            extrema = diff.getextrema()
            max_delta = max(channel[1] for channel in extrema)
            return {
                "available": True,
                "mean_delta": round(float(mean_delta), 3),
                "max_delta": int(max_delta),
                "layer_count": len(motion.figma_layers or []),
            }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
