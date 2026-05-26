from __future__ import annotations

import json
from pathlib import Path
import socket
import urllib.error
import urllib.request

from app.core.config import settings


class OllamaError(RuntimeError):
    pass


def _chat_json_payload(payload: dict, timeout: int = 300) -> dict:
    request = urllib.request.Request(
        url=f"{settings.ollama_base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise OllamaError(f"Ollama request timed out after {timeout}s") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc

    content = body.get("message", {}).get("content", "").strip()
    if not content:
        raise OllamaError("Ollama returned empty content")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Invalid JSON from Ollama: {content[:400]}") from exc


def chat_json(system: str, user: str, timeout: int = 90) -> dict:
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    return _chat_json_payload(payload, timeout=timeout)


def chat_json_images(system: str, user: str, image_paths: list[Path], model: str | None = None) -> dict:
    import base64

    images = [base64.b64encode(path.read_bytes()).decode("ascii") for path in image_paths if path.exists()]
    if not images:
        raise OllamaError("No images provided")
    payload = {
        "model": model or settings.ollama_vision_model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": images},
        ],
    }
    return _chat_json_payload(payload, timeout=420)
