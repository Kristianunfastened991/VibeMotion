from __future__ import annotations

import re
import time
import uuid
from typing import Any

from app.services.motion_scenario import build_motion_qa_gate, build_selected_layer_prompt_scenario, build_whole_frame_prompt_scenario


MOTION_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "одну": 1,
    "один": 1,
    "одна": 1,
    "первую": 1,
    "первой": 1,
    "две": 2,
    "два": 2,
    "вторую": 2,
    "второй": 2,
    "три": 3,
    "третью": 3,
    "третьей": 3,
    "четыре": 4,
    "четвертую": 4,
    "четвертой": 4,
    "пять": 5,
    "пятую": 5,
    "пятой": 5,
    "шесть": 6,
    "шестую": 6,
    "шестой": 6,
    "семь": 7,
    "седьмую": 7,
    "седьмой": 7,
    "восемь": 8,
    "восьмую": 8,
    "восьмой": 8,
    "девять": 9,
    "девятую": 9,
    "девятой": 9,
    "десять": 10,
    "десятую": 10,
    "десятой": 10,
}

MOTION_NUMBER_RE = r"\d+(?:\.\d+)?|" + "|".join(re.escape(key) for key in sorted(MOTION_NUMBER_WORDS, key=len, reverse=True))
MOTION_SECOND_UNIT_RE = r"(?:сек(?:унд(?:а|у|ы|е|ой)?|\.?)?|seconds?|second|secs?|s)"


MOTION_NUMBER_WORDS.update(
    {
        "\u043e\u0434\u0438\u043d": 1,
        "\u043e\u0434\u043d\u0430": 1,
        "\u043e\u0434\u043d\u0443": 1,
        "\u0434\u0432\u0430": 2,
        "\u0434\u0432\u0435": 2,
        "\u0442\u0440\u0438": 3,
        "\u0447\u0435\u0442\u044b\u0440\u0435": 4,
        "\u043f\u044f\u0442\u044c": 5,
        "\u043f\u044f\u0442\u0438": 5,
        "\u0448\u0435\u0441\u0442\u044c": 6,
        "\u0441\u0435\u043c\u044c": 7,
        "\u0432\u043e\u0441\u0435\u043c\u044c": 8,
        "\u0434\u0435\u0432\u044f\u0442\u044c": 9,
        "\u0434\u0435\u0441\u044f\u0442\u044c": 10,
    }
)
MOTION_NUMBER_RE = r"\d+(?:[\.,]\d+)?|" + "|".join(re.escape(key) for key in sorted(MOTION_NUMBER_WORDS, key=len, reverse=True))
MOTION_SECOND_UNIT_RE = r"(?:seconds?|second|secs?|sec|s|\u0441\u0435\u043a(?:\u0443\u043d\u0434(?:\u0430|\u0443|\u044b|\u0435|\u043e\u0439)?|\.?)?)"


def normalize_motion_prompt_mode(value: str | None) -> str:
    mode = str(value or "replace").strip().casefold()
    if mode in {"append", "add", "extend", "overlay", "layer", "добавить", "добавь"}:
        return "append"
    return "replace"


