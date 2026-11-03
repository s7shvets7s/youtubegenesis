"""Bounded HTTPS transport. Redirects are checked before making the next request."""
from __future__ import annotations

import json
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ru_en_dubber.security import validate_download_url
from .models import POLICY_VERSION, fingerprint


class ProviderError(RuntimeError):
    pass


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, hosts: frozenset[str]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl, allowed_hosts=self.hosts, label="redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Transport:
    def __init__(self, cache: Path, budget: int = 120):
        self.cache = cache
        self.budget = budget
        self.calls = 0
        self.cache_hits = 0
        self.disabled: dict[str, float] = {}
        self.events: list[dict] = []
        self.last_call: dict[str, float] = {}

    def json(self, provider: str, endpoint: str, params: dict, *, headers: dict | None = None,
             ttl: int = 86400) -> dict:
        from urllib.parse import urlsplit
        # Keys are never written into filenames, cache values, errors or reports.
        public_params = {
            k: v for k, v in params.items()
            if k not in {"key", "api_key", "client_id"}
        }
        cache_key = fingerprint([POLICY_VERSION, provider, endpoint, public_params])
        path = self.cache / provider / f"{cache_key}.json"
        if path.is_file() and time.time() - path.stat().st_mtime < ttl:
            try:
                if path.stat().st_size <= 8 * 1024 * 1024:
                    result = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(result, dict):
                        self.cache_hits += 1
                        return result
            except (ValueError, OSError):
                pass
        if time.monotonic() < self.disabled.get(provider, 0):
            raise ProviderError(f"{provider}: temporarily disabled")
        hosts = frozenset({urlsplit(endpoint).hostname})
        url = endpoint + "?" + urlencode(params)
        validate_download_url(url, allowed_hosts=hosts)
        for attempt in range(2):
            if self.calls >= self.budget:
                raise ProviderError("provider request budget exhausted")
            # Stay below Pixabay's default 100/minute ceiling within this run.
            gap = 0.65 - (time.monotonic() - self.last_call.get(provider, 0))
            if gap > 0:
                time.sleep(gap)
            self.last_call[provider] = time.monotonic()
            self.calls += 1
            request = Request(url, headers={"User-Agent": "ru-en-dubber/0.2 (local video editor)",
                                           **(headers or {})})
            try:
                with build_opener(SafeRedirect(hosts)).open(request, timeout=25) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ProviderError("API response exceeds size limit")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ProviderError("API response is not an object")
                write_json(path, result)
                self.events.append({"provider": provider, "status": "ok"})
                return result
            except HTTPError as error:
                status = error.code
                try:
                    retry_after = error.headers.get("Retry-After", "60")
                    try:
                        delay = max(1, int(retry_after))
                    except ValueError:
                        delay = max(1, parsedate_to_datetime(retry_after).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    delay = 60
                error.close()
                self.events.append({"provider": provider, "status": status})
                if status == 429:
                    self.disabled[provider] = time.monotonic() + delay
                    break
                if status in {401, 403}:
                    self.disabled[provider] = time.monotonic() + 3600
                    break
                if status < 500:
                    break
            except (OSError, ValueError, ProviderError):
                self.events.append({"provider": provider, "status": "invalid_or_unavailable"})
            if attempt == 0:
                time.sleep(0.5)
        self.disabled[provider] = max(self.disabled.get(provider, 0), time.monotonic() + 60)
        raise ProviderError(f"{provider}: search unavailable (see provider-report.json)")


def download(url: str, destination: Path, hosts: frozenset[str], media_type: str) -> None:
    validate_download_url(url, allowed_hosts=hosts)
    maximum = (50 if media_type == "image" else 750) * 1024 * 1024
    accepted = ({"image/jpeg", "image/png"} if media_type == "image" else
                {"video/mp4", "video/webm", "video/ogg", "application/ogg"})
    temporary = destination.with_suffix(destination.suffix + ".part")
    started = time.monotonic()
    try:
        req = Request(url, headers={"User-Agent": "ru-en-dubber/0.2"})
        with build_opener(SafeRedirect(hosts)).open(req, timeout=30) as response, temporary.open("wb") as output:
            mime = response.headers.get_content_type().lower()
            if mime not in accepted | {"application/octet-stream"}:
                raise ProviderError("disallowed media MIME")
            if int(response.headers.get("Content-Length", "0")) > maximum:
                raise ProviderError("media exceeds size limit")
            count = 0
            first = True
            while chunk := response.read(256 * 1024):
                count += len(chunk)
                if count > maximum or time.monotonic() - started > 180:
                    raise ProviderError("media exceeds transfer budget")
                if first:
                    valid = (chunk.startswith(b"\xff\xd8\xff") or chunk.startswith(b"\x89PNG\r\n\x1a\n")) if media_type == "image" else (
                        chunk[4:8] == b"ftyp" or chunk.startswith(b"\x1aE\xdf\xa3") or chunk.startswith(b"OggS"))
                    if not valid:
                        raise ProviderError("media signature does not match supported format")
                    first = False
                output.write(chunk)
            if count == 0:
                raise ProviderError("empty media")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
