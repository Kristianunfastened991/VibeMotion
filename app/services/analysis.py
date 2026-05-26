from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path

from app.models.schemas import CutRange, CutSuggestion, TranscriptData


# Keep a small safety margin around detected speech. A full 1s margin on both
# sides cancels most real talking-head pauses, because a 1.5s pause would become
# a negative-length cut.
SILENCE_CUT_GUARD_SECONDS = 0.35
FILLER_PADDING_SECONDS = 0.04
MIN_CUT_SECONDS = 0.15
SOFT_FILLER_CONTEXT_GAP_SECONDS = 0.12
RETAKE_MAX_GAP_SECONDS = 0.22
RETAKE_MAX_PHRASE_WORDS = 4
RETAKE_PHRASE_MAX_GAP_SECONDS = 0.42
RETAKE_PHRASE_MAX_DURATION_SECONDS = 2.2

HARD_FILLERS = {
    "ahem",
    "ah",
    "ahm",
    "eh",
    "em",
    "er",
    "erm",
    "hm",
    "hmm",
    "mhm",
    "mm",
    "mmm",
    "uh",
    "uhh",
    "uhm",
    "um",
    "umm",
    "euh",
    "heu",
    "äh",
    "ähm",
    "э",
    "ээ",
    "эээ",
    "эм",
    "мм",
    "ммм",
}

# Soft fillers can be real words in normal sentences, so they are only removed
# when they are isolated by a small pause or at the edge of a segment.
SOFT_FILLERS = {
    "actually",
    "basically",
    "literally",
    "okay",
    "ok",
    "right",
    "so",
    "well",
    "вообще",
    "вот",
    "значит",
    "короче",
    "ну",
    "собственно",
    "типа",
    "alors",
    "bah",
    "ben",
    "bon",
    "bueno",
    "donc",
    "este",
    "genre",
    "naja",
    "pues",
    "tipo",
    "also",
    "genau",
    "halt",
    "irgendwie",
    "quasi",
}

FILLER_PHRASES = (
    ("i", "mean"),
    ("kind", "of"),
    ("sort", "of"),
    ("you", "know"),
    ("you", "see"),
    ("в", "общем"),
    ("как", "бы"),
    ("то", "есть"),
    ("o", "sea"),
    ("es", "decir"),
    ("tu", "vois"),
    ("je", "veux", "dire"),
    ("quer", "dizer"),
    ("ich", "meine"),
)

RETAKE_STARTERS = {
    "i",
    "im",
    "ive",
    "we",
    "were",
    "you",
    "youre",
    "he",
    "she",
    "they",
    "it",
    "its",
    "let",
    "lets",
    "\u044f",
    "\u043c\u044b",
    "\u0442\u044b",
    "\u0432\u044b",
    "\u043e\u043d",
    "\u043e\u043d\u0430",
    "yo",
    "tu",
    "nosotros",
    "je",
    "tu",
    "nous",
    "vous",
    "ich",
    "wir",
    "du",
    "eu",
    "nos",
}

RETAKE_PROTECTED_REPEATS = {
    "again",
    "go",
    "more",
    "no",
    "now",
    "ok",
    "okay",
    "really",
    "so",
    "very",
    "yeah",
    "yes",
    "\u0434\u0430",
    "\u043d\u0435\u0442",
    "\u0435\u0449\u0435",
    "\u043e\u0447\u0435\u043d\u044c",
    "si",
    "oui",
    "non",
    "ja",
    "nein",
    "bien",
    "bom",
}


