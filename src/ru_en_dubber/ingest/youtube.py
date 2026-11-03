from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import MediaSecurityError, validate_media_file


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
METADATA_MARKER = "__YTDLP_METADATA__"


class YouTubeDownloadError(RuntimeError):
    """Raised when a YouTube URL cannot be downloaded safely."""


def is_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in YOUTUBE_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
    )


class YouTubeDownloadStage(PipelineStage):
    """Download one YouTube video into the job as source.mp4."""

    def __init__(
        self,
        *,
        python: str,
        ffmpeg: str,
        max_height: int = 1080,
        cookies_from_browser: str | None = None,
    ) -> None:
        if max_height < 144:
            raise ValueError("max_height must be at least 144")
        self._python = python
        self._ffmpeg = ffmpeg
        self._max_height = max_height
        self._cookies_from_browser = cookies_from_browser

    @property
    def name(self) -> str:
        return "youtube_download"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        self.report_progress(0.02, "Проверка ссылки…")
        source_url = manifest.job.source_url
        if source_url is None or not is_youtube_url(source_url):
            raise YouTubeDownloadError("job does not contain a supported YouTube URL")

        python = self._resolve_executable(self._python, "Python")
        ffmpeg = self._resolve_executable(self._ffmpeg, "FFmpeg")
        self.report_progress(0.08, "Подготовка yt-dlp…")
        output_path = job_dir / "source.mp4"
        if output_path.exists():
            raise YouTubeDownloadError(f"download target already exists: {output_path}")

        metadata_template = (
            METADATA_MARKER
            + '{"filepath":%(filepath)j,"id":%(id)j,"title":%(title)j,'
            + '"duration":%(duration)j,"webpage_url":%(webpage_url)j}'
        )
        command = [
            python,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-playlist",
            "--no-write-playlist-metafiles",
            "--no-write-info-json",
            "--no-write-comments",
            "--max-filesize",
            "8G",
            "--restrict-filenames",
            "--ffmpeg-location",
            str(Path(ffmpeg).parent),
            "--format",
            f"bv*[height<={self._max_height}]+ba/b[height<={self._max_height}]",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--output",
            str(job_dir / "source.%(ext)s"),
            "--print",
            f"after_move:{metadata_template}",
        ]
        if self._cookies_from_browser is not None:
            command.extend(["--cookies-from-browser", self._cookies_from_browser])
        command.append(source_url)

        self.report_progress(None, "Скачивание и объединение потоков…")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10_800,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise YouTubeDownloadError(f"yt-dlp download failed: {details}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise YouTubeDownloadError(
                "yt-dlp completed but the expected source.mp4 was not created"
            )
        try:
            validate_media_file(
                output_path,
                label="downloaded YouTube video",
                maximum_bytes=8 * 1024 * 1024 * 1024,
            )
        except MediaSecurityError as error:
            raise YouTubeDownloadError(str(error)) from error

        self.report_progress(0.92, "Проверка загруженного видео…")
        metadata = self._parse_metadata(result.stdout)
        manifest.job.source_file = output_path.name
        manifest.artifacts["source_video"] = output_path.name
        manifest.metadata["input"] = {"type": "youtube", "url": source_url}
        manifest.metadata["youtube"] = {
            "id": metadata.get("id"),
            "title": metadata.get("title"),
            "duration": metadata.get("duration"),
            "webpage_url": metadata.get("webpage_url", source_url),
            "max_height": self._max_height,
        }
        self.report_progress(0.98, "Видео готово")
        return manifest

    @staticmethod
    def _resolve_executable(command: str, label: str) -> str:
        command_path = Path(command).expanduser()
        if command_path.parent != Path("."):
            if command_path.is_file():
                return str(command_path.resolve())
            raise YouTubeDownloadError(f"{label} executable not found: {command_path}")
        resolved = shutil.which(command)
        if resolved is None:
            raise YouTubeDownloadError(f"{label} executable not found: {command}")
        return resolved

    @staticmethod
    def _parse_metadata(output: str) -> dict[str, object]:
        for line in reversed(output.splitlines()):
            if line.startswith(METADATA_MARKER):
                try:
                    parsed = json.loads(line.removeprefix(METADATA_MARKER))
                except json.JSONDecodeError as error:
                    raise YouTubeDownloadError("yt-dlp returned invalid metadata") from error
                if not isinstance(parsed, dict):
                    raise YouTubeDownloadError("yt-dlp returned invalid metadata")
                return parsed
        raise YouTubeDownloadError("yt-dlp did not return download metadata")
