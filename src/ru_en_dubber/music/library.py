from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ru_en_dubber.visuals.transport import write_json


ENERGY_LEVELS = {"very_low", "low", "medium", "high", "very_high"}
ALLOWED_FIELDS = {
    "id", "file", "title", "artist", "provider", "provider_id", "genres",
    "moods", "energy", "bpm", "instrumental", "use_for", "avoid_for",
    "license", "license_url", "source_url", "attribution", "approved",
    "downloaded_at", "sha256", "duration", "classification_source",
}


class MusicCatalogError(ValueError):
    pass


def _strings(value: object, *, maximum: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise MusicCatalogError("music tag lists must be bounded arrays")
    result = tuple(str(item).strip()[:100] for item in value if str(item).strip())
    if len(result) != len(value):
        raise MusicCatalogError("music tags must be non-empty strings")
    return result


@dataclass(frozen=True, slots=True)
class MusicTrack:
    id: str
    file: str
    title: str
    artist: str
    provider: str
    provider_id: str | None
    genres: tuple[str, ...]
    moods: tuple[str, ...]
    energy: str
    instrumental: bool
    use_for: tuple[str, ...]
    avoid_for: tuple[str, ...]
    license: str
    license_url: str | None
    source_url: str | None
    attribution: str | None
    approved: bool
    bpm: float | None = None
    downloaded_at: str | None = None
    sha256: str | None = None
    duration: float | None = None
    classification_source: str | None = None

    @classmethod
    def from_dict(cls, raw: object) -> MusicTrack:
        if not isinstance(raw, dict) or set(raw) - ALLOWED_FIELDS:
            raise MusicCatalogError("music track contains unknown fields")
        identifier = str(raw.get("id") or "").strip()
        filename = str(raw.get("file") or "").strip().replace("\\", "/")
        provider = str(raw.get("provider") or "").strip().casefold()
        energy = str(raw.get("energy") or "").strip().casefold()
        license_code = str(raw.get("license") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}", identifier):
            raise MusicCatalogError("invalid music track id")
        path = Path(filename)
        if not filename or path.is_absolute() or ".." in path.parts:
            raise MusicCatalogError("music file must stay inside the library")
        if provider not in {"local", "jamendo"}:
            raise MusicCatalogError("music provider must be local or jamendo")
        if energy not in ENERGY_LEVELS:
            raise MusicCatalogError("invalid music energy")
        if not isinstance(raw.get("instrumental"), bool):
            raise MusicCatalogError("instrumental must be a boolean")
        if not license_code or raw.get("approved") is not True:
            raise MusicCatalogError("music track must have a license and explicit approval")
        bpm = raw.get("bpm")
        duration = raw.get("duration")
        parsed_bpm = float(bpm) if bpm is not None else None
        parsed_duration = float(duration) if duration is not None else None
        if parsed_bpm is not None and (not math.isfinite(parsed_bpm) or parsed_bpm <= 0):
            raise MusicCatalogError("invalid music bpm")
        if parsed_duration is not None and (
            not math.isfinite(parsed_duration) or parsed_duration <= 0
        ):
            raise MusicCatalogError("invalid music duration")
        return cls(
            id=identifier,
            file=filename,
            title=str(raw.get("title") or identifier).strip()[:300],
            artist=str(raw.get("artist") or "Unknown").strip()[:300],
            provider=provider,
            provider_id=(str(raw["provider_id"]).strip()[:120]
                         if raw.get("provider_id") is not None else None),
            genres=_strings(raw.get("genres")),
            moods=_strings(raw.get("moods")),
            energy=energy,
            instrumental=raw["instrumental"],
            use_for=_strings(raw.get("use_for", [])),
            avoid_for=_strings(raw.get("avoid_for", [])),
            license=license_code[:120],
            license_url=(str(raw["license_url"]).strip()[:1000]
                         if raw.get("license_url") else None),
            source_url=(str(raw["source_url"]).strip()[:1000]
                        if raw.get("source_url") else None),
            attribution=(str(raw["attribution"]).strip()[:1000]
                         if raw.get("attribution") else None),
            approved=True,
            bpm=parsed_bpm,
            downloaded_at=(str(raw["downloaded_at"]) if raw.get("downloaded_at") else None),
            sha256=(str(raw["sha256"]).casefold() if raw.get("sha256") else None),
            duration=parsed_duration,
            classification_source=(str(raw["classification_source"])
                                   if raw.get("classification_source") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "file": self.file, "title": self.title,
            "artist": self.artist, "provider": self.provider,
            "provider_id": self.provider_id, "genres": list(self.genres),
            "moods": list(self.moods), "energy": self.energy, "bpm": self.bpm,
            "instrumental": self.instrumental, "use_for": list(self.use_for),
            "avoid_for": list(self.avoid_for), "license": self.license,
            "license_url": self.license_url, "source_url": self.source_url,
            "attribution": self.attribution, "approved": self.approved,
            "downloaded_at": self.downloaded_at, "sha256": self.sha256,
            "duration": self.duration,
            "classification_source": self.classification_source,
        }

    def resolve(self, library: Path) -> Path:
        root = library.expanduser().resolve()
        resolved = (root / self.file).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise MusicCatalogError("music file escapes the library") from error
        return resolved


class MusicCatalog:
    def __init__(
        self, root: Path, tracks: list[MusicTrack], invalid_entries: list[str] | None = None
    ) -> None:
        self.root = root.expanduser().resolve()
        self.tracks = tracks
        self.invalid_entries = invalid_entries or []

    @classmethod
    def load(cls, root: Path) -> MusicCatalog:
        resolved = root.expanduser().resolve()
        path = resolved / "catalog.json"
        if not path.is_file():
            return cls(resolved, [])
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or set(payload) != {"version", "tracks"}:
            raise MusicCatalogError("invalid music catalog schema")
        if payload.get("version") != 1 or not isinstance(payload.get("tracks"), list):
            raise MusicCatalogError("unsupported music catalog version")
        tracks: list[MusicTrack] = []
        invalid: list[str] = []
        ids: set[str] = set()
        provider_ids: set[str] = set()
        for index, item in enumerate(payload["tracks"]):
            try:
                track = MusicTrack.from_dict(item)
                if track.id in ids:
                    raise MusicCatalogError("duplicate music track id")
                if (track.provider == "jamendo" and track.provider_id
                        and track.provider_id in provider_ids):
                    raise MusicCatalogError("duplicate Jamendo track id")
                track.resolve(resolved)
            except (MusicCatalogError, TypeError, ValueError) as error:
                invalid.append(f"track[{index}]:{type(error).__name__}")
                continue
            tracks.append(track)
            ids.add(track.id)
            if track.provider == "jamendo" and track.provider_id:
                provider_ids.add(track.provider_id)
        return cls(resolved, tracks, invalid)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_json(self.root / "catalog.json", {
            "version": 1,
            "tracks": [track.to_dict() for track in self.tracks],
        })

    def add(self, track: MusicTrack) -> MusicTrack:
        for existing in self.tracks:
            if existing.id == track.id or (
                track.provider == "jamendo" and track.provider_id
                and existing.provider == "jamendo"
                and existing.provider_id == track.provider_id
            ):
                return existing
        self.tracks.append(track)
        self.save()
        return track


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
