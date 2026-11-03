"""Fail-closed rights and factual filters followed by deterministic ranking."""
from __future__ import annotations

import math
import re

from .models import AssetCandidate, VisualRequest, ProviderCapabilities


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower())) - {
        "the", "and", "was", "were", "with", "that", "this", "from", "into", "for"}


def rights_reason(asset: AssetCandidate) -> str | None:
    code = asset.license_code.casefold().strip()
    if not asset.page_url.startswith("https://") or not asset.rights_evidence:
        return "missing_rights_evidence"
    if code in {"pexels", "pixabay"}:
        if asset.provider != code:
            return "provider_license_mismatch"
        return None
    if code in {"public domain", "cc0"}:
        return None
    if re.fullmatch(r"cc by (?:1\.0|2\.0|2\.5|3\.0|4\.0)", code):
        if asset.author and asset.license_url.startswith("https://creativecommons.org/licenses/by/"):
            return None
        return "missing_attribution"
    return "rights_require_review"


def rejection_reason(asset: AssetCandidate, request: VisualRequest) -> str | None:
    reason = rights_reason(asset)
    if reason:
        return reason
    if asset.media_type not in request.media_types:
        return "wrong_media_type"
    if not math.isfinite(asset.duration) or asset.duration < 0:
        return "invalid_duration"
    if request.visual_intent in {"number", "abstract", "original"}:
        return "requires_data_or_explicit_plan"
    description = (asset.title + " " + asset.description + " " + (asset.location or "")).lower()
    overlap = len(words(request.subject) & words(description)) / max(1, len(words(request.subject)))
    if request.accuracy == "exact":
        if asset.provider in {"pexels", "pixabay"}:
            return "stock_cannot_verify_exact_claim"
        if not request.entities:
            return "exact_subject_unverified"
        if any(entity.lower() not in description for entity in request.entities):
            return "entity_not_confirmed"
        if request.location and request.location.lower() not in description:
            return "location_not_confirmed"
        if request.year_from is not None or request.year_to is not None:
            if asset.year_from is None or asset.year_to is None:
                return "period_unknown"
            lower = request.year_from if request.year_from is not None else request.year_to
            upper = request.year_to if request.year_to is not None else request.year_from
            if asset.year_from < lower or asset.year_to > upper:
                return "period_mismatch"
        if overlap < 0.25:
            return "low_relevance"
    elif description.strip() and overlap < 0.15:
        return "low_relevance"
    return None


def rank(candidates: list[AssetCandidate], request: VisualRequest, used: set[str],
         width: int, height: int) -> list[AssetCandidate]:
    def score(asset):
        relevance = len(words(request.subject) & words(asset.title + " " + asset.description)) / max(1, len(words(request.subject)))
        ratio = asset.width / max(1, asset.height)
        quality = min(1, asset.width / width) if asset.width else 0
        repeat = asset.key in used or asset.download_url in used
        return (-int(repeat), relevance, quality, -abs(ratio - width / height),
                min(asset.duration, request.duration), asset.key)
    return sorted(candidates, key=score, reverse=True)


def route(providers, request: VisualRequest):
    preferred = (["nara", "wikimedia", "loc"] if request.visual_intent == "event" else
                 ["wikimedia", "loc", "nara"]) if request.accuracy == "exact" else ["pexels", "pixabay", "wikimedia", "loc", "nara"]
    def priority(provider):
        score = preferred.index(provider.name) if provider.name in preferred else 99
        capabilities = getattr(provider, "capabilities", None)
        if isinstance(capabilities, ProviderCapabilities):
            context = " ".join([request.location or "", *request.entities]).casefold()
            if any(place.casefold() in context for place in capabilities.geographic_focus):
                score -= 2
            # Historical map/photo preference, not a hard date cutoff on an archive.
            if request.year_to is not None and request.year_to < 1950 and provider.name == "loc":
                score -= 1
        return (score, provider.name)
    return sorted((p for p in providers if p.supports(request)), key=priority)
