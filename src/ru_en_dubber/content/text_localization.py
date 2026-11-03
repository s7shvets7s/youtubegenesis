"""Strict contracts for safe, static on-screen text localization."""
from __future__ import annotations

import re
from typing import Any


CYRILLIC_RE = re.compile(r"[\u0400-\u052f]")
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
LOCALIZABLE_TEXT_ROLES = {"editorial_overlay", "title_card"}
STATIC_TEXT_CONFIDENCE = 0.90
MAX_VISIBLE_TEXT_WORDS = 12
MAX_VISIBLE_TEXT_CHARACTERS = 120


def contains_cyrillic(value: object) -> bool:
    """Return whether *value* contains at least one Cyrillic code point."""

    return bool(CYRILLIC_RE.search(str(value or "")))


def normalize_text_regions(raw: object) -> list[dict[str, Any]]:
    """Validate model regions without turning uncertain data into edit commands."""

    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        detected = str(item.get("detected_text") or "").strip()[:500]
        translated = str(item.get("translated_text") or "").strip()[:500]
        bbox = _normalized_bbox(item.get("bbox"))
        if not detected or bbox is None:
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        motion = str(item.get("motion") or "uncertain")
        if motion not in {"static", "moving", "uncertain"}:
            motion = "uncertain"
        background = str(item.get("background") or "uncertain")
        if background not in {"simple", "complex", "uncertain"}:
            background = "uncertain"
        visible_in_all_frames = item.get("visible_in_all_frames") is True
        style = _normalized_style(item.get("style"))
        is_russian = contains_cyrillic(detected)
        plausible_length = (
            len(detected) <= MAX_VISIBLE_TEXT_CHARACTERS
            and len(detected.split()) <= MAX_VISIBLE_TEXT_WORDS
        )
        localizable = bool(
            is_russian
            and plausible_length
            and translated
            and translated.casefold() != detected.casefold()
            and motion == "static"
            and background == "simple"
            and visible_in_all_frames
            and confidence >= STATIC_TEXT_CONFIDENCE
        )
        if not is_russian:
            reason = "non_cyrillic_text"
        elif not plausible_length:
            reason = "implausible_visual_text_length"
        elif not translated or translated.casefold() == detected.casefold():
            reason = "missing_translation"
        elif motion != "static":
            reason = "moving_or_uncertain_text"
        elif background != "simple":
            reason = "complex_or_uncertain_background"
        elif not visible_in_all_frames:
            reason = "text_interval_not_confirmed"
        elif confidence < STATIC_TEXT_CONFIDENCE:
            reason = "low_confidence"
        else:
            reason = "static_ready"
        normalized.append({
            "detected_text": detected,
            "translated_text": translated if is_russian else "",
            "bbox": bbox,
            "motion": motion,
            "background": background,
            "visible_in_all_frames": visible_in_all_frames,
            "confidence": round(confidence, 3),
            "style": style,
            "localizable": localizable,
            "decision_reason": reason,
        })
    return normalized


def text_cleanup_status(
    text_role: str, regions: list[dict[str, Any]]
) -> str:
    """Choose the conservative cleanup state for one analyzed shot."""

    if text_role not in LOCALIZABLE_TEXT_ROLES:
        return "not_needed"
    russian_regions = [
        item for item in regions if contains_cyrillic(item.get("detected_text"))
    ]
    if not russian_regions:
        return "not_needed"
    if all(item.get("localizable") is True for item in russian_regions):
        return "static_ready"
    return "text_cleanup_review"


def _normalized_bbox(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        width = float(raw["width"])
        height = float(raw["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(value == value for value in (x, y, width, height)):
        return None
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    if x + width > 1.0 or y + height > 1.0:
        return None
    # Reject both microscopic OCR noise and a box that would erase most of a frame.
    if width < 0.01 or height < 0.01 or width * height > 0.45:
        return None
    return {
        "x": round(x, 6),
        "y": round(y, 6),
        "width": round(width, 6),
        "height": round(height, 6),
    }


def _normalized_style(raw: object) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    alignment = str(source.get("alignment") or "center")
    if alignment not in {"left", "center", "right"}:
        alignment = "center"
    weight = str(source.get("weight") or "regular")
    if weight not in {"regular", "medium", "bold"}:
        weight = "regular"
    text_color = str(source.get("text_color") or "#FFFFFF")
    outline_color = str(source.get("outline_color") or "#000000")
    if not HEX_COLOR_RE.fullmatch(text_color):
        text_color = "#FFFFFF"
    if not HEX_COLOR_RE.fullmatch(outline_color):
        outline_color = "#000000"
    font_family = str(source.get("font_family") or "sans_serif")
    if font_family not in {"sans_serif", "condensed", "serif", "monospace"}:
        font_family = "sans_serif"
    panel_color = str(source.get("panel_color") or "")
    if not HEX_COLOR_RE.fullmatch(panel_color):
        red = int(text_color[1:3], 16)
        green = int(text_color[3:5], 16)
        blue = int(text_color[5:7], 16)
        luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
        panel_color = "#F2F2EE" if luminance < 0.45 else "#121416"
    return {
        "alignment": alignment,
        "weight": weight,
        "font_family": font_family,
        "text_color": text_color.upper(),
        "outline_color": outline_color.upper(),
        "panel_color": panel_color.upper(),
        "has_background": source.get("has_background") is True,
    }