def _guarded_silence_cut(start: float, end: float) -> tuple[float, float] | None:
    guarded_start = max(0.0, start + SILENCE_CUT_GUARD_SECONDS)
    guarded_end = max(guarded_start, end - SILENCE_CUT_GUARD_SECONDS)
    if guarded_end - guarded_start < MIN_CUT_SECONDS:
        return None
    return round(guarded_start, 3), round(guarded_end, 3)


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _normalize_word(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    text = _strip_accents(text)
    text = re.sub(r"[^\w\s]+", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "", text)


def _word_gap_before(words, index: int) -> float:
    if index <= 0:
        return 999.0
    return max(0.0, words[index].start - words[index - 1].end)


def _word_gap_after(words, index: int) -> float:
    if index >= len(words) - 1:
        return 999.0
    return max(0.0, words[index + 1].start - words[index].end)


def _has_soft_filler_context(words, start_index: int, end_index: int | None = None) -> bool:
    end_index = start_index if end_index is None else end_index
    before = _word_gap_before(words, start_index) >= SOFT_FILLER_CONTEXT_GAP_SECONDS
    after = _word_gap_after(words, end_index) >= SOFT_FILLER_CONTEXT_GAP_SECONDS
    if start_index == 0 and end_index == len(words) - 1:
        return True
    if start_index == 0:
        return after
    if end_index == len(words) - 1:
        return before
    return before or after


def _is_hesitation_noise(text: str) -> bool:
    if not text:
        return False
    if text in HARD_FILLERS:
        return True
    return bool(re.fullmatch(r"(a+h+|e+h+|u+h+|u+m+|h+m+|m+h*m*|э+|а+|м+|e+u+h*)", text))


def _phrase_at(normalized_words: list[str], index: int) -> tuple[str, int] | None:
    for phrase in FILLER_PHRASES:
        end_index = index + len(phrase)
        if tuple(normalized_words[index:end_index]) == phrase:
            return " ".join(phrase), end_index - 1
    return None


def _is_safe_duplicate_retake(words, normalized_words: list[str], index: int) -> bool:
    if index <= 0:
        return False
    current = normalized_words[index]
    previous = normalized_words[index - 1]
    if not current or current != previous:
        return False
    if len(current) < 3 and current not in {"i", "я"}:
        return False
    if _word_gap_before(words, index) > RETAKE_MAX_GAP_SECONDS:
        return False
    return words[index - 1].end - words[index - 1].start <= 0.65


def _phrase_gaps_are_tight(words, start_index: int, length: int, max_gap: float) -> bool:
    if length <= 1:
        return True
    for index in range(start_index + 1, start_index + length):
        if _word_gap_before(words, index) > max_gap:
            return False
    return True


def _phrase_duration(words, start_index: int, length: int) -> float:
    return max(0.0, words[start_index + length - 1].end - words[start_index].start)


def _is_protected_retake_phrase(phrase: tuple[str, ...]) -> bool:
    if not phrase:
        return True
    if all(token in RETAKE_PROTECTED_REPEATS for token in phrase):
        return True
    if len(phrase) == 1 and phrase[0] in RETAKE_PROTECTED_REPEATS:
        return True
    return False


def _safe_retake_phrase(words, normalized_words: list[str], index: int) -> tuple[int, int, str] | None:
    max_length = min(RETAKE_MAX_PHRASE_WORDS, index, len(words) - index)
    for length in range(max_length, 0, -1):
        previous_start = index - length
        current_start = index
        previous = tuple(normalized_words[previous_start:index])
        current = tuple(normalized_words[current_start : current_start + length])
        if not previous or previous != current or any(not token for token in previous):
            continue
        if _is_protected_retake_phrase(previous):
            continue
        if length > 1 and previous[0] not in RETAKE_STARTERS:
            continue
        if length == 1 and not _is_safe_duplicate_retake(words, normalized_words, index):
            continue
        if _word_gap_before(words, index) > RETAKE_PHRASE_MAX_GAP_SECONDS:
            continue
        if not _phrase_gaps_are_tight(words, previous_start, length, RETAKE_PHRASE_MAX_GAP_SECONDS):
            continue
        if not _phrase_gaps_are_tight(words, current_start, length, RETAKE_PHRASE_MAX_GAP_SECONDS):
            continue
        if _phrase_duration(words, previous_start, length) > RETAKE_PHRASE_MAX_DURATION_SECONDS:
            continue
        label = " ".join(previous)
        return previous_start, index - 1, label
    return None


def find_suggestions(transcript: TranscriptData) -> list[CutSuggestion]:
    suggestions: list[CutSuggestion] = []
    words = transcript.words
    normalized_words = [_normalize_word(word.text) for word in words]
    phrase_skip_until = -1
    retake_skip_until = -1

    for index, word in enumerate(words):
        gap = 0.0
        if index > 0:
            gap = word.start - words[index - 1].end
        text = normalized_words[index]
        if gap >= 0.8:
            cut = _guarded_silence_cut(words[index - 1].end if index > 0 else word.start, word.start)
            if cut:
                suggestions.append(
                    CutSuggestion(
                        start=cut[0],
                        end=cut[1],
                        category="silence",
                        detail=f"Long pause of {gap:.2f}s",
                    )
                )

        if index <= phrase_skip_until:
            continue

        if index <= retake_skip_until:
            continue

        retake = _safe_retake_phrase(words, normalized_words, index)
        if retake:
            retake_skip_until = index + (retake[1] - retake[0])
            suggestions.append(
                CutSuggestion(
                    start=max(0.0, words[retake[0]].start - FILLER_PADDING_SECONDS),
                    end=words[retake[1]].end + FILLER_PADDING_SECONDS,
                    category="retake",
                    detail=f"Repeated restart: {retake[2]}",
                )
            )
            continue

        phrase = _phrase_at(normalized_words, index)
        if phrase and _has_soft_filler_context(words, index, phrase[1]):
            phrase_skip_until = phrase[1]
            suggestions.append(
                CutSuggestion(
                    start=max(0.0, words[index].start - FILLER_PADDING_SECONDS),
                    end=words[phrase[1]].end + FILLER_PADDING_SECONDS,
                    category="filler",
                    detail=f"Filler phrase: {phrase[0]}",
                )
            )
            continue

        if _is_hesitation_noise(text):
            suggestions.append(
                CutSuggestion(
                    start=max(0.0, word.start - FILLER_PADDING_SECONDS),
                    end=word.end + FILLER_PADDING_SECONDS,
                    category="filler",
                    detail=f"Filler word: {word.text}",
                )
            )
            continue

        if text in SOFT_FILLERS and _has_soft_filler_context(words, index):
            suggestions.append(
                CutSuggestion(
                    start=max(0.0, word.start - FILLER_PADDING_SECONDS),
                    end=word.end + FILLER_PADDING_SECONDS,
                    category="filler",
                    detail=f"Context filler word: {word.text}",
                )
            )
            continue

    return merge_suggestions(suggestions)


def detect_audio_silences(video_path: Path, min_silence: float = 0.45, noise: str = "-30dB") -> list[CutSuggestion]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(video_path),
        "-af",
        f"silencedetect=noise={noise}:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode not in {0, 1}:
        return []

    starts: list[float] = []
    suggestions: list[CutSuggestion] = []
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
        if end_match and starts:
            start = starts.pop(0)
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            if duration >= min_silence:
                cut = _guarded_silence_cut(start, end)
                if cut:
                    suggestions.append(
                        CutSuggestion(
                            start=cut[0],
                            end=cut[1],
                            category="silence",
                            detail=f"Audio silence of {duration:.2f}s",
                        )
                    )
    return merge_suggestions(suggestions)


def merge_suggestions(suggestions: list[CutSuggestion], padding: float = 0.08) -> list[CutSuggestion]:
    if not suggestions:
        return []
    ordered = sorted(suggestions, key=lambda item: item.start)
    merged: list[CutSuggestion] = []
    current = ordered[0]
    for item in ordered[1:]:
        if item.start <= current.end + padding:
            current = CutSuggestion(
                start=current.start,
                end=max(current.end, item.end),
                category=current.category if current.category == item.category else "trim",
                detail=f"{current.detail}; {item.detail}",
            )
            continue
        merged.append(current)
        current = item
    merged.append(current)
    return merged


def build_keep_ranges(duration: float, suggestions: list[CutSuggestion], min_segment: float = 0.35) -> list[CutRange]:
    keep_ranges: list[CutRange] = []
    cursor = 0.0
    source_end = round(max(0.0, duration), 3)
    for suggestion in suggestions:
        if suggestion.start - cursor >= min_segment:
            keep_ranges.append(
                CutRange(
                    start=round(cursor, 3),
                    end=round(suggestion.start, 3),
                    reason="Keep spoken content",
                    handle_start=0.0,
                    handle_end=source_end,
                )
            )
        cursor = max(cursor, suggestion.end)
    if duration - cursor >= min_segment:
        keep_ranges.append(
            CutRange(
                start=round(cursor, 3),
                end=round(duration, 3),
                reason="Keep spoken content",
                handle_start=0.0,
                handle_end=source_end,
            )
        )
    return keep_ranges