def strip_stored_motion_prompt(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for prefix in ("Frame choreography prompt:", "Animation prompt:"):
        if text.casefold().startswith(prefix.casefold()):
            return text[len(prefix) :].strip()
    return text


def _motion_text(prompt: str) -> str:
    text = str(prompt or "").casefold().replace(",", ".").replace("ё", "е")
    return re.sub(r"[\u2010-\u2015_-]+", " ", text)


def _motion_number_value(raw: str) -> float | None:
    text = str(raw or "").casefold().replace(",", ".").replace("ё", "е")
    if text in MOTION_NUMBER_WORDS:
        return float(MOTION_NUMBER_WORDS[text])
    try:
        return max(0.0, float(text))
    except ValueError:
        return None


def _motion_second_mentions(prompt: str) -> list[tuple[float, int, int, str]]:
    text = _motion_text(prompt)
    pattern = rf"\b({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}\b"
    mentions: list[tuple[float, int, int, str]] = []
    for match in re.finditer(pattern, text):
        value = _motion_number_value(match.group(1))
        if value is not None:
            mentions.append((value, match.start(), match.end(), text))
    return mentions


def _seconds_from_prompt(prompt: str, fallback: float = 0.0) -> float:
    mentions = _motion_second_mentions(prompt)
    return mentions[0][0] if mentions else fallback


def _explicit_duration_seconds_from_prompt(prompt: str) -> float | None:
    mentions = _motion_second_mentions(prompt)
    if not mentions:
        return None
    for value, start, end, text in mentions:
        context = text[max(0, start - 56) : min(len(text), end + 20)]
        if re.search(r"длительн|продолжительн|в\s*течени|за\s*$|за\s+\d|duration|for|over|на\s*$|растян|длин", context):
            return value
    first_value, first_start, first_end, first_text = mentions[0]
    before = first_text[max(0, first_start - 16) : first_start]
    after = first_text[first_end : min(len(first_text), first_end + 16)]
    mention_text = first_text[first_start:first_end]
    is_russian_second_mark = bool(re.search(r"секунде\b", mention_text))
    if re.search(r"(?:^|\s)at\s*$", before) or re.search(r"^\s*(?:mark|секунде)", after) or (
        re.search(r"(?:^|\s)на\s*$", before) and is_russian_second_mark
    ):
        return None
    return first_value


def _duration_seconds_from_prompt(prompt: str, fallback: float = 0.75) -> float:
    return _explicit_duration_seconds_from_prompt(prompt) or fallback


def _relative_delay_seconds_from_prompt(prompt: str, fallback: float = 0.0) -> float:
    text = _motion_text(prompt)
    if re.search(r"(?:через\s+секунду|after\s+(?:a\s+)?second)\b", text):
        return 1.0
    pattern = rf"(?:через|after)\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}\b"
    match = re.search(pattern, text)
    if not match:
        return fallback
    return _motion_number_value(match.group(1)) or fallback


def _absolute_start_seconds_from_prompt(prompt: str) -> float | None:
    text = _motion_text(prompt)
    patterns = [
        rf"at\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*(?:seconds?|second\s*mark|sec\s*mark|s\b)",
        rf"на\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*(?:секунде|секунду|s\b)",
        rf"(?:с|from|start(?:ing)?\s+at)\s+({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _motion_number_value(match.group(1))
    return None


def _has_fade_in_intent(text: str) -> bool:
    return bool(re.search(r"fade\s*in|fadein|фейд\s*ин|фейдин|появ|прояв|из\s+прозрач|opacity\s*0\s*(?:to|->)\s*1", text))


def _has_fade_out_intent(text: str) -> bool:
    return bool(re.search(r"fade\s*out|fadeout|фейд\s*аут|фейдаут|исчез|пропад|убер|в\s+прозрач|opacity\s*1\s*(?:to|->)\s*0", text))


def _has_drop_intent(text: str) -> bool:
    return bool(re.search(r"пада|упад|паден|рух|камнем|камень|гравитац|ускор|drop|fall|stone|gravity", text))


def _has_drop_entry_intent(text: str) -> bool:
    return bool(re.search(r"drop\s+in|fall\s+in|bounce\s+in|gravity\s+in", text))


def _has_directional_layer_motion_intent(text: str) -> bool:
    return bool(
        re.search(
            r"position\s*y|from\s+below|bottom\s+to\s+top|rise|rises|slide\s+(?:up|down|left|right)|from\s+(?:top|left|right|bottom)",
            text,
        )
    )


def _has_retime_intent(text: str) -> bool:
    return bool(re.search(r"длин|длител|удлин|дольше|растян|растяг|медлен|duration|longer|slower|make.+long|make.+last|last\s+\d|instead|stretch", text))


def _has_extend_by_intent(text: str) -> bool:
    return bool(
        re.search(
            rf"(?:длиннее|удлин|дольше|растян|растяг|продли|extend|longer|add|increase)[\s\S]{{0,36}}(?:на|by|for)?\s*({MOTION_NUMBER_RE})\s*(?:x|х)?\s*{MOTION_SECOND_UNIT_RE}",
            text,
        )
        and not re.search(r"duration\s*(?:to|=)|длительность\s*(?:до|=)", text)
    )


def _has_end_anchor_intent(text: str) -> bool:
    return bool(re.search(r"\b(?:end|ending|final|last)\b|в\s*конце|вконце|последн", text))


def _motion_action_id() -> str:
    return f"act-{uuid.uuid4().hex[:10]}"


def _motion_operation_id() -> str:
    return f"op-{uuid.uuid4().hex[:10]}"


_BASIC_NUMBER_RE = r"\d+(?:[\.,]\d+)?|" + "|".join(re.escape(key) for key in sorted(MOTION_NUMBER_WORDS, key=len, reverse=True))
_BASIC_TIME_UNIT_RE = r"(?:seconds?|second|secs?|sec|s|\u0441\u0435\u043a(?:\u0443\u043d\u0434(?:\u0430|\u0443|\u044b|\u0435|\u043e\u0439)?|\.?)?)"


def _basic_text(prompt: str) -> str:
    return re.sub(r"[\u2010-\u2015_-]+", " ", str(prompt or "").casefold().replace(",", ".").replace("\u0451", "\u0435"))


def _basic_number(raw: str) -> float | None:
    token = str(raw or "").casefold().replace(",", ".").replace("\u0451", "\u0435").strip()
    if token in MOTION_NUMBER_WORDS:
        return float(MOTION_NUMBER_WORDS[token])
    try:
        return max(0.0, float(token))
    except ValueError:
        return None


def _basic_time_mentions(text: str) -> list[tuple[float, int, int, str]]:
    mentions: list[tuple[float, int, int, str]] = []
    for match in re.finditer(rf"\b({_BASIC_NUMBER_RE})\s*(?:[x\u0445]\s*)?{_BASIC_TIME_UNIT_RE}\b", text, flags=re.IGNORECASE):
        value = _basic_number(match.group(1))
        if value is not None:
            mentions.append((value, match.start(), match.end(), match.group(0)))
    return mentions


def _basic_prompt_clauses(text: str) -> list[str]:
    clean = re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()
    if not clean:
        return []
    raw = re.split(
        r"\n+|(?<!\d)[.;!?]+(?!\d)|"
        r"\b(?:then\s+add\s+this\s+motion\s+instruction|and\s+then|then|afterwards|finally)\b|"
        r"\b(?:потом|затем|после\s+этого)\b",
        clean,
        flags=re.IGNORECASE,
    )
    clauses = [item.strip(" ,;:.") for item in raw if item.strip(" ,;:.")]
    return clauses or [clean]


def _basic_duration_for_intent(text: str, marker_pattern: str, fallback: float = 1.0) -> float:
    candidates: list[tuple[float, float]] = []
    for clause in _basic_prompt_clauses(text):
        markers = list(re.finditer(marker_pattern, clause, flags=re.IGNORECASE))
        if not markers:
            continue
        mentions = _basic_time_mentions(clause)
        if not mentions:
            continue
        for marker in markers:
            marker_mid = (marker.start() + marker.end()) / 2.0
            for value, start, end, _raw in mentions:
                mention_mid = (start + end) / 2.0
                before_penalty = 14.0 if end < marker.start() else 0.0
                candidates.append((abs(mention_mid - marker_mid) + before_penalty, value))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return max(0.05, min(60.0, float(candidates[0][1])))
    mentions = _basic_time_mentions(text)
    if len(mentions) == 1 and re.search(marker_pattern, text, flags=re.IGNORECASE):
        return max(0.05, min(60.0, float(mentions[0][0])))
    return max(0.05, min(60.0, float(fallback)))


def _basic_duration_seconds(text: str, fallback: float = 1.0) -> float:
    patterns = [
        rf"(?:duration|for|over|during|lasts?|\u0437\u0430|\u043d\u0430|\u0432\s+\u0442\u0435\u0447\u0435\u043d\u0438[\u0435\u0438]|\u0434\u043b\u0438\u0442\w*)\s+({_BASIC_NUMBER_RE})\s*(?:[x\u0445]\s*)?{_BASIC_TIME_UNIT_RE}\b",
        rf"({_BASIC_NUMBER_RE})\s*(?:[x\u0445]\s*)?{_BASIC_TIME_UNIT_RE}\b[\s\S]{{0,48}}(?:fade|appear|\u0444\u0435\u0439\u0434|\u043f\u043e\u044f\u0432|\u043f\u0440\u043e\u044f\u0432)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _basic_number(match.group(1))
            if value is not None and value > 0:
                return max(0.05, min(60.0, value))
    mentions = _basic_time_mentions(text)
    if len(mentions) == 1:
        value, start, _end, raw = mentions[0]
        before = text[max(0, start - 8) : start]
        if not (re.search(r"(?:^|\s)(?:at|from|\u0432|\u0441|\u043d\u0430)\s*$", before) and re.search(r"\u0441\u0435\u043a\u0443\u043d\u0434\u0435\b", raw)):
            return max(0.05, min(60.0, value))
    return fallback


def _basic_delay_seconds(text: str) -> float:
    match = re.search(rf"(?:after|\u0447\u0435\u0440\u0435\u0437)\s+({_BASIC_NUMBER_RE})\s*(?:[x\u0445]\s*)?{_BASIC_TIME_UNIT_RE}\b", text, flags=re.IGNORECASE)
    if not match:
        return 0.0
    value = _basic_number(match.group(1))
    return max(0.0, min(60.0, float(value or 0.0)))


def _basic_absolute_start_seconds(text: str) -> float | None:
    match = re.search(
        rf"(?:\bat\b|\bfrom\b|\bstart(?:ing)?\s+at\b|\u0441|\u043d\u0430)\s+({_BASIC_NUMBER_RE})\s*(?:[x\u0445]\s*)?{_BASIC_TIME_UNIT_RE}\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = _basic_number(match.group(1))
    return max(0.0, min(60.0, float(value or 0.0))) if value is not None else None


def _basic_has_fade_in(text: str) -> bool:
    return bool(re.search(r"fades?\s*in|fading\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432|\u043f\u0440\u043e\u044f\u0432|\u0438\u0437\s+\u043f\u0440\u043e\u0437\u0440\u0430\u0447|opacity\s*0\s*(?:to|->)\s*1", text))


def _basic_has_fade_out(text: str) -> bool:
    return bool(re.search(r"fades?\s*out|fading\s*out|fadeout|disappear|vanish|hide|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u0435\u0439\u0434\u0430\u0443\u0442|\u0438\u0441\u0447\u0435\u0437|\u043f\u0440\u043e\u043f\u0430\u0434|\u0443\u0431\u0435\u0440|\u0432\s+\u043f\u0440\u043e\u0437\u0440\u0430\u0447|opacity\s*1\s*(?:to|->)\s*0", text))


def _basic_has_slide(text: str) -> bool:
    return bool(
        re.search(
            r"\bslides?\b|\bsliding\b|\bfly(?:ing|ies)?\b|fly\s+(?:in|into)|"
            r"from\s+(?:the\s+)?(?:(?:lower|upper)\s+)?(?:left|right|top|bottom)|"
            r"\u0441\u043b\u0435\u0432\u0430|\u0441\u043f\u0440\u0430\u0432\u0430|\u0441\u0432\u0435\u0440\u0445\u0443|\u0441\u043d\u0438\u0437\u0443|\u0432\u044b\u0435\u0437\u0434|\u0437\u0430\u0435\u0437\u0434|\u0441\u043b\u0430\u0439\u0434|\u0432\u043b\u0435\u0442|\u0437\u0430\u043b\u0435\u0442",
            text,
        )
    )


def _basic_has_end_anchor(text: str) -> bool:
    return bool(re.search(r"\b(?:end|ending|final|last|outro)\b|\u0432\s*\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|\u043f\u043e\u0441\u043b\u0435\u0434\u043d", text))


def _basic_direction(text: str) -> tuple[float, float, str]:
    distance = 80.0
    if re.search(r"(?:from\s+(?:the\s+)?)?(?:lower|bottom)[-\s]+left|\u0441\u043d\u0438\u0437\u0443[\s-]+\u0441\u043b\u0435\u0432\u0430", text):
        return -distance, distance, "bottom-left"
    if re.search(r"(?:from\s+(?:the\s+)?)?(?:lower|bottom)[-\s]+right|\u0441\u043d\u0438\u0437\u0443[\s-]+\u0441\u043f\u0440\u0430\u0432\u0430", text):
        return distance, distance, "bottom-right"
    if re.search(r"(?:from\s+(?:the\s+)?)?(?:upper|top)[-\s]+left|\u0441\u0432\u0435\u0440\u0445\u0443[\s-]+\u0441\u043b\u0435\u0432\u0430", text):
        return -distance, -distance, "top-left"
    if re.search(r"(?:from\s+(?:the\s+)?)?(?:upper|top)[-\s]+right|\u0441\u0432\u0435\u0440\u0445\u0443[\s-]+\u0441\u043f\u0440\u0430\u0432\u0430", text):
        return distance, -distance, "top-right"
    if re.search(r"from\s+(?:the\s+)?left|\bleft\b|\u0441\u043b\u0435\u0432\u0430", text):
        return -distance, 0.0, "left"
    if re.search(r"from\s+(?:the\s+)?right|\bright\b|\u0441\u043f\u0440\u0430\u0432\u0430", text):
        return distance, 0.0, "right"
    if re.search(r"from\s+(?:the\s+)?top|\btop\b|\u0441\u0432\u0435\u0440\u0445\u0443", text):
        return 0.0, -distance, "top"
    if re.search(r"from\s+(?:the\s+)?bottom|\bbottom\b|\u0441\u043d\u0438\u0437\u0443|\u0432\u0432\u0435\u0440\u0445", text):
        return 0.0, distance, "bottom"
    return distance, 0.0, "right"


def _basic_frame(**overrides: Any) -> dict[str, Any]:
    frame = {"time": 0, "x": 0, "y": 0, "scale": 1, "scaleX": 1, "scaleY": 1, "rotate": 0, "skewX": 0, "skewY": 0, "opacity": 1, "blur": 0, "brightness": 1}
    frame.update(overrides)
    return frame


def _basic_action(
    prompt: str,
    kind: str,
    operation_id: str,
    preset: str,
    start: float,
    duration: float,
    timeline_duration: float | None,
    text: str,
) -> dict[str, Any]:
    prompt_text = strip_stored_motion_prompt(prompt)
    start = max(0.0, float(start or 0.0))
    duration = max(0.05, min(60.0, float(duration or 1.0)))
    end = round(start + duration, 4)
    keyframes: list[dict[str, Any]]
    effects: list[dict[str, Any]] = []
    intro_type = "fade"
    direction = "center"
    distance = 0.0
    if preset == "fade-out":
        keyframes = [_basic_frame(time=0, opacity=1)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=1))
        keyframes.append(_basic_frame(time=end, opacity=0, ease="sine"))
        label = f"Fade out {duration:g}s"
    elif preset == "gravity-drop-fade":
        mid = round(start + duration * 0.45, 4)
        keyframes = [_basic_frame(time=0, opacity=1)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=1))
        if mid < end - 0.001:
            keyframes.append(_basic_frame(time=mid, opacity=0.8, y=32, rotate=1.5, ease="power"))
        keyframes.append(_basic_frame(time=end, opacity=0, y=260, rotate=7, blur=1.5, ease="sine"))
        intro_type = "drop"
        direction = "bottom"
        distance = 260.0
        label = f"Drop fade {duration:g}s"
    elif preset == "soft-slide":
        dx, dy, direction = _basic_direction(text)
        distance = 80.0
        keyframes = [_basic_frame(time=0, opacity=0, x=dx, y=dy)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=0, x=dx, y=dy))
        keyframes.append(_basic_frame(time=end, opacity=1, x=0, y=0, ease="sine"))
        intro_type = "slide"
        label = f"Slide in {duration:g}s"
    elif preset in {"pop-in", "elastic-pop"}:
        mid = round(start + duration * 0.72, 4)
        keyframes = [_basic_frame(time=0, opacity=0, scale=0.86)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=0, scale=0.86))
        if mid < end - 0.001:
            keyframes.append(_basic_frame(time=mid, opacity=1, scale=1.055, ease="power"))
        keyframes.append(_basic_frame(time=end, opacity=1, scale=1, ease="sine"))
        intro_type = "pop"
        label = f"Pop in {duration:g}s"
        if preset == "elastic-pop":
            effects.append({"type": "elastic-pop", "start": round(start, 4), "duration": duration})
    elif preset == "drop-bounce":
        mid = round(start + duration * 0.72, 4)
        keyframes = [_basic_frame(time=0, opacity=0, y=-120)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=0, y=-120))
        if mid < end - 0.001:
            keyframes.append(_basic_frame(time=mid, opacity=1, y=14, ease="power"))
        keyframes.append(_basic_frame(time=end, opacity=1, y=0, ease="sine"))
        intro_type = "drop"
        direction = "top"
        distance = 120.0
        label = f"Drop bounce {duration:g}s"
    elif preset in {"wipe-reveal", "mask-reveal"}:
        keyframes = [_basic_frame(time=0, opacity=0)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=0))
        keyframes.append(_basic_frame(time=end, opacity=1, ease="sine"))
        effects.append({"type": "wipe-reveal", "start": round(start, 4), "duration": duration, "direction": "right", "softness": 0.12})
        intro_type = "mask"
        direction = "right"
        label = f"{'Mask' if preset == 'mask-reveal' else 'Wipe'} reveal {duration:g}s"
    else:
        keyframes = [_basic_frame(time=0, opacity=0)]
        if start > 0.001:
            keyframes.append(_basic_frame(time=round(start, 4), opacity=0))
        keyframes.append(_basic_frame(time=end, opacity=1, ease="sine"))
        preset = "fade-in"
        label = f"Fade in {duration:g}s"
    return {
        "id": _motion_action_id(),
        "operation_id": operation_id,
        "prompt": prompt_text,
        "preset": preset,
        "time_mode": "absolute-action",
        "label": label,
        "tags": [kind, "basic-motion", preset],
        "intent": {"type": preset, "scope": "selected-layer", "start": start, "duration": duration, "source": "basic-parser"},
        "intro": {"type": intro_type, "direction": direction, "delay": start, "duration": duration, "distance": distance, "ease": "sine"},
        "motion_dsl": {"version": 1, "source": "basic-parser", "keyframes": keyframes, "effects": effects},
        "phase_plan": {"scope": "selected-layer", "preset": preset, "start": start, "duration": duration, "minimum_duration": end, "parser": "basic"},
    }


