from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..models import AssetCandidate, VisualRequest, ProviderCapabilities
from ..transport import Transport


def plain(value: object) -> str:
    return html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).strip()[:8000]


def years(value: object) -> tuple[int | None, int | None]:
    found = [int(v) for v in re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", plain(value))]
    return (min(found), max(found)) if found else (None, None)


def media_kind(url: str) -> str | None:
    suffix = urlsplit(url).path.lower()
    if suffix.endswith((".jpg", ".jpeg", ".png")):
        return "image"
    if suffix.endswith((".mp4", ".webm", ".ogv")):
        return "video"
    return None


class BaseProvider:
    name = "base"
    media_hosts: frozenset[str] = frozenset()
    archive = False
    disabled_reason = "missing_api_key"
    capabilities = ProviderCapabilities()

    def __init__(self, transport: Transport, key: str = "", limit: int = 6):
        self.transport = transport
        self.key = key
        self.limit = limit
        self.enabled = True

    def supports(self, request: VisualRequest) -> bool:
        return (self.enabled and request.visual_intent not in {"number", "abstract", "original"}
                and bool(set(request.media_types) & set(self.capabilities.media_types))
                and (request.accuracy != "exact" or self.capabilities.factual_metadata))

    def candidate(self, **values) -> AssetCandidate:
        return AssetCandidate(provider=self.name, retrieved_at=datetime.now(timezone.utc).isoformat(), **values)
