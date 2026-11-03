from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Segment:
    """One logical speech unit exchanged by transcription, translation and TTS."""

    id: int
    start: float
    end: float
    ru: str
    speaker: str = "SPEAKER_00"
    en: str | None = None
    tts_file: str | None = None
    tts_duration: float | None = None
    dub_file: str | None = None
    dub_start: float | None = None
    dub_duration: float | None = None
    dub_speed: float | None = None

    def __post_init__(self) -> None:
        numeric_fields = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "tts_duration": self.tts_duration,
            "dub_start": self.dub_start,
            "dub_duration": self.dub_duration,
            "dub_speed": self.dub_speed,
        }
        for name, value in numeric_fields.items():
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.id < 1:
            raise ValueError("segment id must be positive")
        if self.start < 0:
            raise ValueError("segment start must not be negative")
        if self.end <= self.start:
            raise ValueError("segment end must be greater than start")
        if not self.speaker:
            raise ValueError("segment speaker must not be empty")
        if not self.ru.strip():
            raise ValueError("segment Russian text must not be empty")
        if self.tts_duration is not None and self.tts_duration <= 0:
            raise ValueError("tts_duration must be positive")
        if self.dub_start is not None and self.dub_start < 0:
            raise ValueError("dub_start must not be negative")
        if self.dub_duration is not None and self.dub_duration <= 0:
            raise ValueError("dub_duration must be positive")
        if self.dub_speed is not None and self.dub_speed <= 0:
            raise ValueError("dub_speed must be positive")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def timing_error_ratio(self) -> float | None:
        if self.tts_duration is None:
            return None
        return (self.tts_duration - self.duration) / self.duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "speaker": self.speaker,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "ru": self.ru,
            "en": self.en,
            "tts_file": self.tts_file,
            "tts_duration": self.tts_duration,
            "dub_file": self.dub_file,
            "dub_start": self.dub_start,
            "dub_duration": self.dub_duration,
            "dub_speed": self.dub_speed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        segment = cls(
            id=int(data["id"]),
            speaker=str(data.get("speaker", "SPEAKER_00")),
            start=float(data["start"]),
            end=float(data["end"]),
            ru=str(data["ru"]),
            en=data.get("en"),
            tts_file=data.get("tts_file"),
            tts_duration=(
                float(data["tts_duration"])
                if data.get("tts_duration") is not None
                else None
            ),
            dub_file=data.get("dub_file"),
            dub_start=(
                float(data["dub_start"])
                if data.get("dub_start") is not None
                else None
            ),
            dub_duration=(
                float(data["dub_duration"])
                if data.get("dub_duration") is not None
                else None
            ),
            dub_speed=(
                float(data["dub_speed"])
                if data.get("dub_speed") is not None
                else None
            ),
        )

        stored_duration = data.get("duration")
        if stored_duration is not None and not math.isclose(
            float(stored_duration), segment.duration, abs_tol=1e-6
        ):
            raise ValueError("stored segment duration does not match start/end")
        return segment
