"""Validated, portable direction plans for local narration synthesis."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest
from ru_en_dubber.security import resolve_job_path


STYLES = frozenset({"neutral", "warm", "curious", "tense", "excited", "sad", "angry", "whisper"})
MAX_DIRECTION_BYTES = 128_000


def prepare_delivery_plan(
    manifest: Manifest, job_dir: Path, source: Path | None
) -> list[dict[str, Any]]:
    """Validate directions against the current spoken text and snapshot the complete plan."""
    if source is None and "tts_delivery_plan" in manifest.artifacts:
        source = resolve_job_path(
            job_dir, manifest.artifacts["tts_delivery_plan"], label="TTS delivery plan"
        )
    supplied: dict[int, dict[str, Any]] = {}
    if source is not None:
        source = source.expanduser().resolve()
        if not source.is_file() or source.stat().st_size > MAX_DIRECTION_BYTES:
            raise ValueError("TTS direction file is missing or exceeds 128 KB")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("TTS direction file must be UTF-8 JSON") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("TTS direction file must use version 1")
        entries = payload.get("segments")
        if not isinstance(entries, list) or len(entries) > len(manifest.segments):
            raise ValueError("TTS direction file has an invalid segment list")
        by_id = {segment.id: segment for segment in manifest.segments}
        for entry in entries:
            if not isinstance(entry, dict) or type(entry.get("id")) is not int:
                raise ValueError("TTS direction entry has an invalid ID")
            segment_id = entry["id"]
            if segment_id in supplied or segment_id not in by_id:
                raise ValueError(f"TTS direction has an unknown or repeated ID: {segment_id}")
            spoken = (by_id[segment_id].en or "").strip()
            if entry.get("text") != spoken:
                raise ValueError(f"TTS direction text differs from segment {segment_id}")
            style = entry.get("style")
            intensity = entry.get("intensity", 0.5)
            if style not in STYLES or isinstance(intensity, bool) or not isinstance(
                intensity, (int, float)
            ) or not math.isfinite(intensity) or not 0 <= intensity <= 1:
                raise ValueError(f"TTS direction style or intensity is invalid for {segment_id}")
            supplied[segment_id] = {"style": style, "intensity": float(intensity)}

    plan = [
        {
            "id": segment.id,
            "text": (segment.en or "").strip(),
            "style": supplied.get(segment.id, {}).get("style", "neutral"),
            "intensity": supplied.get(segment.id, {}).get("intensity", 0.0),
        }
        for segment in manifest.segments
    ]
    destination = job_dir / "tts-delivery.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"version": 1, "segments": plan}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    manifest.artifacts["tts_delivery_plan"] = destination.name
    manifest.metadata["tts_delivery"] = {
        "directed_segment_count": len(supplied),
        "segment_count": len(plan),
    }
    return plan
