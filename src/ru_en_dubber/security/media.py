from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


class MediaSecurityError(RuntimeError):
    """Raised when untrusted media violates the pipeline safety policy."""


_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")


def validate_job_id(value: str) -> str:
    """Validate the portable directory name used to identify one job."""
    if not isinstance(value, str) or _JOB_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "job id must contain 1-100 ASCII letters, digits, hyphens or underscores"
        )
    return value


def resolve_job_directory(jobs_dir: Path, job_id: str) -> Path:
    """Resolve one job directory and guarantee it remains below ``jobs_dir``."""
    root = jobs_dir.resolve()
    resolved = (root / validate_job_id(job_id)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("job id resolves outside the jobs directory") from error
    return resolved


def resolve_job_path(job_dir: Path, relative: str, *, label: str) -> Path:
    """Resolve an artifact path without allowing it to escape the job directory."""
    value = Path(relative)
    if value.is_absolute():
        raise MediaSecurityError(f"{label} must use a relative job path")
    root = job_dir.resolve()
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise MediaSecurityError(f"{label} points outside the job directory") from error
    return resolved


def validate_download_url(
    value: str, *, allowed_hosts: Iterable[str], label: str = "download URL"
) -> str:
    """Accept only direct HTTPS URLs on an explicit hostname allowlist."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise MediaSecurityError(f"{label} is invalid") from error
    hosts = {host.casefold() for host in allowed_hosts}
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or hostname not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise MediaSecurityError(f"{label} is not an allowed HTTPS address")
    return value


def validate_media_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> None:
    if not path.is_file():
        raise MediaSecurityError(f"{label} was not found")
    size = path.stat().st_size
    if size <= 0:
        raise MediaSecurityError(f"{label} is empty")
    if size > maximum_bytes:
        raise MediaSecurityError(
            f"{label} exceeds the {maximum_bytes // (1024 * 1024)} MiB safety limit"
        )


def _number(value: Any, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _frame_rate(value: Any) -> float:
    text = str(value or "0")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        divisor = _number(denominator)
        return _number(numerator) / divisor if divisor else 0.0
    return _number(text)


def validate_probe_payload(
    payload: dict[str, Any],
    *,
    label: str,
    require_video: bool = False,
    require_audio: bool = False,
    maximum_duration: float = 21_600.0,
    maximum_width: int = 7680,
    maximum_height: int = 4320,
    maximum_fps: float = 120.0,
    allowed_video_codecs: set[str] | None = None,
    allowed_format_tokens: set[str] | None = None,
) -> None:
    """Reject resource bombs and active/irrelevant streams before transcoding."""
    if not isinstance(payload, dict):
        raise MediaSecurityError(f"{label} probe result is invalid")
    streams = payload.get("streams")
    if not isinstance(streams, list) or len(streams) > 32:
        raise MediaSecurityError(f"{label} has an invalid stream table")
    kinds = [
        str(stream.get("codec_type") or "")
        for stream in streams
        if isinstance(stream, dict)
    ]
    if "attachment" in kinds:
        raise MediaSecurityError(f"{label} contains attachment streams")
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "data":
            continue
        codec = str(stream.get("codec_name") or "").casefold()
        tag = str(stream.get("codec_tag_string") or "").casefold()
        if codec != "tmcd" and tag != "tmcd":
            raise MediaSecurityError(f"{label} contains an unsupported data stream")
    if require_video and "video" not in kinds:
        raise MediaSecurityError(f"{label} does not contain a video stream")
    if require_audio and "audio" not in kinds:
        raise MediaSecurityError(f"{label} does not contain an audio stream")

    media_format = payload.get("format")
    media_format = media_format if isinstance(media_format, dict) else {}
    duration = _number(media_format.get("duration"))
    if duration <= 0:
        durations = [
            _number(stream.get("duration"))
            for stream in streams
            if isinstance(stream, dict)
        ]
        duration = max(durations, default=0.0)
    if duration <= 0 or duration > maximum_duration:
        raise MediaSecurityError(f"{label} has an invalid or excessive duration")

    format_names = {
        item.strip().casefold()
        for item in str(media_format.get("format_name") or "").split(",")
        if item.strip()
    }
    if allowed_format_tokens and not format_names.intersection(allowed_format_tokens):
        raise MediaSecurityError(f"{label} uses a disallowed media container")

    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        width = int(_number(stream.get("width")))
        height = int(_number(stream.get("height")))
        fps = _frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
        if (
            width <= 0
            or height <= 0
            or width > maximum_width
            or height > maximum_height
        ):
            raise MediaSecurityError(f"{label} has unsafe video dimensions")
        if (
            width * height > maximum_width * maximum_height
            or fps <= 0
            or fps > maximum_fps
        ):
            raise MediaSecurityError(f"{label} has an unsafe frame rate or frame size")
        codec = str(stream.get("codec_name") or "").casefold()
        if allowed_video_codecs is not None and codec not in allowed_video_codecs:
            raise MediaSecurityError(
                f"{label} uses disallowed video codec: {codec or 'unknown'}"
            )
