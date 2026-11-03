"""Provider-independent, serializable visual planning contracts."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Protocol

POLICY_VERSION = "media-v2-localized-fallback"
INTENTS = {"event", "person", "place", "object", "process", "number", "map",
           "document", "atmosphere", "abstract", "original"}


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class VisualRequest:
    index: int
    start: float
    end: float
    narration: str
    subject: str
    domain: str = "general"
    visual_intent: str = "abstract"
    entities: tuple[str, ...] = ()
    location: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    accuracy: str = "exact"
    media_types: tuple[str, ...] = ("video", "image")

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.start, self.end)) or self.end <= self.start:
            raise ValueError("invalid visual timeline")
        if self.visual_intent not in INTENTS or self.accuracy not in {"exact", "illustrative"}:
            raise ValueError("invalid visual classification")
        if not self.subject.strip() or len(self.subject) > 500:
            raise ValueError("invalid visual subject")
        if not self.media_types or set(self.media_types) - {"video", "image", "chart"}:
            raise ValueError("invalid media types")
        if any(v is not None and (type(v) is not int or not -10000 <= v <= 10000)
               for v in (self.year_from, self.year_to)):
            raise ValueError("invalid period")
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("reversed period")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AssetCandidate:
    provider: str
    asset_id: str
    download_url: str
    page_url: str
    author: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0
    media_type: str = "video"
    title: str = ""
    description: str = ""
    license_code: str = "unknown"
    license_url: str = ""
    rights_evidence: str = ""
    year_from: int | None = None
    year_to: int | None = None
    location: str | None = None
    retrieved_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.asset_id}"

    def to_dict(self) -> dict:
        return asdict(self)


class MediaProvider(Protocol):
    name: str
    media_hosts: frozenset[str]
    enabled: bool

    def supports(self, request: VisualRequest) -> bool: ...
    def search(self, request: VisualRequest) -> list[AssetCandidate]: ...


@dataclass(frozen=True)
class ProviderCapabilities:
    media_types: tuple[str, ...] = ("video", "image")
    factual_metadata: bool = False
    geographic_focus: tuple[str, ...] = ()
    licenses: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataRequest:
    """Numeric observations are not stock-media search results."""
    series_id: str
    start: str
    end: str
    units: str


class DataProvider(Protocol):
    def observations(self, request: DataRequest) -> list[tuple[str, float | None]]: ...
