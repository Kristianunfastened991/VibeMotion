from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models.schemas import MotionSpec


FIGMA_API_BASE = "https://api.figma.com/v1"
MAX_FIGMA_IMAGE_EXPORTS = 16
MAX_FIGMA_THUMBNAILS = 80
FIGMA_FILE_CACHE_TTL = 60 * 60
_FILE_DOCUMENT_CACHE: dict[str, tuple[float, dict]] = {}
_IMAGE_FILL_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


class FigmaImportError(RuntimeError):
    pass


class FigmaRateLimitError(FigmaImportError):
    pass


def _node_id_from_url(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    raw = query.get("node-id", [None])[0]
    if not raw:
        return None
    return raw.replace("-", ":")


def _file_key_from_url(value: str) -> str:
    match = re.search(r"figma\.com/(?:file|design)/([^/?#]+)", value)
    if not match:
        raise FigmaImportError("Figma URL must look like https://figma.com/design/<fileKey>/...")
    return match.group(1)


def parse_figma_reference(figma_url: str, node_id: str | None = None) -> tuple[str, str]:
    file_key = _file_key_from_url(figma_url)
    resolved_node = (node_id or _node_id_from_url(figma_url) or "").strip().replace("-", ":")
    if not resolved_node:
        raise FigmaImportError("Figma node id is missing. Copy a frame link with node-id or paste node id manually.")
    return file_key, resolved_node


def _figma_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        url=f"{FIGMA_API_BASE}{path}",
        headers={"X-Figma-Token": token},
        method="GET",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            if exc.code == 429 and attempt < 3:
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2.0 + attempt * 2.0
                time.sleep(min(10.0, max(1.0, delay)))
                continue
            if exc.code == 429:
                raise FigmaRateLimitError("Figma API rate limit exceeded.") from exc
            raise FigmaImportError(f"Figma API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise FigmaImportError(f"Figma request failed: {exc}") from exc
    raise FigmaImportError("Figma request failed after retries.")


def _download(url: str, output: Path) -> None:
    request = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            output.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise FigmaImportError(f"Figma image download failed: {exc}") from exc


def _safe_layer_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")[:80] or "layer"


def _static_assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "static" / "assets"


def _figma_cache_dir() -> Path:
    path = _static_assets_dir() / "figma-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _figma_file_cache_path(file_key: str) -> Path:
    return _figma_cache_dir() / f"{_safe_layer_name(file_key)}.json"


def _read_disk_file_cache(file_key: str) -> tuple[float, dict] | None:
    path = _figma_file_cache_path(file_key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    document = payload.get("document")
    cached_at = float(payload.get("cached_at") or 0)
    if not isinstance(document, dict) or cached_at <= 0:
        return None
    return cached_at, document


def _write_disk_file_cache(file_key: str, document: dict) -> None:
    payload = {"cached_at": time.time(), "document": document}
    _figma_file_cache_path(file_key).write_text(json.dumps(payload), encoding="utf-8")


def _node_document(file_key: str, node_id: str, token: str) -> dict:
    cached = _node_from_cached_file(file_key, node_id)
    if cached:
        return cached
    ids = urllib.parse.quote(node_id, safe=":")
    payload = _figma_get(f"/files/{file_key}/nodes?ids={ids}", token)
    node = payload.get("nodes", {}).get(node_id)
    if not node or not node.get("document"):
        raise FigmaImportError("Figma node not found or inaccessible")
    return node["document"]


def _file_document(file_key: str, token: str) -> dict:
    cached = _cached_file_document(file_key, allow_stale=False)
    if cached:
        return cached
    try:
        payload = _figma_get(f"/files/{file_key}", token)
    except FigmaRateLimitError:
        cached = _cached_file_document(file_key, allow_stale=True)
        if cached:
            return cached
        raise
    document = payload.get("document")
    if not document:
        raise FigmaImportError("Figma file not found or inaccessible")
    _FILE_DOCUMENT_CACHE[file_key] = (time.time(), document)
    _write_disk_file_cache(file_key, document)
    return document


def _cached_file_document(file_key: str, allow_stale: bool) -> dict | None:
    cached = _FILE_DOCUMENT_CACHE.get(file_key)
    if not cached:
        cached = _read_disk_file_cache(file_key)
        if cached:
            _FILE_DOCUMENT_CACHE[file_key] = cached
    if not cached:
        return None
    cached_at, document = cached
    if allow_stale or time.time() - cached_at <= FIGMA_FILE_CACHE_TTL:
        return document
    return None


def _find_node_by_id(node: dict, node_id: str) -> dict | None:
    if str(node.get("id") or "") == node_id:
        return node
    for child in node.get("children") or []:
        found = _find_node_by_id(child, node_id)
        if found:
            return found
    return None


def _node_from_cached_file(file_key: str, node_id: str) -> dict | None:
    document = _cached_file_document(file_key, allow_stale=True)
    if not document:
        return None
    return _find_node_by_id(document, node_id)


def _export_node_png(file_key: str, node_id: str, token: str, output: Path, scale: float = 1.0) -> None:
    ids = urllib.parse.quote(node_id, safe=":")
    safe_scale = max(0.1, min(4.0, float(scale or 1.0)))
    payload = _figma_get(f"/images/{file_key}?ids={ids}&format=png&scale={safe_scale:g}", token)
    image_url = payload.get("images", {}).get(node_id)
    if not image_url:
        raise FigmaImportError("Figma did not return an export URL for this node")
    _download(image_url, output)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _export_nodes_png(file_key: str, node_ids: list[str], token: str, layer_dir: Path, project_root: Path, scale: float = 1.0) -> dict[str, str]:
    unique_ids = list(dict.fromkeys(node_id for node_id in node_ids if node_id))
    exported: dict[str, str] = {}
    safe_scale = max(0.1, min(4.0, float(scale or 1.0)))
    for chunk in _chunks(unique_ids, 25):
        ids = urllib.parse.quote(",".join(chunk), safe=":,")
        payload = _figma_get(f"/images/{file_key}?ids={ids}&format=png&scale={safe_scale:g}", token)
        images = payload.get("images", {}) or {}
        for node_id in chunk:
            image_url = images.get(node_id)
            if not image_url:
                continue
            output = layer_dir / f"{_safe_layer_name(node_id)}.png"
            _download(image_url, output)
            exported[node_id] = str(output.relative_to(project_root))
    return exported


def _image_fill_urls(file_key: str, token: str) -> dict[str, str]:
    cached = _IMAGE_FILL_CACHE.get(file_key)
    if cached and time.time() - cached[0] <= FIGMA_FILE_CACHE_TTL:
        return cached[1]
    payload = _figma_get(f"/files/{file_key}/images", token)
    images = payload.get("images") or payload.get("meta", {}).get("images") or {}
    _IMAGE_FILL_CACHE[file_key] = (time.time(), images)
    return images


def _color_to_css(color: dict, opacity: float = 1.0) -> str:
    r = int(round(float(color.get("r", 0)) * 255))
    g = int(round(float(color.get("g", 0)) * 255))
    b = int(round(float(color.get("b", 0)) * 255))
    a = max(0.0, min(1.0, float(color.get("a", 1)) * opacity))
    return f"rgba({r}, {g}, {b}, {a:.4f})"


def _solid_fill(node: dict) -> str | None:
    opacity = float(node.get("opacity", 1) or 1)
    for paint in node.get("fills") or []:
        if not paint.get("visible", True):
            continue
        if paint.get("type") == "SOLID" and paint.get("color"):
            return _color_to_css(paint["color"], float(paint.get("opacity", 1)) * opacity)
    return None


def _has_image_fill(node: dict) -> bool:
    return any(paint.get("visible", True) and paint.get("type") == "IMAGE" for paint in (node.get("fills") or []))


def _has_image_fill_deep(node: dict) -> bool:
    if _has_image_fill(node):
        return True
    return any(_has_image_fill_deep(child) for child in (node.get("children") or []) if child.get("visible", True))


def _image_ref(node: dict) -> str | None:
    for paint in node.get("fills") or []:
        if paint.get("visible", True) and paint.get("type") == "IMAGE" and paint.get("imageRef"):
            return str(paint["imageRef"])
    return None


def _radius(node: dict) -> float:
    if "cornerRadius" in node and isinstance(node["cornerRadius"], (int, float)):
        return float(node["cornerRadius"])
    radii = node.get("rectangleCornerRadii")
    if isinstance(radii, list) and radii:
        return float(max(radii))
    return 0.0


def _download_image_ref(file_key: str, image_ref: str, token: str, output_dir: Path, relative_root: Path | None = None) -> str | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{_safe_layer_name(image_ref)}.png"
    if not output.exists() or output.stat().st_size == 0:
        image_url = _image_fill_urls(file_key, token).get(image_ref)
        if not image_url:
            return None
        _download(image_url, output)
    if relative_root:
        return str(output.relative_to(relative_root))
    return str(output)


def _layer_box(node: dict, root_box: dict) -> tuple[float, float, float, float] | None:
    box = node.get("absoluteBoundingBox")
    if not box:
        return None
    width = float(box.get("width") or 0)
    height = float(box.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    return (
        float(box.get("x") or 0) - float(root_box.get("x") or 0),
        float(box.get("y") or 0) - float(root_box.get("y") or 0),
        width,
        height,
    )


def _export_layer_asset(file_key: str, node: dict, token: str, layer_dir: Path, project_root: Path) -> str | None:
    node_id = str(node.get("id") or "")
    if not node_id:
        return None
    output = layer_dir / f"{_safe_layer_name(node_id)}.png"
    _export_node_png(file_key, node_id, token, output)
    return str(output.relative_to(project_root))


def _build_layers(
    file_key: str,
    node: dict,
    root_box: dict,
    token: str,
    layer_dir: Path,
    project_root: Path,
    layers: list[dict],
    include_self: bool = False,
    image_node_ids: list[str] | None = None,
) -> None:
    if image_node_ids is None:
        image_node_ids = []
    if not node.get("visible", True):
        return
    box = _layer_box(node, root_box)
    if box and (include_self or node.get("type") != "GROUP"):
        x, y, width, height = box
        layer_base = {
            "id": str(node.get("id") or uuid.uuid4().hex[:8]),
            "name": str(node.get("name") or node.get("type") or "Layer"),
            "node_type": str(node.get("type") or ""),
            "x": round(x, 3),
            "y": round(y, 3),
            "width": round(width, 3),
            "height": round(height, 3),
            "opacity": float(node.get("opacity", 1) or 1),
        }
        fill = _solid_fill(node)
        children = node.get("children") or []
        if node.get("type") == "TEXT":
            style = node.get("style") or {}
            layers.append(
                {
                    **layer_base,
                    "kind": "text",
                    "text": str(node.get("characters") or ""),
                    "font_size": float(style.get("fontSize") or max(10, height * 0.5)),
                    "font_weight": int(style.get("fontWeight") or 500),
                    "font_family": str(style.get("fontFamily") or "Arial"),
                    "line_height": float((style.get("lineHeightPx") or style.get("fontSize") or 16)),
                    "color": fill or "rgba(0, 0, 0, 1)",
                    "text_align": str(style.get("textAlignHorizontal") or "LEFT").lower(),
                }
            )
        elif _has_image_fill(node):
            asset_node_id = str(node.get("id") or "")
            if asset_node_id:
                image_node_ids.append(asset_node_id)
                layer = {**layer_base, "kind": "image", "asset_node_id": asset_node_id}
                image_ref = _image_ref(node)
                if image_ref:
                    layer["image_ref"] = image_ref
                layers.append(layer)
        elif fill:
            layers.append({**layer_base, "kind": "shape", "fill": fill, "radius": round(_radius(node), 3)})

    for child in node.get("children") or []:
        _build_layers(file_key, child, root_box, token, layer_dir, project_root, layers, image_node_ids=image_node_ids)


def _asset_kind(node: dict) -> str:
    node_type = str(node.get("type") or "LAYER")
    if node_type == "TEXT":
        return "text"
    if _has_image_fill(node):
        return "image"
    if node_type in {"FRAME", "GROUP", "COMPONENT", "INSTANCE", "SECTION"}:
        return "composition"
    return "shape"


def _collect_asset_items(node: dict, root_box: dict, items: list[dict], depth: int = 0, path: str = "", limit: int = 240) -> None:
    if len(items) >= limit or not node.get("visible", True):
        return
    node_id = str(node.get("id") or "")
    name = str(node.get("name") or node.get("type") or "Layer")
    node_type = str(node.get("type") or "")
    next_path = f"{path} / {name}" if path else name
    box = _layer_box(node, root_box)
    if depth > 0 and box and node_type not in {"CANVAS", "DOCUMENT"}:
        x, y, width, height = box
        items.append(
            {
                "node_id": node_id,
                "name": name,
                "path": next_path,
                "node_type": node_type,
                "kind": _asset_kind(node),
                "depth": depth,
                "x": round(x, 3),
                "y": round(y, 3),
                "width": round(width, 3),
                "height": round(height, 3),
                "children_count": len(node.get("children") or []),
            }
        )
    for child in node.get("children") or []:
        _collect_asset_items(child, root_box, items, depth + 1, next_path, limit)


def _frame_item(node: dict, page_name: str, depth: int) -> dict | None:
    box = node.get("absoluteBoundingBox")
    if not box:
        return None
    width = float(box.get("width") or 0)
    height = float(box.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    node_type = str(node.get("type") or "")
    name = str(node.get("name") or node_type or "Frame")
    return {
        "node_id": str(node.get("id") or ""),
        "name": name,
        "path": f"{page_name} / {name}",
        "page": page_name,
        "node_type": node_type,
        "kind": "composition",
        "depth": depth,
        "x": round(float(box.get("x") or 0), 3),
        "y": round(float(box.get("y") or 0), 3),
        "width": round(width, 3),
        "height": round(height, 3),
        "children_count": len(node.get("children") or []),
    }


def _collect_canvas_frames(node: dict, page_name: str, items: list[dict], depth: int = 0, limit: int = 360) -> None:
    if len(items) >= limit or not node.get("visible", True):
        return
    node_type = str(node.get("type") or "")
    frame_types = {"FRAME", "COMPONENT", "INSTANCE", "COMPONENT_SET"}
    if node_type in frame_types:
        item = _frame_item(node, page_name, depth)
        if item:
            items.append(item)
        return
    if node_type == "SECTION":
        for child in node.get("children") or []:
            _collect_canvas_frames(child, page_name, items, depth + 1, limit)
        return
    for child in node.get("children") or []:
        _collect_canvas_frames(child, page_name, items, depth + 1, limit)


def _export_frame_thumbnails(file_key: str, node_ids: list[str], token: str) -> dict[str, str]:
    thumb_root = _static_assets_dir() / "figma-thumbs" / _safe_layer_name(file_key)
    thumb_root.mkdir(parents=True, exist_ok=True)
    unique_ids = list(dict.fromkeys(node_id for node_id in node_ids if node_id))[:MAX_FIGMA_THUMBNAILS]
    thumbnails: dict[str, str] = {}
    missing_ids: list[str] = []
    for node_id in unique_ids:
        output = thumb_root / f"{_safe_layer_name(node_id)}.png"
        if output.exists() and output.stat().st_size > 0:
            thumbnails[node_id] = f"/app/assets/figma-thumbs/{_safe_layer_name(file_key)}/{output.name}"
        else:
            missing_ids.append(node_id)
    for chunk in _chunks(missing_ids, 25):
        ids = urllib.parse.quote(",".join(chunk), safe=":,")
        payload = _figma_get(f"/images/{file_key}?ids={ids}&format=png&scale=0.18", token)
        images = payload.get("images", {}) or {}
        for node_id in chunk:
            image_url = images.get(node_id)
            if not image_url:
                continue
            output = thumb_root / f"{_safe_layer_name(node_id)}.png"
            _download(image_url, output)
            thumbnails[node_id] = f"/app/assets/figma-thumbs/{_safe_layer_name(file_key)}/{output.name}"
    return thumbnails


def _thumbnail_url(file_key: str, node_id: str, suffix: str = "") -> str:
    return f"/app/assets/figma-thumbs/{_safe_layer_name(file_key)}/{_safe_layer_name(node_id)}{suffix}.png"


def _thumb_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_local_thumb_node(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    node: dict,
    root_box: dict,
    scale: float,
    offset_x: float,
    offset_y: float,
    file_key: str,
    token: str,
    download_images: bool = True,
) -> None:
    if not node.get("visible", True):
        return
    box = node.get("absoluteBoundingBox")
    if box:
        x = (float(box.get("x") or 0) - float(root_box.get("x") or 0)) * scale + offset_x
        y = (float(box.get("y") or 0) - float(root_box.get("y") or 0)) * scale + offset_y
        width = float(box.get("width") or 0) * scale
        height = float(box.get("height") or 0) * scale
        if width > 0.5 and height > 0.5:
            fill = _solid_fill(node)
            node_type = str(node.get("type") or "")
            if _has_image_fill(node):
                radius = max(1, int(_radius(node) * scale))
                pasted = False
                image_ref = _image_ref(node)
                if image_ref and download_images:
                    try:
                        local_path = _download_image_ref(file_key, image_ref, token, _static_assets_dir() / "figma-images" / _safe_layer_name(file_key))
                        if local_path:
                            with Image.open(local_path).convert("RGBA") as child:
                                child.thumbnail((max(1, int(width)), max(1, int(height))), Image.Resampling.LANCZOS)
                                crop = Image.new("RGBA", (max(1, int(width)), max(1, int(height))), (0, 0, 0, 0))
                                crop.alpha_composite(child, ((crop.width - child.width) // 2, (crop.height - child.height) // 2))
                                image.alpha_composite(crop, (int(x), int(y)))
                                pasted = True
                    except FigmaImportError:
                        pasted = False
                    except OSError:
                        pasted = False
                if not pasted:
                    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=(210, 210, 210, 255), outline=(188, 188, 188, 255), width=1)
            elif fill and node_type != "TEXT":
                rgba = [float(part.strip()) for part in re.findall(r"[\d.]+", fill)[:4]]
                if len(rgba) >= 3:
                    alpha = int((rgba[3] if len(rgba) > 3 else 1) * 255)
                    color = (int(rgba[0]), int(rgba[1]), int(rgba[2]), alpha)
                    radius = max(0, int(_radius(node) * scale))
                    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=color)
            if node_type == "TEXT":
                text = re.sub(r"\s+", " ", str(node.get("characters") or "")).strip()
                if text:
                    style = node.get("style") or {}
                    font_size = max(5, min(24, int(float(style.get("fontSize") or 14) * scale)))
                    font = _thumb_font(font_size)
                    color = (0, 0, 0, 235)
                    draw.text((x, y), text[:80], font=font, fill=color)
    for child in node.get("children") or []:
        _draw_local_thumb_node(image, draw, child, root_box, scale, offset_x, offset_y, file_key, token, download_images=download_images)


def _render_local_frame_thumbnail(file_key: str, node: dict, token: str, download_images: bool = False) -> str | None:
    box = node.get("absoluteBoundingBox")
    node_id = str(node.get("id") or "")
    if not box or not node_id:
        return None
    width = float(box.get("width") or 0)
    height = float(box.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    thumb_root = _static_assets_dir() / "figma-thumbs" / _safe_layer_name(file_key)
    thumb_root.mkdir(parents=True, exist_ok=True)
    suffix = "-local" if download_images else "-structure"
    output = thumb_root / f"{_safe_layer_name(node_id)}{suffix}.png"
    if output.exists() and output.stat().st_size > 0:
        return _thumbnail_url(file_key, node_id, suffix)

    canvas_w, canvas_h = 320, 200
    scale = min(canvas_w / width, canvas_h / height)
    render_w = max(1, int(width * scale))
    render_h = max(1, int(height * scale))
    offset_x = (canvas_w - render_w) / 2
    offset_y = (canvas_h - render_h) / 2
    image = Image.new("RGBA", (canvas_w, canvas_h), (246, 246, 242, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((offset_x, offset_y, offset_x + render_w, offset_y + render_h), radius=4, fill=(255, 255, 255, 255))
    _draw_local_thumb_node(image, draw, node, box, scale, offset_x, offset_y, file_key, token, download_images=download_images)
    image.convert("RGB").save(output, quality=88)
    return _thumbnail_url(file_key, node_id, suffix)


def list_figma_assets(figma_url: str, token: str | None, node_id: str | None) -> dict:
    access_token = (token or os.getenv("FIGMA_ACCESS_TOKEN") or "").strip()
    if not access_token:
        raise FigmaImportError("Figma token is missing. Paste a token or set FIGMA_ACCESS_TOKEN.")
    file_key = _file_key_from_url(figma_url)
    document = _file_document(file_key, access_token)
    items: list[dict] = []
    for page in document.get("children") or []:
        if str(page.get("type") or "") != "CANVAS":
            continue
        page_name = str(page.get("name") or "Page")
        for child in page.get("children") or []:
            _collect_canvas_frames(child, page_name, items, depth=1)
    items.sort(key=lambda item: (str(item.get("page") or ""), float(item.get("y") or 0), float(item.get("x") or 0)))
    node_by_id = {
        str(item.get("node_id") or ""): _node_from_cached_file(file_key, str(item.get("node_id") or ""))
        for item in items
    }
    image_fill_ids = {
        node_id
        for node_id, node in node_by_id.items()
        if node and _has_image_fill_deep(node)
    }
    thumbnails: dict[str, str] = {}
    thumbnail_warning = "Light Figma mode: frame list loaded without REST image exports to avoid rate limits."
    for item in items:
        node = node_by_id.get(str(item.get("node_id") or ""))
        local_url = _render_local_frame_thumbnail(file_key, node, access_token) if node and _has_image_fill_deep(node) else None
        if local_url:
            item["thumbnail_url"] = local_url
            continue
        thumbnail_url = thumbnails.get(str(item.get("node_id") or ""))
        if thumbnail_url:
            item["thumbnail_url"] = thumbnail_url
            continue
        local_url = _render_local_frame_thumbnail(file_key, node, access_token) if node else None
        if local_url:
            item["thumbnail_url"] = local_url
    return {
        "file_key": file_key,
        "node_id": (node_id or _node_id_from_url(figma_url) or "").strip().replace("-", ":") or None,
        "name": str(document.get("name") or "Figma file"),
        "assets": items,
        "truncated": len(items) >= 360,
        "thumbnail_warning": thumbnail_warning,
    }


def import_figma_node(
    figma_url: str,
    token: str | None,
    node_id: str | None,
    project_root: Path,
    start: float,
    duration: float,
) -> MotionSpec:
    access_token = (token or os.getenv("FIGMA_ACCESS_TOKEN") or "").strip()
    if not access_token:
        raise FigmaImportError("Figma token is missing. Paste a token or set FIGMA_ACCESS_TOKEN.")

    file_key, resolved_node_id = parse_figma_reference(figma_url, node_id)
    document = _node_document(file_key, resolved_node_id, access_token)
    node_name = str(document.get("name") or "Figma frame")
    bounds = document.get("absoluteBoundingBox") or {}
    logical_width = int(round(float(bounds.get("width") or 0)))
    logical_height = int(round(float(bounds.get("height") or 0)))

    imported_dir = project_root / "assets" / "figma"
    imported_dir.mkdir(parents=True, exist_ok=True)
    motion_id = f"figma-{uuid.uuid4().hex[:8]}"
    layer_dir = imported_dir / motion_id
    layer_dir.mkdir(parents=True, exist_ok=True)
    full_frame_asset_path: str | None = None
    image_export_warning = ""
    fallback_asset = layer_dir / "frame.png"
    layers: list[dict] = []
    image_node_ids: list[str] = []
    _build_layers(
        file_key,
        document,
        bounds,
        access_token,
        layer_dir,
        project_root,
        layers,
        include_self=True,
        image_node_ids=image_node_ids,
    )
    if image_node_ids:
        requested_image_ids = image_node_ids[:MAX_FIGMA_IMAGE_EXPORTS]
        skipped_count = max(0, len(set(image_node_ids)) - len(set(requested_image_ids)))
        image_ref_paths: dict[str, str] = {}
        refs = [str(layer.get("image_ref") or "") for layer in layers if layer.get("kind") == "image" and layer.get("image_ref")]
        for image_ref in dict.fromkeys(ref for ref in refs if ref):
            try:
                asset_path = _download_image_ref(file_key, image_ref, access_token, layer_dir / "image-fills", project_root)
            except FigmaImportError:
                asset_path = None
            if asset_path:
                image_ref_paths[image_ref] = asset_path
        asset_paths: dict[str, str] = {}
        missing_image_ids = [
            str(layer["asset_node_id"])
            for layer in layers
            if layer.get("kind") == "image"
            and layer.get("asset_node_id")
            and not image_ref_paths.get(str(layer.get("image_ref") or ""))
        ][:MAX_FIGMA_IMAGE_EXPORTS]
        if missing_image_ids and not full_frame_asset_path:
            image_export_warning = " Some image layers need Figma Plugin import for full fidelity without REST image limits."
        resolved_layers: list[dict] = []
        resolved_image_count = 0
        for layer in layers:
            if layer.get("kind") == "image" and layer.get("asset_node_id"):
                asset_path = image_ref_paths.get(str(layer.get("image_ref") or "")) or asset_paths.get(str(layer["asset_node_id"]))
                if not asset_path:
                    if full_frame_asset_path:
                        resolved_layers.append(layer)
                    continue
                layer = {key: value for key, value in layer.items() if key not in {"asset_node_id", "image_ref"}}
                layer["asset_path"] = asset_path
                resolved_image_count += 1
            resolved_layers.append(layer)
        layers = resolved_layers
        if resolved_image_count:
            full_frame_asset_path = None
            if len(set(refs)) > resolved_image_count:
                image_export_warning = (
                    f" Imported {resolved_image_count} image fills; "
                    f"{len(set(refs)) - resolved_image_count} image fills were skipped."
                    + image_export_warning
                )
        elif not full_frame_asset_path and refs:
            image_export_warning = f" {len(set(refs))} image fills were skipped because Figma did not return image URLs." + image_export_warning
        if skipped_count:
            image_export_warning = f" {skipped_count} image layers were skipped to avoid Figma rate limits." + image_export_warning

    width = max(80, logical_width)
    height = max(60, logical_height)
    if not layers and not full_frame_asset_path:
        try:
            _export_node_png(file_key, resolved_node_id, access_token, fallback_asset)
            with Image.open(fallback_asset) as image:
                pixel_width, pixel_height = image.size
            width = max(width, pixel_width)
            height = max(height, pixel_height)
            layers.append(
                {
                    "id": resolved_node_id,
                    "name": node_name,
                    "kind": "image",
                    "node_type": str(document.get("type") or "FRAME"),
                    "x": 0,
                    "y": 0,
                    "width": width,
                    "height": height,
                    "opacity": 1,
                    "asset_path": str(fallback_asset.relative_to(project_root)),
                }
            )
        except FigmaRateLimitError:
            image_export_warning = " Figma rate-limited image export; imported an editable placeholder."
            layers.append(
                {
                    "id": resolved_node_id,
                    "name": node_name,
                    "kind": "shape",
                    "node_type": str(document.get("type") or "FRAME"),
                    "x": 0,
                    "y": 0,
                    "width": width,
                    "height": height,
                    "opacity": 1,
                    "fill": "rgba(255, 255, 255, 0.35)",
                    "radius": 24,
                }
            )
            layers.append(
                {
                    "id": f"{resolved_node_id}:label",
                    "name": f"{node_name} label",
                    "kind": "text",
                    "node_type": "TEXT",
                    "x": max(16, width * 0.08),
                    "y": max(16, height * 0.40),
                    "width": max(80, width * 0.84),
                    "height": max(30, height * 0.18),
                    "opacity": 1,
                    "text": node_name,
                    "font_size": max(14, min(42, height * 0.08)),
                    "font_weight": 600,
                    "font_family": "Arial",
                    "line_height": max(16, min(48, height * 0.1)),
                    "color": "rgba(0, 0, 0, 1)",
                    "text_align": "center",
                }
            )

    return MotionSpec(
        id=motion_id,
        kind="glass-card",
        design_preset="creator-vibe",
        text=node_name,
        start=max(0.0, start),
        duration=max(0.25, duration),
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
        prompt=f"Imported from Figma: {node_name}.{image_export_warning}",
        source_type="figma",
        asset_path=full_frame_asset_path,
        figma_file_key=file_key,
        figma_node_id=resolved_node_id,
        figma_node_name=node_name,
        figma_layers=layers,
    )


def refresh_motion_from_figma(
    figma_url: str,
    token: str | None,
    project_root: Path,
    motion: MotionSpec,
) -> MotionSpec | None:
    if getattr(motion, "source_type", "generated") != "figma" or not motion.figma_node_id:
        return None
    refreshed = import_figma_node(
        figma_url=figma_url,
        token=token,
        node_id=motion.figma_node_id,
        project_root=project_root,
        start=motion.start,
        duration=motion.duration,
    )
    previous_by_id = {
        str(layer.get("id") or ""): layer
        for layer in (motion.figma_layers or [])
        if layer.get("id")
    }
    merged_layers: list[dict] = []
    for layer in refreshed.figma_layers or []:
        next_layer = dict(layer)
        previous = previous_by_id.get(str(next_layer.get("id") or ""))
        if previous and isinstance(previous.get("motion_recipe"), dict):
            next_layer["motion_recipe"] = previous["motion_recipe"]
        merged_layers.append(next_layer)
    merged_ids = {str(layer.get("id") or "") for layer in merged_layers if layer.get("id")}
    for previous in motion.figma_layers or []:
        previous_id = str(previous.get("id") or "")
        if not previous_id or previous_id in merged_ids:
            continue
        if previous_id.startswith("__"):
            merged_layers.append(dict(previous))
    return refreshed.model_copy(
        update={
            "id": motion.id,
            "start": motion.start,
            "duration": motion.duration,
            "x": motion.x,
            "y": motion.y,
            "width": motion.width,
            "height": motion.height,
            "text_scale": motion.text_scale,
            "accent": motion.accent,
            "background": motion.background,
            "animation": motion.animation,
            "enter_animation": motion.enter_animation,
            "exit_animation": motion.exit_animation,
            "enter_from": motion.enter_from,
            "exit_to": motion.exit_to,
            "enter_duration": motion.enter_duration,
            "exit_duration": motion.exit_duration,
            "easing": motion.easing,
            "figma_layers": merged_layers,
        }
    )