def _basic_layer_motion_actions(prompt: str, layer: dict, operation_id: str, timeline_duration: float | None) -> list[dict[str, Any]]:
    text = _basic_text(prompt)
    kind = str(layer.get("kind") or "layer")
    absolute_start = _basic_absolute_start_seconds(text)
    delay = absolute_start if absolute_start is not None else _basic_delay_seconds(text)
    fade_in_marker = r"(?:fades?\s*in|fading\s*in|fadein|\u0444\u0435\u0439\u0434\s*\u0438\u043d|\u0444\u0435\u0439\u0434\u0438\u043d|\u043f\u043e\u044f\u0432|\u043f\u0440\u043e\u044f\u0432|opacity\s*0\s*(?:to|->)\s*1)"
    fade_out_marker = r"(?:fades?\s*out|fading\s*out|fadeout|disappear|vanish|hide|\u0444\u0435\u0439\u0434\s*\u0430\u0443\u0442|\u0444\u0435\u0439\u0434\u0430\u0443\u0442|\u0438\u0441\u0447\u0435\u0437|\u043f\u0440\u043e\u043f\u0430\u0434|\u0443\u0431\u0435\u0440|opacity\s*1\s*(?:to|->)\s*0)"
    slide_marker = r"(?:\bslides?\b|\bsliding\b|from\s+(?:the\s+)?(?:left|right|top|bottom)|\u0441\u043b\u0430\u0439\u0434|\u0437\u0430\u0435\u0437\u0434|\u0432\u044b\u0435\u0437\u0434|\u0441\u043b\u0435\u0432\u0430|\u0441\u043f\u0440\u0430\u0432\u0430|\u0441\u0432\u0435\u0440\u0445\u0443|\u0441\u043d\u0438\u0437\u0443)"
    drop_marker = r"(?:\u043f\u0430\u0434|\u0443\u043f\u0430\u0434|\u0440\u0443\u0445|drop|fall|gravity)"
    end_marker = r"(?:\u0432\s*\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|\u043f\u043e\u0441\u043b\u0435\u0434\u043d|\b(?:end|ending|final|last|outro)\b)"
    wants_fade_in = _basic_has_fade_in(text)
    wants_fade_out = _basic_has_fade_out(text)
    wants_slide = _basic_has_slide(text)
    wants_drop = _has_drop_intent(text) and not _has_drop_entry_intent(text)
    wants_drop_entry = _has_drop_entry_intent(text) or bool(re.search(r"drop\s*(?:in|bounce)|drop-bounce|\u043f\u0430\u0434\w*\s+\u0432\s+\u043a\u0430\u0434\u0440", text))
    wants_pop = bool(re.search(r"\bpop(?:s|ping)?\b|pop-in|elastic\s+pop|elastic-pop|\u043f\u043e\u043f|\u043f\u0440\u0443\u0436\u0438\u043d", text))
    wants_mask = bool(re.search(r"mask\s+reveal|mask-reveal|\u043c\u0430\u0441\u043a", text))
    wants_wipe = bool(re.search(r"\bwipe\b|wipe\s+reveal|wipe-reveal|\u0448\u0442\u043e\u0440\u043a|\u0432\u0430\u0439\u043f", text))
    enter_duration = _basic_duration_for_intent(text, rf"{fade_in_marker}|{slide_marker}|drop\s*(?:in|bounce)|pop|wipe|mask", 1.0)
    exit_duration = _basic_duration_for_intent(text, rf"{fade_out_marker}|{drop_marker}|{end_marker}", 1.0)
    actions: list[dict[str, Any]] = []
    if wants_pop:
        actions.append(_basic_action(prompt, kind, operation_id, "elastic-pop" if "elastic" in text else "pop-in", delay, enter_duration, timeline_duration, text))
    elif wants_drop_entry:
        actions.append(_basic_action(prompt, kind, operation_id, "drop-bounce", delay, enter_duration, timeline_duration, text))
    elif wants_mask:
        actions.append(_basic_action(prompt, kind, operation_id, "mask-reveal", delay, enter_duration, timeline_duration, text))
    elif wants_wipe:
        actions.append(_basic_action(prompt, kind, operation_id, "wipe-reveal", delay, enter_duration, timeline_duration, text))
    elif wants_slide:
        actions.append(_basic_action(prompt, kind, operation_id, "soft-slide", delay, enter_duration, timeline_duration, text))
    elif wants_fade_in:
        actions.append(_basic_action(prompt, kind, operation_id, "fade-in", delay, enter_duration, timeline_duration, text))
    elif not wants_fade_out and not wants_drop:
        actions.append(_basic_action(prompt, kind, operation_id, "fade-in", delay, enter_duration, timeline_duration, text))
    if wants_drop:
        start = delay
        if _basic_has_end_anchor(text) and timeline_duration is not None:
            try:
                start = max(0.0, float(timeline_duration) - exit_duration)
            except (TypeError, ValueError):
                start = delay
        actions.append(_basic_action(prompt, kind, operation_id, "gravity-drop-fade", start, exit_duration, timeline_duration, text))
    elif wants_fade_out:
        start = delay
        if _basic_has_end_anchor(text) and timeline_duration is not None:
            try:
                start = max(0.0, float(timeline_duration) - exit_duration)
            except (TypeError, ValueError):
                start = delay
        actions.append(_basic_action(prompt, kind, operation_id, "fade-out", start, exit_duration, timeline_duration, text))
    return actions


def _motion_action_identity(action: dict, index: int) -> str:
    return str(action.get("id") or action.get("action_id") or action.get("recipe_id") or f"legacy-{index}")


def motion_recipe_actions(recipe: dict | None) -> list[dict]:
    if not isinstance(recipe, dict) or not recipe:
        return []
    actions = recipe.get("motion_actions")
    if isinstance(actions, list):
        result = [dict(action) for action in actions if isinstance(action, dict)]
    elif recipe.get("motion_dsl") or recipe.get("intro") or recipe.get("outro") or recipe.get("preset"):
        result = [dict(recipe)]
    else:
        result = []
    clean: list[dict] = []
    for index, action in enumerate(result):
        action.pop("motion_actions", None)
        action.setdefault("id", _motion_action_identity(action, index))
        clean.append(action)
    return clean


def motion_operation_history(recipe: dict | None) -> list[dict]:
    if not isinstance(recipe, dict):
        return []
    operations: list[dict] = []
    for operation in list(recipe.get("motion_operation_history") or []):
        if isinstance(operation, dict):
            operations.append(dict(operation))
    current = recipe.get("motion_operation")
    if isinstance(current, dict) and not any(item.get("id") == current.get("id") for item in operations):
        operations.append(dict(current))
    return operations[-24:]


def _motion_action_duration(action: dict) -> float:
    required = 0.0
    phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
    for key in ("minimum_duration", "duration"):
        try:
            required = max(required, float(phase_plan.get(key) or 0))
        except (TypeError, ValueError):
            pass
    intro = action.get("intro") if isinstance(action.get("intro"), dict) else {}
    try:
        required = max(required, float(intro.get("delay") or 0) + float(intro.get("duration") or 0))
    except (TypeError, ValueError):
        pass
    dsl = action.get("motion_dsl") if isinstance(action.get("motion_dsl"), dict) else {}
    for frame in list(dsl.get("keyframes") or []):
        if not isinstance(frame, dict):
            continue
        try:
            required = max(required, float(frame.get("time") or 0))
        except (TypeError, ValueError):
            pass
    return max(0.0, required)


