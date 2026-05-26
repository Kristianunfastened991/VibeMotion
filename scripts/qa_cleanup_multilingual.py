from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import TranscriptData, WordToken
from app.services.analysis import find_suggestions


def _word(start: float, end: float, text: str) -> WordToken:
    return WordToken(start=start, end=end, text=text)


def _suggestions(words: list[WordToken]):
    transcript = TranscriptData(duration=4, text=" ".join(word.text for word in words), words=words)
    return find_suggestions(transcript)


def _assert_has_category(name: str, words: list[WordToken], category: str) -> None:
    suggestions = _suggestions(words)
    if not any(item.category == category for item in suggestions):
        raise AssertionError(f"{name}: expected {category}, got {[item.model_dump() for item in suggestions]}")


def _assert_no_suggestions(name: str, words: list[WordToken]) -> None:
    suggestions = _suggestions(words)
    if suggestions:
        raise AssertionError(f"{name}: expected no cuts, got {[item.model_dump() for item in suggestions]}")


def _assert_no_like_filler(name: str, words: list[WordToken]) -> None:
    suggestions = _suggestions(words)
    bad = [item for item in suggestions if item.category == "filler" and "like" in item.detail.casefold()]
    if bad:
        raise AssertionError(f"{name}: like should not be treated as filler, got {[item.model_dump() for item in bad]}")


def _assert_has_detail(name: str, words: list[WordToken], text: str) -> None:
    suggestions = _suggestions(words)
    if not any(text.casefold() in item.detail.casefold() for item in suggestions):
        raise AssertionError(f"{name}: expected detail containing {text!r}, got {[item.model_dump() for item in suggestions]}")


def main() -> None:
    _assert_has_category(
        "english filler phrase",
        [
            _word(0.00, 0.20, "Um"),
            _word(0.45, 0.70, "I"),
            _word(0.73, 1.00, "mean"),
            _word(1.40, 1.70, "this"),
        ],
        "filler",
    )
    _assert_no_suggestions(
        "english real like",
        [
            _word(0.00, 0.20, "I"),
            _word(0.22, 0.45, "like"),
            _word(0.47, 0.75, "this"),
        ],
    )
    _assert_no_like_filler(
        "english like this after pause",
        [
            _word(0.00, 0.20, "telegram"),
            _word(2.40, 2.90, "like"),
            _word(2.92, 3.10, "this"),
            _word(3.12, 3.30, "one"),
        ],
    )
    _assert_no_suggestions(
        "english semantic you know",
        [
            _word(0.00, 0.16, "you"),
            _word(0.18, 0.36, "know"),
            _word(0.38, 0.55, "this"),
            _word(0.57, 0.88, "already"),
        ],
    )
    _assert_has_detail(
        "english filler you know with pause",
        [
            _word(0.00, 0.16, "you"),
            _word(0.18, 0.36, "know"),
            _word(0.65, 0.82, "this"),
            _word(0.84, 1.10, "works"),
        ],
        "Filler phrase",
    )
    _assert_has_category(
        "russian filler phrase",
        [
            _word(0.00, 0.15, "\u044d\u044d"),
            _word(0.40, 0.70, "\u043d\u0443"),
            _word(0.90, 1.20, "\u043a\u0430\u043a"),
            _word(1.22, 1.45, "\u0431\u044b"),
        ],
        "filler",
    )
    _assert_has_category(
        "spanish filler phrase",
        [
            _word(0.00, 0.20, "este"),
            _word(0.42, 0.55, "o"),
            _word(0.57, 0.72, "sea"),
        ],
        "filler",
    )
    _assert_has_category(
        "german filler phrase",
        [
            _word(0.00, 0.20, "\u00e4hm"),
            _word(0.45, 0.70, "ich"),
            _word(0.73, 0.98, "meine"),
        ],
        "filler",
    )
    _assert_has_category(
        "french filler phrase",
        [
            _word(0.00, 0.20, "euh"),
            _word(0.50, 0.70, "tu"),
            _word(0.73, 0.95, "vois"),
        ],
        "filler",
    )
    _assert_has_category(
        "duplicate restart",
        [
            _word(0.00, 0.35, "recording"),
            _word(0.48, 0.80, "recording"),
            _word(1.10, 1.40, "works"),
        ],
        "retake",
    )
    _assert_has_detail(
        "english phrase retake",
        [
            _word(0.00, 0.12, "I"),
            _word(0.14, 0.34, "want"),
            _word(0.36, 0.48, "to"),
            _word(0.62, 0.74, "I"),
            _word(0.76, 0.96, "want"),
            _word(0.98, 1.10, "to"),
            _word(1.30, 1.60, "show"),
        ],
        "Repeated restart",
    )
    _assert_has_detail(
        "russian phrase retake",
        [
            _word(0.00, 0.12, "\u044f"),
            _word(0.14, 0.36, "\u0445\u043e\u0447\u0443"),
            _word(0.52, 0.64, "\u044f"),
            _word(0.66, 0.88, "\u0445\u043e\u0447\u0443"),
            _word(1.10, 1.40, "\u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c"),
        ],
        "Repeated restart",
    )
    _assert_has_detail(
        "spanish phrase retake",
        [
            _word(0.00, 0.15, "yo"),
            _word(0.17, 0.38, "voy"),
            _word(0.54, 0.69, "yo"),
            _word(0.71, 0.92, "voy"),
            _word(1.10, 1.40, "ahora"),
        ],
        "Repeated restart",
    )
    _assert_no_suggestions(
        "intentional emphasis no no no",
        [
            _word(0.00, 0.15, "no"),
            _word(0.18, 0.33, "no"),
            _word(0.36, 0.51, "no"),
        ],
    )
    _assert_no_suggestions(
        "intentional emphasis very very",
        [
            _word(0.00, 0.18, "very"),
            _word(0.20, 0.38, "very"),
            _word(0.40, 0.70, "good"),
        ],
    )
    _assert_no_suggestions(
        "intentional repeated count",
        [
            _word(0.00, 0.15, "one"),
            _word(0.17, 0.32, "two"),
            _word(0.34, 0.49, "one"),
            _word(0.51, 0.66, "two"),
        ],
    )
    _assert_no_suggestions(
        "demonstration phrase repeated",
        [
            _word(0.00, 0.22, "this"),
            _word(0.24, 0.48, "one"),
            _word(0.50, 0.72, "like"),
            _word(0.74, 0.96, "this"),
            _word(0.98, 1.22, "one"),
            _word(1.24, 1.46, "like"),
        ],
    )
    print("cleanup multilingual QA: pass")


if __name__ == "__main__":
    main()
