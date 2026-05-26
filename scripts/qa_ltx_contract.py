from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ltx_video import (  # noqa: E402
    _adaptive_ltx_max_side,
    _fit_ltx_size,
    _ltx_effective_prompt,
    _ltx_frame_count,
)


def test_ltx_prompt_expansion() -> None:
    prompt = _ltx_effective_prompt("Zoom in")
    lower = prompt.casefold()
    assert "dolly-in" in lower or "push" in lower or "zoom" in lower, prompt
    assert "must not look like a flat 2d" in lower, prompt
    assert "simple digital zoom" in lower, prompt
    assert "preserve the exact" in lower, prompt
    assert len(prompt.split()) >= 35, prompt


def test_ltx_frame_contract() -> None:
    assert _ltx_frame_count(4, 24) == 97
    assert (_ltx_frame_count(4, 24) - 1) % 8 == 0
    assert _ltx_frame_count(4, 8) == 33


def test_ltx_resolution_contract() -> None:
    width_480, height_480 = _fit_ltx_size(614, 931, 480)
    assert width_480 % 64 == 0 and height_480 % 64 == 0
    assert width_480 >= 320 and height_480 >= 512
    width, height = _fit_ltx_size(614, 931, 720)
    assert width % 64 == 0 and height % 64 == 0
    assert max(width, height) <= 720
    assert width >= 448 and height >= 704


def test_ltx_vram_gate_contract() -> None:
    # This is environment-sensitive, so only assert that the adaptive gate never upscales
    # beyond the user's request and never returns a non-positive side. If the current
    # machine is below the safe VRAM floor, the contract is a graceful RuntimeError.
    try:
        side = _adaptive_ltx_max_side(720)
    except RuntimeError as exc:
        assert "free gpu memory" in str(exc).casefold(), exc
        return
    assert 0 < side <= 720


if __name__ == "__main__":
    test_ltx_prompt_expansion()
    test_ltx_frame_contract()
    test_ltx_resolution_contract()
    test_ltx_vram_gate_contract()
    print("ltx contract QA passed")