def _motion_action_phase_start(action: dict) -> float:
    phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
    try:
        return max(0.0, float(phase_plan.get("start") or 0))
    except (TypeError, ValueError):
        return 0.0


def _motion_action_phase_duration(action: dict) -> float:
    phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
    try:
        duration = float(phase_plan.get("duration") or 0)
        if duration > 0:
            return duration
    except (TypeError, ValueError):
        pass
    return max(0.0, _motion_action_duration(action) - _motion_action_phase_start(action))


def _target_action_index_for_prompt(prompt: str, actions: list[dict]) -> int:
    if not actions:
        return -1
    text = _motion_text(prompt)
    wants_fade_in = _has_fade_in_intent(text)
    wants_fade_out = _has_fade_out_intent(text)
    wants_drop = _has_drop_intent(text)
    for index in range(len(actions) - 1, -1, -1):
        action = actions[index]
        haystack = " ".join(str(action.get(key) or "") for key in ("preset", "label", "prompt")).casefold()
        if wants_fade_in and ("fade-in" in haystack or "fade in" in haystack or "фейдин" in haystack or "фейд ин" in haystack):
            return index
        if wants_fade_out and ("fade-out" in haystack or "fade out" in haystack or "фейдаут" in haystack or "фейд аут" in haystack):
            return index
        if wants_drop and ("drop" in haystack or "fall" in haystack or "пад" in haystack):
            return index
    return len(actions) - 1


def _reference_time_for_prompt(prompt: str, actions: list[dict]) -> float:
    index = _target_action_index_for_prompt(prompt, actions)
    if index >= 0:
        return _motion_action_duration(actions[index])
    return max((_motion_action_duration(action) for action in actions), default=0.0)


