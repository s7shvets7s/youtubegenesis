from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import (
    MediaSecurityError,
    validate_probe_payload,
)


class VisualRebuildError(RuntimeError):
    """Raised when a replacement video track cannot be built."""


@dataclass(frozen=True, slots=True)
class VisualBeat:
    index: int
    start: float
    end: float
    segment_ids: tuple[int, ...]
    narration: str
    search_query: str
    action: str = "replace_media"
    visual_intent: str = "abstract"
    subject: str = ""
    contains_author: bool = False
    episode_index: int | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


class VisualRebuildStage(PipelineStage):
    """Build a visual track through independent media providers."""

    _STOP_WORDS = {
        "about",
        "after",
        "again",
        "also",
        "among",
        "and",
        "are",
        "because",
        "been",
        "before",
        "being",
        "between",
        "both",
        "but",
        "can",
        "could",
        "did",
        "does",
        "during",
        "each",
        "even",
        "first",
        "for",
        "from",
        "had",
        "has",
        "have",
        "her",
        "here",
        "him",
        "his",
        "how",
        "into",
        "its",
        "just",
        "more",
        "most",
        "not",
        "now",
        "only",
        "other",
        "our",
        "out",
        "over",
        "same",
        "she",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "through",
        "too",
        "under",
        "very",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "will",
        "with",
        "would",
        "you",
    }
    _PEXELS_MEDIA_HOSTS = {"videos.pexels.com"}
    _MAX_STOCK_BYTES = 750 * 1024 * 1024
    _MAX_SOURCE_BYTES = 8 * 1024 * 1024 * 1024

    def __init__(
        self,
        *,
        ffmpeg: str,
        ffprobe: str,
        api_key: str | None = None,
        target_beat_seconds: float = 10.0,
        candidates_per_query: int = 6,
        provider_names: tuple[str, ...] = ("pexels", "pixabay", "wikimedia", "nara", "loc"),
        providers=None,
        planner_mode: str = "auto",
        planner_server: str | None = None,
        planner_model: Path | None = None,
        planner_gpu_layers: int = 99,
        requests_file: Path | None = None,
        fallback: str = "card",
        request_budget: int = 120,
        media_type_mode: str = "mixed",
        script_animation_mode: str = "off",
    ) -> None:
        if not 4.0 <= target_beat_seconds <= 30.0:
            raise ValueError("target visual beat duration must be between 4 and 30 seconds")
        if not 1 <= candidates_per_query <= 20:
            raise ValueError("stock candidate count must be between 1 and 20")
        if planner_mode not in {"auto", "legacy"}:
            raise ValueError("planner mode must be auto or legacy")
        if fallback not in {"card", "original"}:
            raise ValueError("fallback must be card or original")
        if not 1 <= request_budget <= 10000:
            raise ValueError("request budget must be between 1 and 10000")
        if media_type_mode not in {"image", "mixed", "video"}:
            raise ValueError("media type mode must be image, mixed, or video")
        if set(provider_names) - {"pexels", "pixabay", "wikimedia", "nara", "loc"}:
            raise ValueError("unknown media provider")
        self._provider_names = tuple(dict.fromkeys(provider_names))
        self._providers = providers
        self._planner_mode = planner_mode
        self._planner_server = planner_server
        self._planner_model = planner_model
        self._planner_gpu_layers = planner_gpu_layers
        self._requests_file = requests_file
        self._fallback = fallback
        self._request_budget = request_budget
        self._media_type_mode = media_type_mode
        self._script_animation_mode = script_animation_mode
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._api_key = api_key
        self._target_beat_seconds = target_beat_seconds
        self._candidates_per_query = candidates_per_query

    @property
    def name(self) -> str:
        return "visual_rebuild"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        from .engine import run
        return run(self, manifest, job_dir)

    @classmethod
    def build_beats(
        cls,
        segments: list[Segment],
        *,
        source_duration: float,
        target_seconds: float = 10.0,
    ) -> list[VisualBeat]:
        translated = sorted(
            (segment for segment in segments if segment.en and segment.en.strip()),
            key=lambda item: (item.start, item.id),
        )
        if not translated or source_duration <= 0:
            return []

        groups: list[list[Segment]] = []
        current: list[Segment] = []
        for segment in translated:
            if current:
                span = segment.end - current[0].start
                gap = segment.start - current[-1].end
                if span > target_seconds * 1.35 or gap > 4.0:
                    groups.append(current)
                    current = []
            current.append(segment)
            if current[-1].end - current[0].start >= target_seconds:
                groups.append(current)
                current = []
        if current:
            groups.append(current)

        starts = [0.0] + [max(0.0, group[0].start) for group in groups[1:]]
        beats: list[VisualBeat] = []
        for index, group in enumerate(groups, start=1):
            start = starts[index - 1]
            end = starts[index] if index < len(starts) else source_duration
            end = min(source_duration, max(end, start + 0.04))
            narration = " ".join(str(segment.en).strip() for segment in group)
            beats.append(
                VisualBeat(
                    index=index,
                    start=start,
                    end=end,
                    segment_ids=tuple(segment.id for segment in group),
                    narration=narration,
                    search_query=cls.build_search_query(narration),
                )
            )
        return [beat for beat in beats if beat.duration > 0]

    @classmethod
    def build_search_query(cls, narration: str, maximum_words: int = 7) -> str:
        words = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", narration)
        selected: list[str] = []
        seen: set[str] = set()
        for word in words:
            normalized = word.lower().strip("'-")
            if len(normalized) < 3 or normalized in cls._STOP_WORDS or normalized in seen:
                continue
            seen.add(normalized)
            selected.append(normalized)
            if len(selected) >= maximum_words:
                break
        if not selected:
            return "historical documentary"
        return " ".join(selected)

    @classmethod
    def build_episode_beats(
        cls, manifest: Manifest, episode_plan: dict[str, Any], *, source_duration: float
    ) -> list[VisualBeat]:
        """Build a gap-free source-coordinate timeline from the reviewed episode plan."""
        raw = episode_plan.get("episodes")
        if not isinstance(raw, list):
            raise VisualRebuildError("episode plan has no episodes")
        mode = str(episode_plan.get("source_mode") or manifest.metadata.get("video_mode") or "hybrid")
        segments = sorted(manifest.segments, key=lambda item: (item.start, item.id))
        entries: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise VisualRebuildError("episode plan contains an invalid episode")
            try:
                start, end = float(item["source_start"]), float(item["source_end"])
                action = str(item["action"])
            except (KeyError, TypeError, ValueError) as error:
                raise VisualRebuildError("episode plan contains an invalid timeline") from error
            if action not in {"keep", "localize_text", "replace_media", "replace_animation"}:
                raise VisualRebuildError("episode plan contains an invalid action")
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise VisualRebuildError("episode plan contains an invalid interval")
            entries.append(dict(item, source_start=max(0.0, start), source_end=min(source_duration, end)))
        entries.sort(key=lambda item: (item["source_start"], item["source_end"]))
        beats: list[VisualBeat] = []

        def append(start: float, end: float, action: str, item: dict[str, Any] | None = None) -> None:
            if end <= start:
                return
            overlapping = [segment for segment in segments if segment.start < end and segment.end > start]
            narration = " ".join(str(segment.en or "").strip() for segment in overlapping).strip()
            subject = str((item or {}).get("subject") or narration or "documentary context")[:500]
            intent = str((item or {}).get("visual_intent") or "original")
            beats.append(VisualBeat(index=len(beats) + 1, start=start, end=end,
                                    segment_ids=tuple(segment.id for segment in overlapping),
                                    narration=narration or subject,
                                    search_query=cls.build_search_query(subject), action=action,
                                    visual_intent=intent, subject=subject,
                                    contains_author=bool((item or {}).get("contains_author")),
                                    episode_index=int(item["index"]) if item and "index" in item else None))

        cursor = 0.0
        # Unclassified gaps are source footage, never implicit replacement work.
        gap_action = "keep"
        previous: dict[str, Any] | None = None
        for item in entries:
            start, end = item["source_start"], item["source_end"]
            if start < cursor - 0.01:
                raise VisualRebuildError("episode plan intervals overlap")
            if beats and 0 < start - cursor <= 0.04 + 1e-9:
                beats[-1] = replace(beats[-1], end=start)
            else:
                append(cursor, start, gap_action, previous or item)
            append(start, end, str(item["action"]), item)
            cursor = end
            previous = item
        append(cursor, source_duration, gap_action, previous)
        return beats

    @classmethod
    def _download_video(cls, url: str, destination: Path) -> None:
        from .transport import download
        try:
            download(url, destination, frozenset(cls._PEXELS_MEDIA_HOSTS), "video")
        except (RuntimeError, OSError, ValueError) as error:
            raise VisualRebuildError(str(error)) from error

    @staticmethod
    def _render_beat(
        ffmpeg: str,
        source: Path,
        output: Path,
        *,
        duration: float,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        filter_chain = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps:.6f},format=yuv420p"
        )
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-protocol_whitelist",
                "file",
                "-stream_loop",
                "-1",
                "-i",
                str(source),
                "-t",
                f"{duration:.6f}",
                "-an",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-vf",
                filter_chain,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-video_track_timescale",
                "90000",
                "-movflags",
                "+faststart",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(120.0, duration * 12.0),
        )
        if result.returncode != 0 or not output.is_file():
            raise VisualRebuildError(
                "FFmpeg failed to prepare a stock clip: "
                + (result.stderr.strip()[-2000:] or "no output")
            )

    @staticmethod
    def _concat_clips(
        ffmpeg: str,
        clips: list[Path],
        output: Path,
        *,
        durations: list[float] | None = None,
    ) -> None:
        if durations is not None and len(durations) != len(clips):
            raise VisualRebuildError("concat duration count does not match clip count")
        concat_path = output.parent / "concat.txt"
        lines = []
        for index, clip in enumerate(clips):
            try:
                relative = clip.resolve().relative_to(output.parent.resolve())
            except ValueError as error:
                raise VisualRebuildError(
                    "rebuilt clip escaped its output directory"
                ) from error
            escaped = relative.as_posix().replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
            if durations is not None:
                duration = float(durations[index])
                if not math.isfinite(duration) or duration <= 0:
                    raise VisualRebuildError("concat contains an invalid clip duration")
                # The demuxer otherwise derives each next timestamp from the
                # rounded MP4 duration, accumulating drift over hundreds of clips.
                lines.append(f"duration {duration:.9f}")
        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "1",
                "-protocol_whitelist",
                "file",
                "-i",
                str(concat_path),
                "-an",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=1800,
        )
        if result.returncode != 0 or not output.is_file():
            raise VisualRebuildError(
                "FFmpeg failed to concatenate the rebuilt video: "
                + (result.stderr.strip()[-2000:] or "no output")
            )

    @staticmethod
    def _probe(ffprobe: str, source: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
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
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise VisualRebuildError(
                f"FFprobe failed: {result.stderr.strip() or 'unknown error'}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise VisualRebuildError("FFprobe returned invalid JSON") from error

    @staticmethod
    def _validate_media(
        media: dict[str, Any], *, label: str, strict_stock: bool
    ) -> None:
        try:
            validate_probe_payload(
                media,
                label=label,
                require_video=True,
                maximum_duration=1_200.0 if strict_stock else 21_600.0,
                allowed_video_codecs={"h264", "hevc"} if strict_stock else None,
                allowed_format_tokens={"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
                if strict_stock
                else None,
            )
        except MediaSecurityError as error:
            raise VisualRebuildError(str(error)) from error

    @staticmethod
    def _video_properties(media: dict[str, Any]) -> tuple[int, int, float, float]:
        video = next(
            (stream for stream in media.get("streams", []) if stream.get("codec_type") == "video"),
            None,
        )
        if not isinstance(video, dict):
            raise VisualRebuildError("video stream is unavailable")
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0")
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            fps = float(numerator) / float(denominator)
        else:
            fps = float(rate)
        duration = float(media.get("format", {}).get("duration") or video.get("duration") or 0)
        if width <= 0 or height <= 0 or fps <= 0 or duration <= 0:
            raise VisualRebuildError("source video properties are incomplete")
        return width, height, fps, duration

    @staticmethod
    def _resolve_executable(command: str, label: str) -> str:
        candidate = Path(command).expanduser()
        if candidate.parent != Path("."):
            if candidate.is_file():
                return str(candidate.resolve())
            raise VisualRebuildError(f"{label} not found: {candidate}")
        resolved = shutil.which(command)
        if resolved is None:
            raise VisualRebuildError(f"{label} not found: {command}")
        return resolved
