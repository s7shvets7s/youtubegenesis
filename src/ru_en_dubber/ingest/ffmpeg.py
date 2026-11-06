from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import (
    MediaSecurityError,
    resolve_job_path,
    validate_media_file,
    validate_probe_payload,
)


class FFmpegError(RuntimeError):
    """Base error raised by the FFmpeg ingest adapter."""


class FFmpegNotFoundError(FFmpegError):
    """Raised when FFmpeg or FFprobe cannot be resolved."""


class FFmpegExecutionError(FFmpegError):
    """Raised when an FFmpeg command exits unsuccessfully."""


class FFmpegAudioExtractionStage(PipelineStage):
    """Extract the first audio stream as uncompressed WAV.

    The 48 kHz stereo PCM file is intended for source separation. WhisperX will
    later consume the separated vocals and perform its own mono/16 kHz loading.
    """

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        sample_rate: int = 48_000,
        channels: int = 2,
        output_name: str = "audio.wav",
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        if Path(output_name).name != output_name or not output_name.lower().endswith(".wav"):
            raise ValueError("output_name must be a WAV filename, not a path")

        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._sample_rate = sample_rate
        self._channels = channels
        self._output_name = output_name

    @property
    def name(self) -> str:
        return "audio_extraction"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        self.report_progress(0.03, "Locating FFmpeg…")
        ffmpeg = self._resolve_executable(self._ffmpeg)
        ffprobe = self._resolve_executable(self._ffprobe)
        try:
            source_path = resolve_job_path(
                job_dir, manifest.job.source_file, label="source video"
            )
            validate_media_file(
                source_path,
                label="source video",
                maximum_bytes=8 * 1024 * 1024 * 1024,
            )
        except MediaSecurityError as error:
            raise FFmpegExecutionError(str(error)) from error

        self.report_progress(0.08, "Reading video parameters…")
        media_info = self._probe(ffprobe, source_path)
        audio_streams = [
            stream
            for stream in media_info.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]
        if not audio_streams:
            raise FFmpegExecutionError("source video does not contain an audio stream")
        try:
            validate_probe_payload(
                media_info,
                label="source video",
                require_audio=True,
            )
        except MediaSecurityError as error:
            raise FFmpegExecutionError(str(error)) from error

        output_path = job_dir / self._output_name
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(self._sample_rate),
            "-ac",
            str(self._channels),
            str(output_path),
        ]
        self.report_progress(None, "Extracting 48 kHz PCM audio…")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(
                300.0,
                float(media_info.get("format", {}).get("duration") or 0.0) * 4.0,
            ),
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "unknown FFmpeg error"
            raise FFmpegExecutionError(f"audio extraction failed: {message}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise FFmpegExecutionError("FFmpeg completed but audio.wav was not created")

        self.report_progress(0.92, "Checking the audio file…")
        source_format = media_info.get("format", {})
        source_audio = audio_streams[0]
        manifest.artifacts["extracted_audio"] = self._output_name
        manifest.metadata["source_media"] = {
            "duration": self._optional_float(source_format.get("duration")),
            "format_name": source_format.get("format_name"),
            "audio_codec": source_audio.get("codec_name"),
            "audio_sample_rate": self._optional_int(source_audio.get("sample_rate")),
            "audio_channels": self._optional_int(source_audio.get("channels")),
        }
        manifest.metadata["extracted_audio"] = {
            "codec": "pcm_s16le",
            "sample_rate": self._sample_rate,
            "channels": self._channels,
        }
        self.report_progress(0.98, "Audio extracted")
        return manifest

    @staticmethod
    def _resolve_executable(command: str) -> str:
        command_path = Path(command).expanduser()
        if command_path.parent != Path("."):
            if command_path.is_file():
                return str(command_path.resolve())
            raise FFmpegNotFoundError(f"executable not found: {command_path}")

        resolved = shutil.which(command)
        if resolved is None:
            raise FFmpegNotFoundError(
                f"{command} was not found in PATH; install FFmpeg or pass an explicit path"
            )
        return resolved

    @staticmethod
    def _probe(ffprobe: str, source_path: Path) -> dict[str, Any]:
        command = [
            ffprobe,
            "-protocol_whitelist",
            "file",
            "-max_alloc",
            str(256 * 1024 * 1024),
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,codec_tag_string,width,height,avg_frame_rate,r_frame_rate,duration,sample_rate,channels:format=format_name,duration,size",
            "-of",
            "json",
            str(source_path),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "unknown FFprobe error"
            raise FFmpegExecutionError(f"media probe failed: {message}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise FFmpegExecutionError("FFprobe returned invalid JSON") from error

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return float(value) if value not in (None, "N/A") else None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return int(value) if value not in (None, "N/A") else None