def _retime_motion_action(action: dict, duration: float, operation_id: str) -> dict:
    duration = max(0.05, min(60.0, float(duration or 0)))
    next_action = dict(action)
    dsl = next_action.get("motion_dsl") if isinstance(next_action.get("motion_dsl"), dict) else {}
    keyframes = [dict(frame) for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
    phase_start = _motion_action_phase_start(action)
    previous_duration = max(0.001, _motion_action_phase_duration(action))
    if keyframes:
        for frame in keyframes:
            try:
                current = float(frame.get("time") or 0)
            except (TypeError, ValueError):
                current = phase_start
            if current + 0.0001 < phase_start:
                continue
            frame["time"] = round(phase_start + ((current - phase_start) / previous_duration) * duration, 4)
        next_action["motion_dsl"] = {**dsl, "keyframes": keyframes}
    intro = next_action.get("intro") if isinstance(next_action.get("intro"), dict) else None
    if intro is not None:
        next_action["intro"] = {**intro, "duration": duration}
    phase_plan = next_action.get("phase_plan") if isinstance(next_action.get("phase_plan"), dict) else {}
    keyframe_end = max((float(frame.get("time") or 0) for frame in keyframes), default=duration)
    next_action["phase_plan"] = {**phase_plan, "duration": duration, "minimum_duration": max(duration, keyframe_end)}
    preset = str(next_action.get("preset") or "")
    if preset == "fade-in":
        next_action["label"] = f"Fade in {duration:g}s"
    elif preset == "fade-out":
        next_action["label"] = f"Fade out {duration:g}s"
    next_action["operation_id"] = operation_id
    intent = next_action.get("intent") if isinstance(next_action.get("intent"), dict) else {}
    next_action["intent"] = {**intent, "duration": duration, "edit": "retime", "source": "deterministic-dialog"}
    return next_action


def _modify_existing_motion_action(prompt: str, actions: list[dict], operation_id: str) -> tuple[list[dict] | None, list[str], float | None]:
    if not actions:
        return None, [], None
    text = _motion_text(prompt)
    seconds = _explicit_duration_seconds_from_prompt(prompt)
    if _has_retime_intent(text) and seconds and seconds > 0:
        has_new_effect_intent = _has_fade_in_intent(text) or _has_fade_out_intent(text) or _has_drop_intent(text)
        has_explicit_retime_command = _has_extend_by_intent(text) or bool(
            re.search(r"длиннее|удлин|дольше|растян|растяг|медлен|longer|slower|stretch|duration\s*(?:to|=)|make.+last|last\s+\d|instead|длительность\s*(?:до|=)", text)
        )
        if has_new_effect_intent and not has_explicit_retime_command:
            return None, [], None
        updated = [dict(action) for action in actions]
        target_index = _target_action_index_for_prompt(prompt, updated)
        if target_index < 0:
            target_index = len(updated) - 1
        target_preset = str(updated[target_index].get("preset") or "").casefold()
        target_matches_requested_effect = (
            (not has_new_effect_intent)
            or (bool(_has_fade_in_intent(text)) and target_preset == "fade-in")
            or (bool(_has_fade_out_intent(text)) and target_preset == "fade-out")
            or (bool(_has_drop_intent(text)) and target_preset == "gravity-drop-fade")
        )
        if not target_matches_requested_effect:
            return None, [], None
        previous_duration = _motion_action_phase_duration(updated[target_index])
        final_duration = previous_duration + seconds if _has_extend_by_intent(text) else seconds
        updated[target_index] = _retime_motion_action(updated[target_index], final_duration, operation_id)
        updated[target_index]["prompt"] = strip_stored_motion_prompt(prompt)
        return updated, [str(updated[target_index].get("id") or "")], final_duration
    return None, [], None


def _compound_motion_clause_texts(prompt: str) -> list[str]:
    text = _motion_text(prompt)
    marker_pattern = (
        r"\b(?:and\s+then|then|afterwards|finally|at\s+the\s+end|in\s+the\s+end)\b"
        r"|\band\s+(?=(?:fade\s+out|fadeout|drop|fall))"
        r"|(?:\u0438\s+)?(?:\u043f\u043e\u0442\u043e\u043c|\u0437\u0430\u0442\u0435\u043c|\u043f\u043e\u0441\u043b\u0435\s+\u044d\u0442\u043e\u0433\u043e|\u0432\s+\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435)"
        r"|\u0438\s+(?=(?:fade\s+out|fadeout|\u043f\u0430\u0434|\u0443\u043f\u0430\u0434|\u0438\u0441\u0447\u0435\u0437|\u043f\u0440\u043e\u043f\u0430\u0434))"
    )
    marked = re.sub(marker_pattern, lambda match: f". {match.group(0)}", text)
    raw_clauses = re.split(
        r"[.;]+|,(?=\s*(?:and\s+then|then|afterwards|finally|at\s+the\s+end|in\s+the\s+end|\u0438\s+)?(?:\u043f\u043e\u0442\u043e\u043c|\u0437\u0430\u0442\u0435\u043c|\u043f\u043e\u0441\u043b\u0435|\u0432\s+\u043a\u043e\u043d\u0446\u0435|\u0432\u043a\u043e\u043d\u0446\u0435|fade|drop|fall))",
        marked,
    )
    clauses: list[str] = []
    for clause in raw_clauses:
        clean = re.sub(r"\s+", " ", clause).strip(" .;,\t\r\n")
        if clean and _requested_intents(clean):
            clauses.append(clean)
    if len(clauses) < 2:
        return []
    effectful = [clause for clause in clauses if _has_fade_in_intent(clause) or _has_fade_out_intent(clause) or _has_drop_intent(clause)]
    return clauses if len(effectful) >= 2 else []


def _compound_selected_layer_motion_actions(
    prompt: str,
    target_layer: dict,
    existing_actions: list[dict],
    operation_id: str,
    timeline_duration: float | None,
) -> tuple[list[dict], list[str]] | None:
    clauses = _compound_motion_clause_texts(prompt)
    if not clauses:
        return None
    built_actions: list[dict] = []
    for clause in clauses:
        reference_actions = [*existing_actions, *built_actions]
        action = _deterministic_layer_motion_action(clause, target_layer, reference_actions, operation_id, timeline_duration)
        if action is None:
            return None
        action["prompt"] = clause
        intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
        action["intent"] = {**intent, "source": "deterministic-compound-dialog"}
        built_actions.append(action)
    return built_actions, clauses


def _has_temporal_reference_to_existing_action(prompt: str, existing_actions: list[dict]) -> bool:
    if not existing_actions:
        return False
    text = _motion_text(prompt)
    effect_patterns: list[str] = []
    for action in existing_actions:
        preset = str(action.get("preset") or "").casefold()
        if preset == "fade-in":
            effect_patterns.extend([r"fade\s*in", r"fadein", r"фейд\s*ин", r"фейдин"])
        elif preset == "fade-out":
            effect_patterns.extend([r"fade\s*out", r"fadeout", r"фейд\s*аут", r"фейдаут"])
        elif "drop" in preset:
            effect_patterns.extend([r"drop", r"fall", r"пад", r"вниз"])
    if not effect_patterns:
        return False
    effect_pattern = "(?:" + "|".join(effect_patterns) + ")"
    return bool(
        re.search(rf"(?:когда|после\s+того\s+как|when|after)\s+[\s\S]{{0,80}}{effect_pattern}", text)
        or re.search(rf"{effect_pattern}[\s\S]{{0,80}}(?:реализ|законч|заверш|finish|complete)", text)
    )


def _deterministic_layer_motion_action(prompt: str, layer: dict, existing_actions: list[dict], operation_id: str, timeline_duration: float | None = None) -> dict | None:
    text = _motion_text(prompt)
    kind = str(layer.get("kind") or "layer")
    duration = _duration_seconds_from_prompt(prompt, 0.75)
    base = {"x": 0, "y": 0, "scale": 1, "scaleX": 1, "scaleY": 1, "rotate": 0, "skewX": 0, "skewY": 0, "opacity": 1, "blur": 0, "brightness": 1}
    wants_fade_out = _has_fade_out_intent(text)
    wants_fade_in = _has_fade_in_intent(text)
    wants_drop = _has_drop_intent(text)
    prompt_text = strip_stored_motion_prompt(prompt)

    if wants_fade_in and not wants_drop and not _has_directional_layer_motion_intent(text):
        end = max(0.05, duration)
        return {
            "id": _motion_action_id(),
            "operation_id": operation_id,
            "prompt": prompt_text,
            "preset": "fade-in",
            "time_mode": "absolute-action",
            "label": f"Fade in {end:g}s",
            "tags": [kind, "motion-action", "fade-in"],
            "intent": {"type": "fade-in", "scope": "selected-layer", "start": 0, "duration": end, "source": "deterministic-dialog"},
            "intro": {"type": "fade", "direction": "center", "delay": 0, "duration": end, "distance": 0, "ease": "sine"},
            "motion_dsl": {"version": 1, "keyframes": [{**base, "time": 0, "opacity": 0}, {**base, "time": end, "opacity": 1, "ease": "sine"}], "effects": []},
            "phase_plan": {"scope": "selected-layer", "preset": "fade-in", "start": 0, "duration": end, "minimum_duration": end},
        }

    if wants_fade_out and not wants_drop:
        end = max(0.05, duration)
        start = 0.0
        anchor_end = False
        if _has_end_anchor_intent(text) and timeline_duration is not None:
            try:
                start = max(0.0, float(timeline_duration) - end)
                anchor_end = True
            except (TypeError, ValueError):
                start = 0.0
        final_time = start + end
        keyframes = [{**base, "time": 0, "opacity": 1}]
        if start > 0.001:
            keyframes.append({**base, "time": round(start, 4), "opacity": 1})
        keyframes.append({**base, "time": round(final_time, 4), "opacity": 0, "ease": "sine"})
        return {
            "id": _motion_action_id(),
            "operation_id": operation_id,
            "prompt": prompt_text,
            "preset": "fade-out",
            "time_mode": "absolute-action",
            "label": "Fade out at end" if anchor_end else f"Fade out {end:g}s" if start <= 0.001 else f"Fade out at {start:g}s",
            "tags": [kind, "motion-action", "fade-out"],
            "intent": {"type": "fade-out", "scope": "selected-layer", "start": start, "duration": end, "anchor": "end" if anchor_end else "", "source": "deterministic-dialog"},
            "intro": {"type": "fade", "direction": "center", "delay": start, "duration": end, "distance": 0, "ease": "sine"},
            "motion_dsl": {"version": 1, "keyframes": keyframes, "effects": []},
            "phase_plan": {"scope": "selected-layer", "preset": "fade-out", "start": start, "duration": end, "minimum_duration": final_time, "anchor": "end" if anchor_end else ""},
        }

    if wants_drop and _has_drop_entry_intent(text) and not wants_fade_out and not _has_end_anchor_intent(text):
        end = max(0.05, duration)
        return {
            "id": _motion_action_id(),
            "operation_id": operation_id,
            "prompt": prompt_text,
            "preset": "drop-bounce",
            "time_mode": "absolute-action",
            "label": f"Drop bounce {end:g}s",
            "tags": [kind, "motion-action", "drop-bounce", "dynamic"],
            "intent": {"type": "drop-bounce", "scope": "selected-layer", "start": 0, "duration": end, "source": "deterministic-dialog"},
            "motion_dsl": {
                "version": 1,
                "keyframes": [
                    {**base, "time": 0, "y": -180, "opacity": 0},
                    {**base, "time": round(end * 0.72, 4), "y": 8, "opacity": 1, "ease": "gravity"},
                    {**base, "time": round(end * 0.86, 4), "y": -3, "opacity": 1, "ease": "sine"},
                    {**base, "time": end, "y": 0, "opacity": 1, "ease": "smooth"},
                ],
                "effects": [],
            },
            "phase_plan": {"scope": "selected-layer", "preset": "drop-bounce", "start": 0, "duration": end, "minimum_duration": end},
        }

    if wants_drop:
        explicit_start = _absolute_start_seconds_from_prompt(prompt)
        anchor_end = False
        if explicit_start is not None:
            delay = max(0.0, explicit_start)
        elif _has_end_anchor_intent(text) and timeline_duration is not None:
            fall_duration = 1.15
            try:
                delay = max(0.0, float(timeline_duration) - fall_duration)
                anchor_end = True
            except (TypeError, ValueError):
                delay = 0.0
        else:
            relative_delay = _relative_delay_seconds_from_prompt(prompt, 0.0)
            after_existing = _reference_time_for_prompt(prompt, list(existing_actions or []))
            delay = after_existing + relative_delay if re.search(r"после|after|then|когда|потом|затем", text) else relative_delay
        fall_duration = 1.15
        end = delay + fall_duration
        return {
            "id": _motion_action_id(),
            "operation_id": operation_id,
            "prompt": prompt_text,
            "preset": "gravity-drop-fade",
            "time_mode": "absolute-action",
            "label": "Gravity drop at end" if anchor_end else f"Gravity drop at {delay:g}s",
            "tags": [kind, "motion-action", "gravity-drop-fade", "dynamic"],
            "intent": {"type": "gravity-drop-fade", "scope": "selected-layer", "start": delay, "duration": fall_duration, "anchor": "end" if anchor_end else "", "source": "deterministic-dialog"},
            "motion_dsl": {
                "version": 1,
                "keyframes": [
                    {**base, "time": 0},
                    {**base, "time": round(delay, 4)},
                    {**base, "time": round(delay + fall_duration * 0.32, 4), "y": 32, "rotate": 1.5, "opacity": 1, "ease": "gravity"},
                    {**base, "time": round(end, 4), "y": 260, "rotate": 7, "opacity": 0, "blur": 1.5, "ease": "gravity"},
                ],
                "effects": [],
            },
            "phase_plan": {"scope": "selected-layer", "preset": "gravity-drop-fade", "start": delay, "duration": fall_duration, "minimum_duration": end, "anchor": "end" if anchor_end else ""},
        }
    return None


def _requested_intents(prompt: str) -> list[str]:
    text = _motion_text(prompt)
    intents: list[str] = []
    if _has_fade_in_intent(text):
        intents.append("fade-in")
    if _has_fade_out_intent(text):
        intents.append("fade-out")
    if _has_drop_intent(text):
        intents.append("gravity-drop")
    if _has_retime_intent(text):
        intents.append("retime")
    return intents


def _operation_base(prompt: str, mode: str, layer: dict, existing_actions: list[dict]) -> dict[str, Any]:
    text = _motion_text(prompt)
    explicit_duration = _explicit_duration_seconds_from_prompt(prompt)
    return {
        "id": _motion_operation_id(),
        "scope": "selected-layer",
        "mode": mode,
        "type": "append" if mode == "append" else "replace",
        "prompt": strip_stored_motion_prompt(prompt),
        "target_layer_id": str(layer.get("id") or ""),
        "target_layer_name": str(layer.get("name") or layer.get("id") or ""),
        "target_layer_kind": str(layer.get("kind") or "layer"),
        "previous_action_ids": [str(action.get("id") or "") for action in existing_actions if action.get("id")],
        "created_action_ids": [],
        "updated_action_ids": [],
        "removed_action_ids": [],
        "requested": {
            "intents": _requested_intents(prompt),
            "duration_seconds": explicit_duration,
            "duration_mode": "add" if explicit_duration and _has_extend_by_intent(text) else "set" if explicit_duration else None,
            "final_duration_seconds": None,
            "first_seconds": _seconds_from_prompt(prompt, 0.0) if _motion_second_mentions(prompt) else None,
            "absolute_start_seconds": _absolute_start_seconds_from_prompt(prompt),
            "relative_delay_seconds": _relative_delay_seconds_from_prompt(prompt, 0.0),
        },
        "created_at_ms": int(time.time() * 1000),
    }


def _motion_recipe_from_actions(actions: list[dict], operation: dict[str, Any], history: list[dict] | None = None) -> dict | None:
    clean_actions: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        clean = dict(action)
        clean.pop("motion_actions", None)
        clean["id"] = str(clean.get("id") or _motion_action_id())
        clean_actions.append(clean)
    if not clean_actions:
        return None
    latest = clean_actions[-1]
    labels = [str(action.get("label") or action.get("preset") or action.get("prompt") or "motion").strip() for action in clean_actions]
    tags: list[str] = []
    for action in clean_actions:
        for tag in list(action.get("tags") or []):
            text = str(tag or "").strip()
            if text and text not in tags:
                tags.append(text)
    if "motion-stack" not in tags:
        tags.append("motion-stack")
    required = max((_motion_action_duration(action) for action in clean_actions), default=0.0)
    prompt_scenario = build_selected_layer_prompt_scenario(operation["prompt"], operation, clean_actions, required)
    recipe = {
        "id": f"recipe-stack-{uuid.uuid4().hex[:10]}",
        "prompt": operation["prompt"],
        "preset": latest.get("preset") if len(clean_actions) == 1 else "motion-stack",
        "time_mode": "action-stack",
        "label": labels[-1] if len(clean_actions) == 1 else f"{len(clean_actions)} motion actions",
        "tags": tags,
        "motion_actions": clean_actions,
        "motion_operation": operation,
        "motion_operation_history": [*(history or []), operation][-24:],
        "prompt_scenario": prompt_scenario,
        "dsl_contract": _dsl_contract("selected-layer"),
        "phase_plan": {
            "scope": "selected-layer",
            "mode": "action-stack",
            "operation_id": operation["id"],
            "action_count": len(clean_actions),
            "minimum_duration": required,
            "prompt_scenario": prompt_scenario,
            "actions": [
                {
                    "id": action.get("id"),
                    "label": action.get("label") or action.get("preset"),
                    "preset": action.get("preset"),
                    "duration": _motion_action_duration(action),
                    "intent": (action.get("intent") or {}).get("type") if isinstance(action.get("intent"), dict) else None,
                }
                for action in clean_actions
            ],
        },
    }
    for key in ("intro", "hold", "outro", "motion_dsl", "transform_reference"):
        if key in latest:
            recipe[key] = latest[key]
    return recipe


def _attach_motion_recipe_qa(recipe: dict, operation: dict[str, Any]) -> dict:
    recipe, repair_report = _auto_repair_motion_recipe(recipe, operation)
    qa = _motion_recipe_qa(recipe, operation)
    recipe["visual_qa"] = qa
    recipe["auto_repair"] = repair_report
    scenario = recipe.get("prompt_scenario") if isinstance(recipe.get("prompt_scenario"), dict) else None
    if scenario:
        scenario = dict(scenario)
        scenario["motion_qa_gate"] = build_motion_qa_gate(scenario, qa, recipe.get("dsl_contract"), repair_report)
        recipe["prompt_scenario"] = scenario
    recipe["phase_plan"] = {
        **(recipe.get("phase_plan") or {}),
        "auto_repair": repair_report,
        "qa_status": qa["status"],
        "qa_sample_times": qa["sample_times"],
        **({"prompt_scenario": scenario, "qa_gate": scenario.get("motion_qa_gate")} if scenario else {}),
    }
    return recipe


def _motion_recipe_qa(recipe: dict | None, operation: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    actions = motion_recipe_actions(recipe)
    if not actions:
        errors.append("no motion actions")
    requested = operation.get("requested") if isinstance(operation.get("requested"), dict) else {}
    requested_duration = requested.get("final_duration_seconds") or requested.get("duration_seconds")
    affected_action_ids = {
        str(action_id)
        for action_id in [
            *list(operation.get("created_action_ids") or []),
            *list(operation.get("updated_action_ids") or []),
        ]
        if action_id
    }
    for action in actions:
        action_id = str(action.get("id") or "")
        dsl = action.get("motion_dsl") if isinstance(action.get("motion_dsl"), dict) else {}
        keyframes = [frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        if not keyframes:
            errors.append(f"{action_id}: missing motion_dsl.keyframes")
            continue
        times: list[float] = []
        for frame in keyframes:
            try:
                times.append(float(frame.get("time") or 0))
            except (TypeError, ValueError):
                errors.append(f"{action_id}: non-numeric keyframe time")
        if any(value < -0.001 for value in times):
            errors.append(f"{action_id}: negative keyframe time")
        if times != sorted(times):
            errors.append(f"{action_id}: non-monotonic keyframes")
        phase_plan = action.get("phase_plan") if isinstance(action.get("phase_plan"), dict) else {}
        if not phase_plan:
            warnings.append(f"{action_id}: missing phase_plan")
        action_duration = _motion_action_duration(action)
        keyframe_end = max(times, default=0.0)
        if action_duration + 0.025 < keyframe_end:
            errors.append(f"{action_id}: phase duration shorter than DSL end")
        if requested_duration and action.get("preset") in {"fade-in", "fade-out"} and (
            not affected_action_ids or action_id in affected_action_ids or action.get("operation_id") == operation.get("id")
        ):
            try:
                planned_duration = float(phase_plan.get("duration") or 0)
            except (TypeError, ValueError):
                planned_duration = 0.0
            if abs(planned_duration - float(requested_duration)) > 0.025:
                errors.append(f"{action_id}: requested duration {requested_duration:g}s not preserved")
        intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
        intent_duration = intent.get("duration")
        if intent_duration and action.get("operation_id") == operation.get("id"):
            try:
                planned_duration = float(phase_plan.get("duration") or 0)
                expected_duration = float(intent_duration)
            except (TypeError, ValueError):
                planned_duration = expected_duration = 0.0
            if expected_duration > 0 and abs(planned_duration - expected_duration) > 0.025:
                errors.append(f"{action_id}: action intent duration {expected_duration:g}s not preserved")
    sample_times = sorted({0.0, *[round(_motion_action_duration(action), 3) for action in actions]})
    return {
        "status": "fail" if errors else "pass",
        "checks": {
            "actions_have_dsl": not any("missing motion_dsl" in item for item in errors),
            "keyframes_monotonic": not any("non-monotonic" in item for item in errors),
            "requested_timing_preserved": not any("requested duration" in item for item in errors),
            "preview_render_contract": "motion_dsl",
        },
        "sample_times": sample_times,
        "errors": errors,
        "warnings": warnings,
    }


def _safe_motion_float(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if result != result:
        return fallback
    return result


def _motion_base_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "opacity": 1,
        "x": 0,
        "y": 0,
        "scale": 1,
        "rotate": 0,
        "blur": 0,
    }
    state.update(overrides)
    return state


def _repair_item(kind: str, target: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "target": str(target or "motion"), "detail": detail}


def _requested_action_duration_for_repair(action: dict, operation: dict[str, Any]) -> float | None:
    requested = operation.get("requested") if isinstance(operation.get("requested"), dict) else {}
    raw_duration = requested.get("final_duration_seconds") or requested.get("duration_seconds")
    intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
    if intent.get("duration") and action.get("operation_id") == operation.get("id"):
        raw_duration = intent.get("duration")
    if not raw_duration:
        return None
    duration = _safe_motion_float(raw_duration, 0.0)
    if duration <= 0:
        return None
    action_id = str(action.get("id") or "")
    affected_action_ids = {
        str(action_id)
        for action_id in [
            *list(operation.get("created_action_ids") or []),
            *list(operation.get("updated_action_ids") or []),
        ]
        if action_id
    }
    preset = str(action.get("preset") or "")
    current_operation = action.get("operation_id") == operation.get("id")
    if intent.get("duration") and current_operation:
        return duration
    if preset in {"fade-in", "fade-out"} and (not affected_action_ids or action_id in affected_action_ids or current_operation):
        return duration
    return None


def _fallback_motion_dsl(action: dict, start: float, duration: float) -> dict[str, Any]:
    start = max(0.0, start)
    duration = max(0.05, duration)
    end = round(start + duration, 4)
    intent = action.get("intent") if isinstance(action.get("intent"), dict) else {}
    preset = str(action.get("preset") or intent.get("type") or "").strip()
    if preset == "fade-in":
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=0)},
            {"time": end, **_motion_base_state(opacity=1)},
        ]
        easing = "ease-out"
    elif preset == "fade-out":
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=1)},
            {"time": end, **_motion_base_state(opacity=0)},
        ]
        easing = "ease-in"
    elif preset in {"gravity-drop-fade", "drop", "fall", "scatter-fall"}:
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=1)},
            {"time": round(start + duration * 0.45, 4), **_motion_base_state(opacity=0.92, y=120, rotate=3)},
            {"time": end, **_motion_base_state(opacity=0, y=620, rotate=10)},
        ]
        easing = "ease-in"
    elif preset in {"parallax-photo-reveal", "photo-parallax", "parallax"}:
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=0, x=-18, y=12, scale=1.03)},
            {"time": end, **_motion_base_state(opacity=1, x=0, y=0, scale=1)},
        ]
        easing = "ease-out"
    elif preset in {"fade-up-lines", "text-lines-up", "line-fade-up"}:
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=0, y=22)},
            {"time": end, **_motion_base_state(opacity=1, y=0)},
        ]
        easing = "ease-out"
    elif preset in {"button-y-rise-fade", "rise-fade", "slide-up-fade"}:
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=0, y=28)},
            {"time": end, **_motion_base_state(opacity=1, y=0)},
        ]
        easing = "ease-out"
    else:
        keyframes = [
            {"time": round(start, 4), **_motion_base_state(opacity=0, y=16, scale=0.985)},
            {"time": end, **_motion_base_state(opacity=1, y=0, scale=1)},
        ]
        easing = "ease-out"
    return {
        "version": 1,
        "engine": "vibemotion-motion-dsl",
        "source": "auto-repair",
        "preset": preset or "motion",
        "easing": easing,
        "keyframes": keyframes,
        "effects": [],
    }


