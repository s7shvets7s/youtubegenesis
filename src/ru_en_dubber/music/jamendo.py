from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.request import Request, build_opener

from ru_en_dubber.security import validate_download_url
from ru_en_dubber.visuals.transport import ProviderError, SafeRedirect, Transport


JAMENDO_MEDIA_HOSTS = frozenset({
    "prod-1.storage.jamendo.com",
    "prod-2.storage.jamendo.com",
    "storage.jamendo.com",
    "usercontent.jamendo.com",
})


def allowed_license(url: object) -> str | None:
    value = str(url or "").strip().lower().rstrip("/") + "/"
    if "/publicdomain/zero/" in value:
        return "CC0"
    match = re.search(r"/licenses/([^/]+)/", value)
    if match and match.group(1) == "by":
        return "CC BY"
    return None


class JamendoProvider:
    name = "jamendo"

    def __init__(self, transport: Transport, client_id: str, limit: int = 20) -> None:
        self.transport = transport
        self.client_id = client_id.strip()
        self.limit = max(1, min(50, limit))

    @property
    def enabled(self) -> bool:
        return bool(self.client_id)

    def search(self, terms: list[str]) -> list[dict]:
        if not self.enabled:
            return []
        fuzzy = " ".join(dict.fromkeys(term.strip().casefold() for term in terms if term.strip()))[:180]
        data = self.transport.json(
            self.name,
            "https://api.jamendo.com/v3.0/tracks/",
            {
                "client_id": self.client_id,
                "format": "json",
                "limit": self.limit,
                "fuzzytags": fuzzy,
                "vocalinstrumental": "instrumental",
                "ccnc": "false",
                "ccnd": "false",
                "ccsa": "false",
                "include": "licenses musicinfo",
                "audioformat": "mp32",
                "order": "relevance",
            },
            ttl=7 * 86400,
        )
        results: list[dict] = []
        for item in data.get("results", []):
            if not isinstance(item, dict) or item.get("audiodownload_allowed") is not True:
                continue
            license_code = allowed_license(item.get("license_ccurl"))
            download_url = str(item.get("audiodownload") or "")
            if not license_code or not download_url:
                continue
            try:
                validate_download_url(download_url, allowed_hosts=JAMENDO_MEDIA_HOSTS)
                duration = float(item.get("duration") or 0)
            except (ValueError, TypeError):
                continue
            if duration < 20 or duration > 3600:
                continue
            results.append({
                "provider_id": str(item.get("id") or "")[:120],
                "title": str(item.get("name") or "Untitled")[:300],
                "artist": str(item.get("artist_name") or "Unknown")[:300],
                "duration": duration,
                "download_url": download_url,
                "source_url": str(item.get("shareurl") or item.get("shorturl") or "")[:1000],
                "license": license_code,
                "license_url": str(item.get("license_ccurl") or "")[:1000],
            })
        return results

    @staticmethod
    def download(url: str, destination: Path) -> None:
        validate_download_url(url, allowed_hosts=JAMENDO_MEDIA_HOSTS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        maximum = 100 * 1024 * 1024
        started = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": "ru-en-dubber/0.2"})
            with build_opener(SafeRedirect(JAMENDO_MEDIA_HOSTS)).open(
                request, timeout=30
            ) as response, temporary.open("wb") as output:
                mime = response.headers.get_content_type().casefold()
                if mime not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                    raise ProviderError("Jamendo returned a disallowed audio MIME")
                if int(response.headers.get("Content-Length", "0")) > maximum:
                    raise ProviderError("Jamendo track exceeds size limit")
                size = 0
                first = True
                while chunk := response.read(256 * 1024):
                    size += len(chunk)
                    if size > maximum or time.monotonic() - started > 180:
                        raise ProviderError("Jamendo transfer exceeded safety budget")
                    if first:
                        if not (chunk.startswith(b"ID3") or (
                            len(chunk) >= 2 and chunk[0] == 0xFF and chunk[1] & 0xE0 == 0xE0
                        )):
                            raise ProviderError("Jamendo audio signature is not MP3")
                        first = False
                    output.write(chunk)
                if size == 0:
                    raise ProviderError("Jamendo returned an empty track")
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