def _normalize_motion_keyframes(keyframes: list[dict], target: str) -> tuple[list[dict], list[dict[str, str]]]:
    repairs: list[dict[str, str]] = []
    normalized: list[dict] = []
    original_times: list[float] = []
    changed_time = False
    for frame in keyframes:
        next_frame = dict(frame)
        raw_time = next_frame.get("time")
        parsed = _safe_motion_float(raw_time, 0.0)
        if raw_time is None or str(raw_time) == "" or (not isinstance(raw_time, (int, float)) and parsed == 0.0):
            changed_time = True
        if parsed < 0:
            parsed = 0.0
            changed_time = True
        original_times.append(parsed)
        next_frame["time"] = round(parsed, 4)
        normalized.append(next_frame)
    if changed_time:
        repairs.append(_repair_item("keyframes", target, "normalized invalid or negative keyframe times"))
    if original_times != sorted(original_times):
        normalized.sort(key=lambda frame: _safe_motion_float(frame.get("time"), 0.0))
        repairs.append(_repair_item("keyframes", target, "sorted non-monotonic keyframes"))
    return normalized, repairs


def _auto_repair_motion_action(action: dict, operation: dict[str, Any]) -> tuple[dict, list[dict[str, str]]]:
    repairs: list[dict[str, str]] = []
    next_action = dict(action)
    if not next_action.get("id"):
        next_action["id"] = _motion_action_id()
        repairs.append(_repair_item("action-id", str(next_action["id"]), "created missing action id"))
    target = str(next_action.get("id") or "motion")
    intent = next_action.get("intent") if isinstance(next_action.get("intent"), dict) else {}
    preset = str(next_action.get("preset") or intent.get("type") or "motion").strip() or "motion"
    next_action.setdefault("preset", preset)
    if not str(next_action.get("label") or "").strip():
        next_action["label"] = preset.replace("-", " ").title()
        repairs.append(_repair_item("action-label", target, "created missing action label"))

    phase_plan = dict(next_action.get("phase_plan") or {}) if isinstance(next_action.get("phase_plan"), dict) else {}
    intro = next_action.get("intro") if isinstance(next_action.get("intro"), dict) else {}
    start = max(0.0, _safe_motion_float(phase_plan.get("start"), _safe_motion_float(intro.get("delay"), 0.0)))
    requested_duration = _requested_action_duration_for_repair(next_action, operation)
    phase_duration = _safe_motion_float(phase_plan.get("duration"), 0.0)
    current_duration = max(0.0, _motion_action_phase_duration(next_action))
    if phase_duration <= 0:
        phase_duration = requested_duration or current_duration or _safe_motion_float(intro.get("duration"), 0.0) or 0.75
        repairs.append(_repair_item("phase-plan", target, "created missing action duration"))

    dsl = dict(next_action.get("motion_dsl") or {}) if isinstance(next_action.get("motion_dsl"), dict) else {}
    keyframes = [dict(frame) for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
    generated_dsl = False
    if not keyframes:
        fallback_duration = requested_duration or phase_duration or current_duration or 0.75
        dsl = _fallback_motion_dsl(next_action, start, fallback_duration)
        keyframes = [dict(frame) for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        phase_duration = fallback_duration
        generated_dsl = True
        repairs.append(_repair_item("motion-dsl", target, "synthesized deterministic keyframes"))
    else:
        keyframes, keyframe_repairs = _normalize_motion_keyframes(keyframes, target)
        repairs.extend(keyframe_repairs)
        dsl = {**dsl, "keyframes": keyframes}
    next_action["motion_dsl"] = dsl

    keyframe_end = max((_safe_motion_float(frame.get("time"), 0.0) for frame in keyframes), default=start + phase_duration)
    if start + phase_duration + 0.025 < keyframe_end:
        phase_duration = max(0.05, keyframe_end - start)
        repairs.append(_repair_item("phase-plan", target, "extended phase duration to cover DSL"))
    next_action["phase_plan"] = {
        **phase_plan,
        "scope": phase_plan.get("scope") or "selected-layer",
        "preset": phase_plan.get("preset") or preset,
        "start": round(start, 4),
        "duration": round(max(0.05, phase_duration), 4),
        "minimum_duration": round(max(start + max(0.05, phase_duration), keyframe_end), 4),
    }

    if requested_duration and not generated_dsl:
        planned_duration = _motion_action_phase_duration(next_action)
        if abs(planned_duration - requested_duration) > 0.025:
            next_action = _retime_motion_action(next_action, requested_duration, str(operation.get("id") or "auto-repair"))
            repairs.append(_repair_item("timing", target, f"retimed action to requested {requested_duration:g}s"))
    return next_action, repairs


def _auto_repair_report(actions: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "attempted": bool(actions),
        "status": "repaired" if actions else "not-needed",
        "actions": actions[:24],
    }


def _auto_repair_motion_recipe(recipe: dict | None, operation: dict[str, Any]) -> tuple[dict, dict[str, Any]]:
    if not isinstance(recipe, dict):
        return {}, _auto_repair_report([_repair_item("recipe", "motion", "created empty repair target")])
    next_recipe = dict(recipe)
    repaired_actions: list[dict] = []
    repairs: list[dict[str, str]] = []
    for action in motion_recipe_actions(next_recipe):
        repaired, action_repairs = _auto_repair_motion_action(action, operation)
        repaired_actions.append(repaired)
        repairs.extend(action_repairs)
    if repaired_actions:
        next_recipe["motion_actions"] = repaired_actions
        latest = repaired_actions[-1]
        for key in ("intro", "hold", "outro", "motion_dsl", "transform_reference"):
            if key in latest:
                next_recipe[key] = latest[key]
        required = max((_motion_action_duration(action) for action in repaired_actions), default=0.0)
        prompt_text = str(operation.get("prompt") or next_recipe.get("prompt") or "")
        scenario = build_selected_layer_prompt_scenario(prompt_text, operation, repaired_actions, required)
        next_recipe["prompt_scenario"] = scenario
        phase_plan = dict(next_recipe.get("phase_plan") or {}) if isinstance(next_recipe.get("phase_plan"), dict) else {}
        next_recipe["phase_plan"] = {
            **phase_plan,
            "scope": phase_plan.get("scope") or "selected-layer",
            "mode": phase_plan.get("mode") or "action-stack",
            "operation_id": operation.get("id") or phase_plan.get("operation_id"),
            "action_count": len(repaired_actions),
            "minimum_duration": required,
            "prompt_scenario": scenario,
            "actions": [
                {
                    "id": action.get("id"),
                    "label": action.get("label") or action.get("preset"),
                    "preset": action.get("preset"),
                    "duration": _motion_action_duration(action),
                    "intent": (action.get("intent") or {}).get("type") if isinstance(action.get("intent"), dict) else None,
                }
                for action in repaired_actions
            ],
        }
    return next_recipe, _auto_repair_report(repairs)


def build_layer_motion_recipe_from_prompt(prompt: str, mode: str, target_layer: dict, all_layers: list[dict], timeline_duration: float | None = None) -> dict:
    normalized_mode = normalize_motion_prompt_mode(mode)
    existing_recipe = target_layer.get("motion_recipe") if isinstance(target_layer.get("motion_recipe"), dict) else {}
    existing_actions = motion_recipe_actions(existing_recipe)
    existing_history = [] if normalized_mode == "replace" else motion_operation_history(existing_recipe)
    operation = _operation_base(prompt, normalized_mode, target_layer, existing_actions)

    basic_actions = _basic_layer_motion_actions(prompt, target_layer, operation["id"], timeline_duration)
    if basic_actions:
        operation["type"] = "append" if normalized_mode == "append" else "replace"
        operation["created_action_ids"] = [str(action.get("id") or "") for action in basic_actions]
        operation["requested"]["intents"] = [
            str((action.get("intent") or {}).get("type") or action.get("preset") or "")
            for action in basic_actions
            if isinstance(action, dict)
        ]
        durations = [
            float((action.get("phase_plan") or {}).get("duration") or 0)
            for action in basic_actions
            if isinstance(action.get("phase_plan"), dict)
        ]
        operation["requested"]["duration_seconds"] = max(durations) if durations else None
        if normalized_mode == "append":
            updated_actions = [*existing_actions, *basic_actions]
        else:
            operation["removed_action_ids"] = [str(action.get("id") or "") for action in existing_actions if action.get("id")]
            updated_actions = basic_actions
        recipe = _motion_recipe_from_actions(updated_actions, operation, existing_history)
        if recipe is None:
            raise ValueError("Could not build motion recipe")
        return _attach_motion_recipe_qa(recipe, operation)

    updated_actions: list[dict] | None = None
    if normalized_mode == "append":
        updated_actions, updated_ids, final_duration = _modify_existing_motion_action(prompt, existing_actions, operation["id"])
        if updated_actions is not None:
            operation["type"] = "modify"
            operation["updated_action_ids"] = [item for item in updated_ids if item]
            if final_duration is not None:
                operation["requested"]["final_duration_seconds"] = final_duration

    if updated_actions is None:
        action = None
        compound = None
        if not _has_temporal_reference_to_existing_action(prompt, existing_actions):
            compound = _compound_selected_layer_motion_actions(prompt, target_layer, existing_actions, operation["id"], timeline_duration)
        if compound is not None:
            created_actions, clauses = compound
            operation["created_action_ids"] = [str(action.get("id") or "") for action in created_actions]
            operation["requested"]["compound"] = True
            operation["requested"]["clauses"] = clauses
            operation["requested"]["duration_seconds"] = None
            if normalized_mode == "append":
                updated_actions = [*existing_actions, *created_actions]
            else:
                operation["removed_action_ids"] = [str(action.get("id") or "") for action in existing_actions if action.get("id")]
                updated_actions = created_actions
        else:
            action = _deterministic_layer_motion_action(prompt, target_layer, existing_actions, operation["id"], timeline_duration)
        if updated_actions is None and action is None:
            # Keep unsupported layer prompts predictable. The old fallback could
            # invent complex effects; the minimal product should prefer a
            # visible, timing-preserving result over a surprising one.
            duration = _basic_duration_seconds(_basic_text(prompt), 1.0)
            action = _basic_action(
                prompt,
                str(target_layer.get("kind") or "layer"),
                operation["id"],
                "fade-in",
                0.0,
                duration,
                timeline_duration,
                _basic_text(prompt),
            )
            action["intent"] = {**(action.get("intent") or {}), "source": "basic-default"}
        if updated_actions is None:
            operation["created_action_ids"] = [str(action.get("id") or "")]
            if normalized_mode == "append":
                updated_actions = [*existing_actions, action]
            else:
                operation["removed_action_ids"] = [str(action.get("id") or "") for action in existing_actions if action.get("id")]
                updated_actions = [action]

    recipe = _motion_recipe_from_actions(updated_actions, operation, existing_history)
    if recipe is None:
        raise ValueError("Could not build motion recipe")
    return _attach_motion_recipe_qa(recipe, operation)


def build_layer_motion_recipe_after_delete(existing_recipe: dict | None, action_id: str, target_layer: dict) -> tuple[dict | None, dict[str, Any], bool]:
    actions = motion_recipe_actions(existing_recipe)
    operation = {
        "id": _motion_operation_id(),
        "scope": "selected-layer",
        "mode": "delete",
        "type": "delete",
        "prompt": "",
        "target_layer_id": str(target_layer.get("id") or ""),
        "target_layer_name": str(target_layer.get("name") or target_layer.get("id") or ""),
        "target_layer_kind": str(target_layer.get("kind") or "layer"),
        "previous_action_ids": [str(action.get("id") or "") for action in actions if action.get("id")],
        "created_action_ids": [],
        "updated_action_ids": [],
        "removed_action_ids": [],
        "requested": {"intents": ["delete-action"], "duration_seconds": None, "first_seconds": None, "absolute_start_seconds": None, "relative_delay_seconds": 0.0},
        "created_at_ms": int(time.time() * 1000),
    }
    kept_actions: list[dict] = []
    found = False
    for index, action in enumerate(actions):
        identity = _motion_action_identity(action, index)
        if identity == str(action_id):
            operation["removed_action_ids"].append(identity)
            found = True
            continue
        kept_actions.append(action)
    if not found:
        return existing_recipe if isinstance(existing_recipe, dict) else None, operation, False
    history = motion_operation_history(existing_recipe)
    recipe = _motion_recipe_from_actions(kept_actions, operation, history)
    if recipe is None:
        return None, operation, True
    return _attach_motion_recipe_qa(recipe, operation), operation, True


def _dsl_contract(scope: str) -> dict[str, Any]:
    return {
        "version": 1,
        "scope": scope,
        "source_of_truth": "motion_dsl",
        "preview_engine": "app/static/index.html::motionRecipeState",
        "render_engine": "app/services/motion.py::_motion_dsl_state",
    }


def _frame_motion_visual_qa(layers: list[dict], phase_plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    phases = [phase for phase in list(phase_plan.get("phases") or []) if isinstance(phase, dict)]
    if not phases:
        errors.append("missing phase plan")
    previous_start = -0.001
    for phase in phases:
        try:
            start = float(phase.get("start") or 0)
            duration = float(phase.get("duration") or 0)
        except (TypeError, ValueError):
            errors.append(f"{phase.get('id') or 'phase'}: non-numeric timing")
            continue
        if start + 0.001 < previous_start:
            errors.append(f"{phase.get('id') or 'phase'}: non-monotonic start")
        if duration < -0.001:
            errors.append(f"{phase.get('id') or 'phase'}: negative duration")
        previous_start = start
    recipe_count = 0
    dsl_count = 0
    recipe_ids: list[str] = []
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("visible") is False:
            continue
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if not recipe:
            continue
        recipe_count += 1
        recipe_id = str(recipe.get("id") or "").strip()
        if recipe_id:
            recipe_ids.append(recipe_id)
        dsl = recipe.get("motion_dsl") if isinstance(recipe.get("motion_dsl"), dict) else {}
        keyframes = [frame for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
        if keyframes:
            dsl_count += 1
        else:
            errors.append(f"{layer.get('id') or 'layer'}: missing motion_dsl.keyframes")
        times: list[float] = []
        for frame in keyframes:
            try:
                times.append(float(frame.get("time") or 0))
            except (TypeError, ValueError):
                errors.append(f"{layer.get('id') or 'layer'}: non-numeric keyframe time")
        if times != sorted(times):
            errors.append(f"{layer.get('id') or 'layer'}: non-monotonic keyframes")
    if recipe_count <= 0:
        errors.append("no animated recipes")
    elif dsl_count < recipe_count:
        warnings.append("some recipes have no DSL")
    duplicate_ids = sorted({recipe_id for recipe_id in recipe_ids if recipe_ids.count(recipe_id) > 1})
    if duplicate_ids:
        errors.append(f"duplicate motion recipe ids: {', '.join(duplicate_ids[:8])}")
    return {
        "status": "fail" if errors else "pass",
        "checks": {
            "phase_plan_present": bool(phases),
            "phase_timings_monotonic": not any("non-monotonic start" in item for item in errors),
            "actions_have_dsl": dsl_count == recipe_count and recipe_count > 0,
            "preview_render_contract": "motion_dsl",
        },
        "sample_times": list((phase_plan.get("acceptance") or {}).get("sample_times") or []),
        "errors": errors,
        "warnings": warnings,
    }


def _auto_repair_frame_motion(layers: list[dict], phase_plan: dict[str, Any]) -> tuple[list[dict], dict[str, Any], dict[str, Any]]:
    repairs: list[dict[str, str]] = []
    plan = dict(phase_plan or {})
    phases: list[dict] = []
    for phase in [phase for phase in list(plan.get("phases") or []) if isinstance(phase, dict)]:
        next_phase = dict(phase)
        target = str(next_phase.get("id") or next_phase.get("preset") or "phase")
        raw_start = next_phase.get("start")
        raw_duration = next_phase.get("duration")
        start = max(0.0, _safe_motion_float(raw_start, 0.0))
        duration = max(0.0, _safe_motion_float(raw_duration, 0.0))
        if raw_start != start:
            repairs.append(_repair_item("phase-plan", target, "normalized phase start"))
        if raw_duration != duration:
            repairs.append(_repair_item("phase-plan", target, "normalized phase duration"))
        next_phase["start"] = round(start, 4)
        next_phase["duration"] = round(duration, 4)
        next_phase["end"] = round(start + duration, 4)
        phases.append(next_phase)
    sorted_phases = sorted(phases, key=lambda phase: _safe_motion_float(phase.get("start"), 0.0))
    if [phase.get("id") for phase in phases] != [phase.get("id") for phase in sorted_phases]:
        repairs.append(_repair_item("phase-plan", "whole-frame", "sorted non-monotonic phases"))
    if phases:
        plan["phases"] = sorted_phases

    result: list[dict] = []
    seen_recipe_ids: dict[str, int] = {}
    for layer in layers:
        next_layer = dict(layer)
        recipe = next_layer.get("motion_recipe") if isinstance(next_layer.get("motion_recipe"), dict) else None
        if recipe:
            next_recipe = dict(recipe)
            layer_id = str(next_layer.get("id") or next_recipe.get("id") or "layer")
            recipe_id = str(next_recipe.get("id") or "").strip()
            if not recipe_id:
                recipe_id = f"recipe-{uuid.uuid4().hex[:10]}"
                repairs.append(_repair_item("recipe-id", layer_id, "created missing recipe id"))
            seen_count = seen_recipe_ids.get(recipe_id, 0)
            seen_recipe_ids[recipe_id] = seen_count + 1
            if seen_count:
                new_recipe_id = f"{recipe_id}-{seen_count + 1}"
                next_recipe["id"] = new_recipe_id
                repairs.append(_repair_item("recipe-id", layer_id, f"made duplicate recipe id unique: {new_recipe_id}"))
            else:
                next_recipe["id"] = recipe_id

            recipe_phase = dict(next_recipe.get("phase_plan") or {}) if isinstance(next_recipe.get("phase_plan"), dict) else {}
            start = max(0.0, _safe_motion_float(recipe_phase.get("start"), 0.0))
            duration = max(0.05, _safe_motion_float(recipe_phase.get("duration"), _safe_motion_float(recipe_phase.get("minimum_duration"), 0.75)))
            dsl = dict(next_recipe.get("motion_dsl") or {}) if isinstance(next_recipe.get("motion_dsl"), dict) else {}
            keyframes = [dict(frame) for frame in list(dsl.get("keyframes") or []) if isinstance(frame, dict)]
            if not keyframes:
                next_recipe["motion_dsl"] = _fallback_motion_dsl(next_recipe, start, duration)
                repairs.append(_repair_item("motion-dsl", layer_id, "synthesized missing recipe DSL"))
            else:
                keyframes, keyframe_repairs = _normalize_motion_keyframes(keyframes, layer_id)
                repairs.extend(keyframe_repairs)
                next_recipe["motion_dsl"] = {**dsl, "keyframes": keyframes}
            next_layer["motion_recipe"] = next_recipe
        result.append(next_layer)
    return result, plan, _auto_repair_report(repairs)


def attach_frame_motion_contract(prompt: str, motion_id: str, layers: list[dict], phase_plan: dict[str, Any] | None) -> list[dict]:
    plan = dict(phase_plan or {})
    layers, plan, repair_report = _auto_repair_frame_motion(layers, plan)
    phases = [dict(phase) for phase in list(plan.get("phases") or []) if isinstance(phase, dict)]
    operation = {
        "id": _motion_operation_id(),
        "scope": "whole-frame",
        "mode": "replace",
        "type": "replace",
        "prompt": strip_stored_motion_prompt(prompt),
        "target_motion_id": str(motion_id or ""),
        "previous_action_ids": [],
        "created_action_ids": [],
        "updated_action_ids": [],
        "removed_action_ids": [],
        "requested": {
            "intents": [str(phase.get("preset") or phase.get("id") or "") for phase in phases if phase.get("preset") or phase.get("id")],
            "phases": phases,
        },
        "created_at_ms": int(time.time() * 1000),
    }
    for layer in layers:
        recipe = layer.get("motion_recipe") if isinstance(layer.get("motion_recipe"), dict) else None
        if recipe and recipe.get("id"):
            recipe_id = str(recipe.get("id"))
            if recipe_id not in operation["created_action_ids"]:
                operation["created_action_ids"].append(recipe_id)
    qa = _frame_motion_visual_qa(layers, plan)
    contract = _dsl_contract("whole-frame")
    prompt_scenario = build_whole_frame_prompt_scenario(prompt, plan)
    prompt_scenario = dict(prompt_scenario)
    prompt_scenario["motion_qa_gate"] = build_motion_qa_gate(prompt_scenario, qa, contract, repair_report)
    enriched_plan = {
        **plan,
        "motion_operation": operation,
        "prompt_scenario": prompt_scenario,
        "dsl_contract": contract,
        "visual_qa": qa,
        "auto_repair": repair_report,
        "qa_gate": prompt_scenario["motion_qa_gate"],
        "qa_status": qa["status"],
        "qa_sample_times": qa["sample_times"],
    }
    result: list[dict] = []
    for layer in layers:
        next_layer = dict(layer)
        recipe = next_layer.get("motion_recipe") if isinstance(next_layer.get("motion_recipe"), dict) else None
        if recipe:
            next_recipe = dict(recipe)
            next_recipe["motion_operation"] = operation
            next_recipe["motion_operation_history"] = [operation]
            next_recipe["prompt_scenario"] = prompt_scenario
            next_recipe["dsl_contract"] = contract
            next_recipe["visual_qa"] = qa
            next_recipe["auto_repair"] = repair_report
            next_recipe["phase_plan"] = enriched_plan
            next_layer["motion_recipe"] = next_recipe
        result.append(next_layer)
    return result
