from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ru_en_dubber.content.text_localization import (
    LOCALIZABLE_TEXT_ROLES,
    contains_cyrillic,
    normalize_text_regions,
)
from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import (
    MediaSecurityError,
    resolve_job_path,
    validate_probe_payload,
)


class PremiereExportError(RuntimeError):
    """Raised when an editable Premiere package cannot be created."""


@dataclass(frozen=True, slots=True)
class Episode:
    index: int
    start: float
    end: float
    shot_indices: tuple[int, ...]
    path: Path | None = None
    timeline_start: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def sequence_start(self) -> float:
        return self.timeline_start if self.timeline_start is not None else self.start

    @property
    def sequence_end(self) -> float:
        return self.sequence_start + self.duration


@dataclass(frozen=True, slots=True)
class RenderBackend:
    device: str
    decoder: str
    encoder: str
    input_options: tuple[str, ...]
    output_options: tuple[str, ...]
    fallback_reason: str | None = None


class PremiereExportStage(PipelineStage):
    """Create replaceable video episodes and an importable FCP 7 XML timeline."""

    def __init__(
        self,
        *,
        python: str,
        ffmpeg: str,
        ffprobe: str,
        threshold: float = 3.0,
        minimum_shot_seconds: float = 0.4,
        minimum_episode_seconds: float = 2.0,
        maximum_episode_seconds: float = 15.0,
        codec: str = "h264",
        device: str = "auto",
        detection_timeout: float = 7200.0,
    ) -> None:
        if threshold <= 0:
            raise ValueError("scene threshold must be positive")
        if minimum_shot_seconds <= 0:
            raise ValueError("minimum shot duration must be positive")
        if minimum_episode_seconds <= 0:
            raise ValueError("minimum episode duration must be positive")
        if maximum_episode_seconds < minimum_episode_seconds:
            raise ValueError("maximum episode duration must not be below minimum")
        if codec not in {"h264", "prores"}:
            raise ValueError("Premiere codec must be h264 or prores")
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("Premiere render device must be auto, cuda or cpu")
        if not math.isfinite(detection_timeout) or detection_timeout <= 0:
            raise ValueError("scene detection timeout must be finite and positive")
        self._python = python
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._threshold = threshold
        self._minimum_shot_seconds = minimum_shot_seconds
        self._minimum_episode_seconds = minimum_episode_seconds
        self._maximum_episode_seconds = maximum_episode_seconds
        self._codec = codec
        self._device = device
        self._detection_timeout = float(detection_timeout)
        self._active_backend: RenderBackend | None = None

    @property
    def name(self) -> str:
        return "premiere_export"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        source_relative = (
            manifest.artifacts.get("avatar_base_video")
            if manifest.artifacts.get("avatar_overlay_video")
            else manifest.artifacts.get("source_video") or manifest.job.source_file
        )
        try:
            source = resolve_job_path(
                job_dir, source_relative, label="Premiere source video"
            )
        except MediaSecurityError as error:
            raise PremiereExportError(str(error)) from error
        if not source.is_file():
            raise PremiereExportError(f"source video not found: {source}")
        if not manifest.segments:
            raise PremiereExportError("Premiere export requires transcript segments")
        missing_audio = []
        for segment in manifest.segments:
            media_file = segment.dub_file or segment.tts_file
            if not media_file or not segment.tts_duration:
                missing_audio.append(segment.id)
                continue
            try:
                audio_path = resolve_job_path(
                    job_dir, media_file, label=f"dialogue audio {segment.id}"
                )
            except MediaSecurityError:
                missing_audio.append(segment.id)
                continue
            if not audio_path.is_file():
                missing_audio.append(segment.id)
        if missing_audio:
            raise PremiereExportError(
                f"Premiere export requires TTS audio for segment IDs: {missing_audio}"
            )

        python = self._resolve_executable(self._python, "Python")
        ffmpeg = self._resolve_executable(self._ffmpeg, "FFmpeg")
        ffprobe = self._resolve_executable(self._ffprobe, "FFprobe")
        export_dir = self._next_export_dir(job_dir)
        video_dir = export_dir / "video"
        voice_dir = export_dir / "voice"
        overlay_dir = export_dir / "overlays"
        video_dir.mkdir(parents=True, exist_ok=True)
        voice_dir.mkdir(parents=True, exist_ok=True)
        overlay_dir.mkdir(parents=True, exist_ok=True)

        self.report_progress(0.02, "Reading source video parameters…")
        media = self._probe(ffprobe, source)
        self._active_backend = self._select_render_backend(ffmpeg, source, media)
        detection_path = export_dir / "shot-detection.json"
        self.report_progress(0.05, "Local search for edit cuts…")
        probed_duration = float(media.get("format", {}).get("duration") or 0.0)
        detection = self._detect_shots(
            python,
            source,
            detection_path,
            source_duration=probed_duration,
        )
        raw_shots = detection.get("scenes")
        if not isinstance(raw_shots, list) or not raw_shots:
            raise PremiereExportError("scene detector returned no shots")
        source_duration = probed_duration or float(raw_shots[-1]["end_seconds"])
        removed_ranges = self._resolved_ad_ranges(
            manifest, raw_shots, source_duration
        )
        episodes = self._group_episodes(raw_shots)
        if removed_ranges:
            episodes = self._remove_episode_ranges(episodes, removed_ranges)
        if not episodes:
            raise PremiereExportError("ad removal would remove the complete video")
        export_segments = self._segments_for_export(manifest.segments, removed_ranges)
        export_manifest = replace(manifest, segments=export_segments)
        text_localizations = self._static_text_localizations(
            manifest, job_dir, removed_ranges
        )
        video_stream = next(
            (
                stream
                for stream in media.get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        frame_size = (
            int(video_stream.get("width") or 1920),
            int(video_stream.get("height") or 1080),
        )
        if text_localizations:
            self._active_backend = self._cpu_backend(
                fallback_reason="static text cleanup uses a CPU spatial filter"
            )

        self.report_progress(0.12, f"Episodes found: {len(episodes)}")
        assert self._active_backend is not None
        self.report_progress(
            None,
            "Rendering episodes: "
            f"{self._active_backend.decoder} decode → {self._active_backend.encoder}",
        )
        rendered = self._render_episodes(
            ffmpeg,
            source,
            video_dir,
            episodes,
            text_localizations=text_localizations,
            frame_size=frame_size,
        )
        dialogue_files = self._copy_dialogue(job_dir, voice_dir, export_segments)
        background_path = self._prepare_background(
            ffmpeg,
            job_dir,
            export_dir,
            manifest,
            source_duration,
            removed_ranges,
        )
        music_path = self._copy_music_bed(manifest, job_dir, export_dir)
        text_overlays = self._localized_text_overlays(text_localizations)
        text_report_path = export_dir / "text-localization.json"
        self._write_json(text_report_path, {
            "version": 1,
            "policy": "static-opaque-cover-with-margin",
            "items": text_localizations,
            "localized_count": len(text_overlays),
            "review_count": sum(
                item.get("cleanup_status") == "text_cleanup_review"
                for item in text_localizations
            ),
        })
        avatar_overlay = self._copy_avatar_overlay(manifest, job_dir, overlay_dir)

        self.report_progress(0.90, "Building tables and XML for Premiere…")
        self._write_scenes_csv(export_dir / "scenes.csv", rendered, export_segments)
        self._write_dialogue_csv(
            export_dir / "dialogue.csv", export_segments, dialogue_files, rendered
        )
        xml_path = export_dir / "premiere_sequence.xml"
        self._write_xml(
            xml_path,
            export_manifest,
            media,
            rendered,
            dialogue_files,
            background_path,
            max(episode.sequence_end for episode in rendered),
            music_path=music_path,
            text_overlays=text_overlays,
            avatar_overlay=avatar_overlay,
            visual_review_beats=self._visual_review_beats(manifest, job_dir),
        )
        self._write_instructions(export_dir / "README.txt")

        report_path = export_dir / "premiere-export.json"
        report = {
            "format": "premiere-editable-package",
            "video_mode": manifest.metadata.get("video_mode", "original"),
            "source_video": source.relative_to(job_dir.resolve()).as_posix(),
            "timeline": xml_path.name,
            "scene_detection": detection_path.name,
            "scenes_csv": "scenes.csv",
            "dialogue_csv": "dialogue.csv",
            "video_directory": "video",
            "voice_directory": "voice",
            "overlay_directory": "overlays",
            "text_localization_report": text_report_path.name,
            "localized_text_count": len(text_overlays),
            "text_cleanup_review_count": sum(
                item.get("cleanup_status") == "text_cleanup_review"
                for item in text_localizations
            ),
            "avatar_track": avatar_overlay.name if avatar_overlay else None,
            "background": background_path.name if background_path else None,
            "music_bed": music_path.name if music_path else None,
            "codec": self._codec,
            "requested_render_device": self._device,
            "render_device": self._active_backend.device,
            "video_decoder": self._active_backend.decoder,
            "video_encoder": self._active_backend.encoder,
            "render_fallback_reason": self._active_backend.fallback_reason,
            "raw_shot_count": len(raw_shots),
            "episode_count": len(rendered),
            "dialogue_count": len(dialogue_files),
            "removed_ad_ranges": removed_ranges,
            "removed_ad_seconds": round(
                sum(item["end"] - item["start"] for item in removed_ranges), 3
            ),
            "speakers": list(
                dict.fromkeys(segment.speaker for segment in export_segments)
            ),
            "speaker_track_count": len(
                self._allocate_speaker_tracks(export_segments)
            ),
            "timing_aligned": all(
                segment.dub_file is not None for segment in export_segments
            ),
            "threshold": self._threshold,
            "minimum_episode_seconds": self._minimum_episode_seconds,
            "maximum_episode_seconds": self._maximum_episode_seconds,
        }
        self._write_json(report_path, report)
        manifest.artifacts["premiere_export"] = str(
            report_path.relative_to(job_dir).as_posix()
        )
        manifest.artifacts["premiere_xml"] = str(xml_path.relative_to(job_dir).as_posix())
        manifest.artifacts["text_localization_report"] = str(
            text_report_path.relative_to(job_dir).as_posix()
        )
        manifest.metadata["premiere_export"] = report
        self.report_progress(
            0.99,
            f"Premiere package ready: {len(rendered)} episodes and "
            f"{len(dialogue_files)} lines",
        )
        return manifest

    def _detect_shots(
        self,
        python: str,
        source: Path,
        output: Path,
        *,
        source_duration: float = 0.0,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = source_root + (
            os.pathsep + environment["PYTHONPATH"]
            if environment.get("PYTHONPATH")
            else ""
        )
        command = [
            python,
            "-m",
            "ru_en_dubber.editing.detector",
            "--source",
            str(source),
            "--output",
            str(output),
            "--threshold",
            str(self._threshold),
            "--minimum-shot-seconds",
            str(self._minimum_shot_seconds),
        ]
        timeout = max(
            self._detection_timeout,
            source_duration * 12.0
            if math.isfinite(source_duration) and source_duration > 0
            else 0.0,
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise PremiereExportError(
                f"scene detection timed out after {timeout:.0f}s"
            ) from error
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise PremiereExportError(
                f"scene detection failed: {details[-4000:] or 'no error output'}"
            )
        try:
            return json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PremiereExportError("scene detector returned invalid JSON") from error

    def _group_episodes(self, shots: list[dict[str, Any]]) -> list[Episode]:
        pieces: list[tuple[float, float, int]] = []
        for fallback_index, shot in enumerate(shots, start=1):
            start = float(shot["start_seconds"])
            end = float(shot["end_seconds"])
            shot_index = int(shot.get("index", fallback_index))
            if end <= start:
                raise PremiereExportError("scene detector returned invalid timing")
            cursor = start
            while end - cursor > self._maximum_episode_seconds:
                pieces.append((cursor, cursor + self._maximum_episode_seconds, shot_index))
                cursor += self._maximum_episode_seconds
            pieces.append((cursor, end, shot_index))

        grouped: list[tuple[float, float, list[int]]] = []
        current_start: float | None = None
        current_end = 0.0
        current_shots: list[int] = []
        for start, end, shot_index in pieces:
            if current_start is None:
                current_start = start
            projected = end - current_start
            current_duration = current_end - current_start
            if (
                current_shots
                and current_duration >= self._minimum_episode_seconds
                and projected > self._maximum_episode_seconds
            ):
                grouped.append((current_start, current_end, current_shots))
                current_start = start
                current_shots = []
            current_end = end
            if shot_index not in current_shots:
                current_shots.append(shot_index)
            if current_end - current_start >= self._minimum_episode_seconds:
                grouped.append((current_start, current_end, current_shots))
                current_start = None
                current_end = 0.0
                current_shots = []
        if current_start is not None:
            if grouped and current_end - current_start < self._minimum_episode_seconds:
                previous_start, _, previous_shots = grouped.pop()
                grouped.append(
                    (previous_start, current_end, previous_shots + current_shots)
                )
            else:
                grouped.append((current_start, current_end, current_shots))
        return [
            Episode(index=index, start=start, end=end, shot_indices=tuple(shot_ids))
            for index, (start, end, shot_ids) in enumerate(grouped, start=1)
        ]

    @staticmethod
    def _resolved_ad_ranges(
        manifest: Manifest,
        shots: list[dict[str, Any]],
        source_duration: float,
        *,
        snap_tolerance: float = 3.0,
    ) -> list[dict[str, Any]]:
        report = manifest.metadata.get("ad_removal", {})
        if not isinstance(report, dict) or report.get("mode") != "remove":
            return []
        raw_ranges = report.get("ranges")
        if not isinstance(raw_ranges, list):
            return []
        boundaries = sorted(
            {
                max(0.0, min(source_duration, float(value)))
                for shot in shots
                for value in (shot.get("start_seconds"), shot.get("end_seconds"))
                if value is not None
            }
        )

        def snap_outward(value: float, *, is_start: bool) -> float:
            candidates = [
                boundary
                for boundary in boundaries
                if (boundary <= value if is_start else boundary >= value)
            ]
            if not candidates:
                return value
            closest = max(candidates) if is_start else min(candidates)
            return closest if abs(closest - value) <= snap_tolerance else value

        normalized: list[dict[str, Any]] = []
        for item in raw_ranges:
            if not isinstance(item, dict):
                continue
            try:
                expanded = PremiereExportStage._expand_ad_start_to_brand(
                    item, manifest.segments
                )
                start = snap_outward(
                    max(0.0, expanded), is_start=True
                )
                end = snap_outward(
                    min(source_duration, float(item["end"])), is_start=False
                )
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            normalized.append({**item, "start": start, "end": end})
        normalized.sort(key=lambda item: (float(item["start"]), float(item["end"])))

        merged: list[dict[str, Any]] = []
        for item in normalized:
            if merged and float(item["start"]) <= float(merged[-1]["end"]):
                merged[-1]["end"] = max(
                    float(merged[-1]["end"]), float(item["end"])
                )
                continue
            merged.append(dict(item))
        return merged

    @staticmethod
    def _expand_ad_start_to_brand(
        item: dict[str, Any],
        segments: list[Segment],
        *,
        lookback_seconds: float = 45.0,
        maximum_gap_seconds: float = 2.0,
    ) -> float:
        """Include adjacent sponsor-description lines omitted by the classifier."""
        start = float(item["start"])
        brand = re.sub(
            r"[^\w\d]+", "", str(item.get("brand") or "").casefold()
        )
        if len(brand) < 4:
            return start

        candidates = [
            segment
            for segment in segments
            if segment.end <= start
            and start - segment.start <= lookback_seconds
            and brand
            in re.sub(r"[^\w\d]+", "", segment.ru.casefold())
        ]
        expanded_start = start
        cursor = start
        for segment in sorted(candidates, key=lambda value: value.start, reverse=True):
            if cursor - segment.end > maximum_gap_seconds:
                break
            expanded_start = segment.start
            cursor = segment.start
        return expanded_start

    @classmethod
    def _remove_episode_ranges(
        cls, episodes: list[Episode], ranges: list[dict[str, Any]]
    ) -> list[Episode]:
        pieces: list[Episode] = []
        for episode in episodes:
            cursor = episode.start
            for item in ranges:
                cut_start = float(item["start"])
                cut_end = float(item["end"])
                if cut_end <= cursor or cut_start >= episode.end:
                    continue
                kept_end = min(cut_start, episode.end)
                if kept_end > cursor:
                    pieces.append(
                        Episode(
                            index=0,
                            start=cursor,
                            end=kept_end,
                            shot_indices=episode.shot_indices,
                            timeline_start=cursor - cls._removed_before(cursor, ranges),
                        )
                    )
                cursor = max(cursor, cut_end)
                if cursor >= episode.end:
                    break
            if cursor < episode.end:
                pieces.append(
                    Episode(
                        index=0,
                        start=cursor,
                        end=episode.end,
                        shot_indices=episode.shot_indices,
                        timeline_start=cursor - cls._removed_before(cursor, ranges),
                    )
                )
        return [replace(episode, index=index) for index, episode in enumerate(pieces, 1)]

    @classmethod
    def _segments_for_export(
        cls, segments: list[Segment], ranges: list[dict[str, Any]]
    ) -> list[Segment]:
        exported: list[Segment] = []
        for segment in segments:
            audio_start = (
                segment.dub_start if segment.dub_start is not None else segment.start
            )
            audio_duration = (
                segment.dub_duration
                if segment.dub_duration is not None
                else segment.tts_duration or segment.duration
            )
            audio_end = audio_start + audio_duration
            if any(
                audio_start < float(item["end"])
                and audio_end > float(item["start"])
                for item in ranges
            ):
                continue
            start = segment.start - cls._removed_before(segment.start, ranges)
            end = segment.end - cls._removed_before(segment.end, ranges)
            dub_start = segment.dub_start
            if dub_start is not None:
                dub_start -= cls._removed_before(dub_start, ranges)
            exported.append(
                replace(segment, start=start, end=end, dub_start=dub_start)
            )
        return exported

    @staticmethod
    def _removed_before(value: float, ranges: list[dict[str, Any]]) -> float:
        return sum(
            max(0.0, min(value, float(item["end"])) - float(item["start"]))
            for item in ranges
        )

    def _render_episodes(
        self,
        ffmpeg: str,
        source: Path,
        output_dir: Path,
        episodes: list[Episode],
        *,
        text_localizations: list[dict[str, Any]] | None = None,
        frame_size: tuple[int, int] | None = None,
    ) -> list[Episode]:
        rendered: list[Episode] = []
        backend = self._active_backend or self._cpu_backend()
        localizations = text_localizations or []
        width, height = frame_size or (1920, 1080)
        for localization in localizations:
            localization["cleanup_status"] = "pending"
            localization["_required_episode_ids"] = [
                episode.index
                for episode in episodes
                if episode.start < float(localization["source_end"])
                and episode.end > float(localization["source_start"])
            ]
            localization["_cleaned_episode_ids"] = []
        extension = ".mov" if self._codec == "prores" else ".mp4"
        for position, episode in enumerate(episodes, start=1):
            start_ms = round(episode.start * 1000)
            end_ms = round(episode.end * 1000)
            filename = (
                f"scene_{episode.index:04d}_{start_ms:09d}_{end_ms:09d}{extension}"
            )
            output = output_dir / filename
            episode_localizations = [
                item
                for item in localizations
                if episode.start < float(item["source_end"])
                and episode.end > float(item["source_start"])
            ]
            video_filters = self._text_cover_filters(
                episode, episode_localizations, width, height
            )
            result = self._run_episode(
                ffmpeg,
                source,
                output,
                episode,
                backend,
                video_filters=video_filters,
            )
            if (
                (result.returncode != 0 or not output.is_file() or output.stat().st_size == 0)
                and backend.device == "cuda"
            ):
                details = result.stderr.strip() or "GPU render did not create the clip"
                backend = self._cpu_backend(
                    fallback_reason=f"GPU render failed: {details[-1000:]}"
                )
                self._active_backend = backend
                result = self._run_episode(
                    ffmpeg,
                    source,
                    output,
                    episode,
                    backend,
                    video_filters=video_filters,
                )
            if (
                video_filters
                and (result.returncode != 0 or not output.is_file() or output.stat().st_size == 0)
            ):
                details = result.stderr.strip() or "static text cleanup failed"
                for localization in episode_localizations:
                    localization["cleanup_status"] = "text_cleanup_review"
                    localization["cleanup_reason"] = details[-1000:]
                result = self._run_episode(
                    ffmpeg, source, output, episode, self._cpu_backend()
                )
            if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                details = result.stderr.strip() or "FFmpeg did not create the clip"
                raise PremiereExportError(
                    f"episode {episode.index} export failed: {details[-4000:]}"
                )
            elif video_filters:
                for localization in episode_localizations:
                    if localization.get("cleanup_status") != "text_cleanup_review":
                        localization["_cleaned_episode_ids"].append(episode.index)
            rendered.append(replace(episode, path=output))
            self.report_progress(
                0.12 + 0.68 * position / len(episodes),
                f"Cut {position} of {len(episodes)} video episodes",
            )
        for localization in localizations:
            required = set(localization.pop("_required_episode_ids", []))
            cleaned = set(localization.pop("_cleaned_episode_ids", []))
            if localization.get("cleanup_status") == "text_cleanup_review":
                continue
            if required and required == cleaned:
                localization["cleanup_status"] = "passed"
                localization["cleanup_reason"] = (
                    "original text covered by an opaque padded panel"
                )
            else:
                localization["cleanup_status"] = "text_cleanup_review"
                localization["cleanup_reason"] = "cleanup interval did not map to rendered video"
        return rendered

    def _run_episode(
        self,
        ffmpeg: str,
        source: Path,
        output: Path,
        episode: Episode,
        backend: RenderBackend,
        *,
        video_filters: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # Input-side seeking avoids decoding the video from time zero for every clip.
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *backend.input_options,
            "-ss",
            f"{episode.start:.6f}",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-t",
            f"{episode.duration:.6f}",
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            *(["-vf", ",".join(video_filters)] if video_filters else []),
            *backend.output_options,
            str(output),
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(120.0, episode.duration * 12.0),
        )

    @staticmethod
    def _text_cover_filters(
        episode: Episode,
        localizations: list[dict[str, Any]],
        width: int,
        height: int,
    ) -> list[str]:
        filters: list[str] = []
        for item in localizations:
            bbox = item["bbox"]
            box_width_px = float(bbox["width"]) * width
            box_height_px = float(bbox["height"]) * height
            # Leave enough room around anti-aliased glyphs and imperfect model
            # boxes. Unlike delogo, drawbox does not blur or invent pixels.
            padding_x = max(12, round(box_width_px * 0.10), round(width * 0.004))
            padding_y = max(8, round(box_height_px * 0.18), round(height * 0.004))
            x = max(0, round(float(bbox["x"]) * width) - padding_x)
            y = max(0, round(float(bbox["y"]) * height) - padding_y)
            right = min(
                width,
                round((float(bbox["x"]) + float(bbox["width"])) * width)
                + padding_x,
            )
            bottom = min(
                height,
                round((float(bbox["y"]) + float(bbox["height"])) * height)
                + padding_y,
            )
            box_width = right - x
            box_height = bottom - y
            if box_width < 8 or box_height < 8:
                continue
            start = max(0.0, float(item["source_start"]) - episode.start)
            end = min(episode.duration, float(item["source_end"]) - episode.start)
            if end <= start:
                continue
            style = item.get("style") if isinstance(item.get("style"), dict) else {}
            panel_color = str(style.get("panel_color") or "#121416")
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", panel_color):
                panel_color = "#121416"
            filters.append(
                f"drawbox=x={x}:y={y}:w={box_width}:h={box_height}:"
                f"color=0x{panel_color[1:]}:t=fill:"
                f"enable='between(t,{start:.6f},{end:.6f})'"
            )
        return filters

    def _select_render_backend(
        self, ffmpeg: str, source: Path, media: dict[str, Any]
    ) -> RenderBackend:
        if self._codec == "prores" or self._device == "cpu":
            reason = (
                "ProRes export uses the CPU encoder"
                if self._codec == "prores" and self._device != "cpu"
                else None
            )
            return self._cpu_backend(fallback_reason=reason)

        video_stream = next(
            (
                stream
                for stream in media.get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            {},
        )
        source_codec = str(video_stream.get("codec_name") or "").lower()
        gpu_decode_codecs = {"h264", "hevc", "vp9", "mpeg2video", "vc1"}
        failures: list[str] = []

        if source_codec in gpu_decode_codecs:
            backend = self._cuda_backend(hardware_decode=True)
            error = self._probe_backend(ffmpeg, source, backend)
            if error is None:
                return backend
            failures.append(f"CUDA decode unavailable: {error}")

        backend = self._cuda_backend(hardware_decode=False)
        error = self._probe_backend(ffmpeg, source, backend)
        if error is None:
            reason = None
            if source_codec and source_codec not in gpu_decode_codecs:
                reason = f"{source_codec} decode is not supported by this CUDA path; using CPU decode"
            elif failures:
                reason = failures[-1]
            if reason is not None:
                backend = RenderBackend(
                    device=backend.device,
                    decoder=backend.decoder,
                    encoder=backend.encoder,
                    input_options=backend.input_options,
                    output_options=backend.output_options,
                    fallback_reason=reason,
                )
            return backend

        failures.append(f"NVENC unavailable: {error}")
        return self._cpu_backend(fallback_reason="; ".join(failures))

    @staticmethod
    def _probe_backend(
        ffmpeg: str, source: Path, backend: RenderBackend
    ) -> str | None:
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            *backend.input_options,
            "-ss",
            "0",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-map",
            "0:v:0",
            "-an",
            *backend.output_options,
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return "FFmpeg backend probe timed out"
        if result.returncode == 0:
            return None
        return (result.stderr.strip() or "FFmpeg backend probe failed")[-1000:]

    def _cpu_backend(self, fallback_reason: str | None = None) -> RenderBackend:
        if self._codec == "prores":
            options = (
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-pix_fmt",
                "yuv422p10le",
            )
        else:
            options = (
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            )
        return RenderBackend(
            device="cpu",
            decoder="software",
            encoder=options[1],
            input_options=(),
            output_options=options,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _cuda_backend(*, hardware_decode: bool) -> RenderBackend:
        return RenderBackend(
            device="cuda",
            decoder="cuda" if hardware_decode else "software",
            encoder="h264_nvenc",
            input_options=("-hwaccel", "cuda", "-hwaccel_output_format", "cuda")
            if hardware_decode
            else (),
            output_options=(
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p5",
                "-tune",
                "hq",
                "-rc",
                "vbr",
                "-cq",
                "18",
                "-b:v",
                "0",
                "-movflags",
                "+faststart",
            ),
        )

    @staticmethod
    def _copy_dialogue(
        job_dir: Path, output_dir: Path, segments: list[Segment]
    ) -> dict[int, Path]:
        copied: dict[int, Path] = {}
        for segment in segments:
            media_file = segment.dub_file or segment.tts_file
            assert media_file is not None
            try:
                source = resolve_job_path(
                    job_dir, media_file, label=f"dialogue audio {segment.id}"
                )
            except MediaSecurityError as error:
                raise PremiereExportError(str(error)) from error
            timeline_start = segment.dub_start if segment.dub_start is not None else segment.start
            filename = f"line_{segment.id:04d}_{round(timeline_start * 1000):09d}.wav"
            destination = output_dir / filename
            shutil.copy2(source, destination)
            copied[segment.id] = destination
        return copied

    @staticmethod
    def _prepare_background(
        ffmpeg: str,
        job_dir: Path,
        export_dir: Path,
        manifest: Manifest,
        source_duration: float,
        removed_ranges: list[dict[str, Any]],
    ) -> Path | None:
        relative = manifest.artifacts.get("background")
        if relative is None:
            return None
        try:
            source = resolve_job_path(job_dir, relative, label="background audio")
        except MediaSecurityError as error:
            raise PremiereExportError(str(error)) from error
        if not source.is_file():
            raise PremiereExportError(f"background audio not found: {source}")
        destination = export_dir / "background.wav"
        if not removed_ranges:
            shutil.copy2(source, destination)
            return destination

        kept: list[tuple[float, float]] = []
        cursor = 0.0
        for item in removed_ranges:
            start = float(item["start"])
            end = float(item["end"])
            if start > cursor:
                kept.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < source_duration:
            kept.append((cursor, source_duration))
        if not kept:
            raise PremiereExportError("ad removal would remove the complete background")

        filters = []
        labels = []
        for index, (start, end) in enumerate(kept):
            label = f"a{index}"
            filters.append(
                f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(
            "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
        )
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-protocol_whitelist",
                "file",
                "-i",
                str(source),
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
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
                "48000",
                "-ac",
                "2",
                str(destination),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(120.0, source_duration * 4.0),
        )
        if result.returncode != 0 or not destination.is_file():
            details = result.stderr.strip() or "FFmpeg did not create edited background"
            raise PremiereExportError(f"background ad removal failed: {details[-4000:]}")
        return destination

    @staticmethod
    def _copy_music_bed(
        manifest: Manifest, job_dir: Path, export_dir: Path
    ) -> Path | None:
        relative = manifest.artifacts.get("music_bed")
        if not relative:
            return None
        try:
            source = resolve_job_path(job_dir, relative, label="AI music bed")
        except MediaSecurityError as error:
            raise PremiereExportError(str(error)) from error
        if not source.is_file():
            raise PremiereExportError(f"AI music bed not found: {source}")
        destination = export_dir / "ai-music.wav"
        shutil.copy2(source, destination)
        credits_relative = manifest.artifacts.get("music_credits")
        if credits_relative:
            try:
                credits = resolve_job_path(job_dir, credits_relative, label="music credits")
                if credits.is_file():
                    shutil.copy2(credits, export_dir / "music-credits.json")
            except MediaSecurityError as error:
                raise PremiereExportError(str(error)) from error
        return destination

    @staticmethod
    def _overlapping_dialogue(episode: Episode, segments: list[Segment]) -> list[int]:
        return [
            segment.id
            for segment in segments
            if segment.start < episode.sequence_end
            and segment.end > episode.sequence_start
        ]

    def _write_scenes_csv(
        self, path: Path, episodes: list[Episode], segments: list[Segment]
    ) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "episode",
                    "file",
                    "start_seconds",
                    "end_seconds",
                    "timeline_start_seconds",
                    "timeline_end_seconds",
                    "duration_seconds",
                    "source_shots",
                    "dialogue_ids",
                ]
            )
            for episode in episodes:
                assert episode.path is not None
                writer.writerow(
                    [
                        episode.index,
                        episode.path.name,
                        f"{episode.start:.3f}",
                        f"{episode.end:.3f}",
                        f"{episode.sequence_start:.3f}",
                        f"{episode.sequence_end:.3f}",
                        f"{episode.duration:.3f}",
                        ",".join(map(str, episode.shot_indices)),
                        ",".join(
                            map(str, self._overlapping_dialogue(episode, segments))
                        ),
                    ]
                )

    def _write_dialogue_csv(
        self,
        path: Path,
        segments: list[Segment],
        files: dict[int, Path],
        episodes: list[Episode],
    ) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "line",
                    "speaker",
                    "file",
                    "source_start_seconds",
                    "source_end_seconds",
                    "tts_duration_seconds",
                    "timeline_start_seconds",
                    "timeline_duration_seconds",
                    "speed_factor",
                    "episode_ids",
                    "ru",
                    "en",
                ]
            )
            for segment in segments:
                episode_ids = [
                    episode.index
                    for episode in episodes
                    if segment.start < episode.sequence_end
                    and segment.end > episode.sequence_start
                ]
                writer.writerow(
                    [
                        segment.id,
                        segment.speaker,
                        files[segment.id].name,
                        f"{segment.start:.3f}",
                        f"{segment.end:.3f}",
                        f"{segment.tts_duration:.3f}",
                        f"{(segment.dub_start if segment.dub_start is not None else segment.start):.3f}",
                        f"{(segment.dub_duration if segment.dub_duration is not None else segment.tts_duration):.3f}",
                        f"{(segment.dub_speed if segment.dub_speed is not None else 1.0):.4f}",
                        ",".join(map(str, episode_ids)),
                        segment.ru,
                        segment.en or "",
                    ]
                )

    @classmethod
    def _static_text_localizations(
        cls,
        manifest: Manifest,
        job_dir: Path,
        removed_ranges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        relative = manifest.artifacts.get("episode_plan")
        if not relative:
            return []
        try:
            plan_path = resolve_job_path(job_dir, relative, label="localized text plan")
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            episodes = payload.get("episodes", [])
        except (MediaSecurityError, OSError, ValueError, TypeError):
            return []
        candidates: list[dict[str, Any]] = []
        for item in episodes if isinstance(episodes, list) else []:
            if not isinstance(item, dict) or item.get("action") != "localize_text":
                continue
            if item.get("text_cleanup_status") != "static_ready":
                continue
            if item.get("text_role") not in LOCALIZABLE_TEXT_ROLES:
                continue
            try:
                source_start = float(item["source_start"])
                source_end = float(item["source_end"])
            except (KeyError, TypeError, ValueError):
                continue
            if source_start < 0 or source_end <= source_start:
                continue
            # A localization that touches removed advertising is discarded in full.
            if any(
                source_start < float(ad["end"])
                and source_end > float(ad["start"])
                for ad in removed_ranges
            ):
                continue
            regions = item.get("text_regions")
            for region in normalize_text_regions(regions):
                if region.get("localizable") is not True:
                    continue
                source_text = str(region.get("detected_text") or "").strip()
                text = str(region.get("translated_text") or "").strip()
                bbox = region.get("bbox")
                if (
                    not contains_cyrillic(source_text)
                    or not text
                    or len(text) > 500
                    or not isinstance(bbox, dict)
                    or region.get("motion") != "static"
                    or region.get("background") != "simple"
                    or region.get("visible_in_all_frames") is not True
                    or region.get("confidence", 0.0) < 0.90
                ):
                    continue
                try:
                    normalized_bbox = {
                        key: float(bbox[key])
                        for key in ("x", "y", "width", "height")
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    normalized_bbox["x"] < 0
                    or normalized_bbox["y"] < 0
                    or normalized_bbox["width"] <= 0
                    or normalized_bbox["height"] <= 0
                    or normalized_bbox["x"] + normalized_bbox["width"] > 1
                    or normalized_bbox["y"] + normalized_bbox["height"] > 1
                ):
                    continue
                try:
                    episode_index = int(item.get("index") or 0)
                except (TypeError, ValueError):
                    episode_index = 0
                candidates.append({
                    "index": len(candidates) + 1,
                    "episode_index": episode_index,
                    "source_start": source_start,
                    "source_end": source_end,
                    "start": source_start - cls._removed_before(source_start, removed_ranges),
                    "end": source_end - cls._removed_before(source_end, removed_ranges),
                    "text": text,
                    "source_text": source_text[:500],
                    "text_role": str(item.get("text_role") or "editorial_overlay"),
                    "bbox": normalized_bbox,
                    "style": region["style"],
                    "cleanup_status": "pending",
                })
        candidates.sort(key=lambda value: (value["source_start"], value["source_end"]))
        merged: list[dict[str, Any]] = []
        for candidate in candidates:
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and cls._same_text_localization(previous, candidate)
                and candidate["source_start"] <= previous["source_end"] + 0.15
            ):
                previous["source_end"] = max(
                    previous["source_end"], candidate["source_end"]
                )
                previous["end"] = max(previous["end"], candidate["end"])
                continue
            merged.append(candidate)
        for index, item in enumerate(merged, start=1):
            item["index"] = index
        return merged

    @staticmethod
    def _same_text_localization(
        left: dict[str, Any], right: dict[str, Any], *, bbox_tolerance: float = 0.015
    ) -> bool:
        if " ".join(left["source_text"].casefold().split()) != " ".join(
            right["source_text"].casefold().split()
        ):
            return False
        if " ".join(left["text"].casefold().split()) != " ".join(
            right["text"].casefold().split()
        ):
            return False
        return all(
            abs(float(left["bbox"][key]) - float(right["bbox"][key]))
            <= bbox_tolerance
            for key in ("x", "y", "width", "height")
        )

    @staticmethod
    def _localized_text_overlays(
        localizations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return sorted(
            (
                dict(item)
                for item in localizations
                if item.get("cleanup_status") == "passed"
            ),
            key=lambda item: (item["start"], item["end"]),
        )

    @staticmethod
    def _copy_avatar_overlay(
        manifest: Manifest, job_dir: Path, output_dir: Path
    ) -> Path | None:
        relative = manifest.artifacts.get("avatar_overlay_video")
        if not relative:
            return None
        try:
            source = resolve_job_path(job_dir, relative, label="avatar overlay")
        except MediaSecurityError as error:
            raise PremiereExportError(str(error)) from error
        if not source.is_file():
            raise PremiereExportError(f"avatar overlay not found: {source}")
        output = output_dir / source.name
        shutil.copy2(source, output)
        return output

    def _write_xml(
        self,
        path: Path,
        manifest: Manifest,
        media: dict[str, Any],
        episodes: list[Episode],
        dialogue_files: dict[int, Path],
        background_path: Path | None,
        sequence_duration: float,
        *,
        music_path: Path | None = None,
        text_overlays: list[dict[str, Any]] | None = None,
        avatar_overlay: Path | None = None,
        visual_review_beats: list[dict[str, Any]] | None = None,
    ) -> None:
        video_stream = next(
            (
                stream
                for stream in media.get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            None,
        )
        if video_stream is None:
            raise PremiereExportError("source does not contain a video stream")
        fps = self._parse_rate(
            video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        )
        width = int(video_stream.get("width", 1920))
        height = int(video_stream.get("height", 1080))
        total_seconds = sequence_duration
        timebase = max(1, round(fps))
        ntsc = not math.isclose(fps, float(timebase), rel_tol=0.0, abs_tol=0.001)
        total_frames = max(1, round(total_seconds * fps))

        root = ET.Element("xmeml", version="4")
        sequence = ET.SubElement(root, "sequence", id="sequence-1")
        self._text(sequence, "name", f"{manifest.job.id} — editable dub")
        self._text(sequence, "duration", total_frames)
        self._add_rate(sequence, timebase, ntsc)
        media_node = ET.SubElement(sequence, "media")
        video_node = ET.SubElement(media_node, "video")
        format_node = ET.SubElement(video_node, "format")
        sample = ET.SubElement(format_node, "samplecharacteristics")
        self._add_rate(sample, timebase, ntsc)
        self._text(sample, "width", width)
        self._text(sample, "height", height)
        self._text(sample, "anamorphic", "FALSE")
        self._text(sample, "pixelaspectratio", "square")
        self._text(sample, "fielddominance", "none")
        video_track = ET.SubElement(video_node, "track")
        self._text(video_track, "name", "Source Video")
        self._text(video_track, "enabled", "TRUE")
        self._text(video_track, "locked", "FALSE")
        for episode in episodes:
            assert episode.path is not None
            start = round(episode.sequence_start * fps)
            duration = max(1, round(episode.duration * fps))
            end = start + duration
            clip = ET.SubElement(video_track, "clipitem", id=f"video-{episode.index}")
            self._text(clip, "name", episode.path.name)
            self._text(clip, "duration", duration)
            self._add_rate(clip, timebase, ntsc)
            self._text(clip, "start", start)
            self._text(clip, "end", end)
            self._text(clip, "in", 0)
            self._text(clip, "out", duration)
            file_node = ET.SubElement(clip, "file", id=f"video-file-{episode.index}")
            self._text(file_node, "name", episode.path.name)
            self._text(file_node, "pathurl", self._path_url(episode.path))
            self._text(file_node, "duration", duration)
            self._add_rate(file_node, timebase, ntsc)
            file_media = ET.SubElement(file_node, "media")
            file_video = ET.SubElement(file_media, "video")
            file_sample = ET.SubElement(file_video, "samplecharacteristics")
            self._add_rate(file_sample, timebase, ntsc)
            self._text(file_sample, "width", width)
            self._text(file_sample, "height", height)

        if text_overlays:
            text_track = ET.SubElement(video_node, "track")
            self._text(text_track, "name", "Localized Text")
            self._text(text_track, "enabled", "TRUE")
            self._text(text_track, "locked", "FALSE")
            for overlay in text_overlays:
                start = max(0, round(float(overlay["start"]) * fps))
                end = min(total_frames, round(float(overlay["end"]) * fps))
                if end <= start:
                    continue
                item = ET.SubElement(
                    text_track, "generatoritem", id=f"localized-text-{overlay['index']}"
                )
                self._text(item, "name", f"Localized Text {overlay['index']}")
                self._text(item, "duration", end - start)
                self._add_rate(item, timebase, ntsc)
                self._text(item, "start", start)
                self._text(item, "end", end)
                self._text(item, "in", 0)
                self._text(item, "out", end - start)
                self._text(item, "enabled", "TRUE")
                self._text(item, "anamorphic", "FALSE")
                self._text(item, "alphatype", "black")
                source_track = ET.SubElement(item, "sourcetrack")
                self._text(source_track, "mediatype", "video")
                effect = ET.SubElement(item, "effect")
                self._text(effect, "name", "Text")
                self._text(effect, "effectid", "Text")
                self._text(effect, "effectcategory", "Text")
                self._text(effect, "effecttype", "generator")
                self._text(effect, "mediatype", "video")
                bbox = overlay.get("bbox") or {}
                style = overlay.get("style") if isinstance(overlay.get("style"), dict) else {}
                center_x = float(bbox.get("x", 0.5)) + float(bbox.get("width", 0.0)) / 2
                center_y = float(bbox.get("y", 0.5)) + float(bbox.get("height", 0.0)) / 2
                font_name = {
                    "sans_serif": "Arial",
                    "condensed": "Arial Narrow",
                    "serif": "Georgia",
                    "monospace": "Consolas",
                }.get(str(style.get("font_family") or "sans_serif"), "Arial")
                font_size = self._localized_font_size(
                    str(overlay["text"]), bbox, width, height
                )
                alignment = {"left": 1, "center": 2, "right": 3}.get(
                    str(style.get("alignment") or "center"), 2
                )
                font_style = 2 if style.get("weight") in {"medium", "bold"} else 1
                self._add_generator_parameter(effect, "str", "Text", overlay["text"])
                self._add_generator_parameter(effect, "fontname", "Font", font_name)
                self._add_generator_parameter(effect, "fontsize", "Size", font_size)
                self._add_generator_parameter(effect, "fontstyle", "Style", font_style)
                self._add_generator_parameter(effect, "fontalign", "Alignment", alignment)
                self._add_generator_color_parameter(
                    effect, "fontcolor", "Font Color", str(style.get("text_color") or "#FFFFFF")
                )
                self._add_generator_point_parameter(
                    effect,
                    "origin",
                    "Origin",
                    center_x - 0.5,
                    center_y - 0.5,
                )
                comment = ET.SubElement(item, "comments")
                serialized_style = json.dumps(
                    style, ensure_ascii=False, sort_keys=True
                )
                self._text(
                    comment,
                    "mastercomment1",
                    f"Original: {overlay['source_text']} | style: {serialized_style}",
                )

        if avatar_overlay is not None:
            avatar_track = ET.SubElement(video_node, "track")
            self._text(avatar_track, "name", "Avatar")
            self._text(avatar_track, "enabled", "TRUE")
            self._text(avatar_track, "locked", "FALSE")
            avatar_meta = manifest.metadata.get("avatar", {})
            avatar_size = int(avatar_meta.get("size") or 360)
            position = str(avatar_meta.get("position") or "bottom-right")
            margin = int(avatar_meta.get("margin") or 48)
            center_x = margin + avatar_size / 2 if position.endswith("left") else width - margin - avatar_size / 2
            center_y = margin + avatar_size / 2 if position.startswith("top") else height - margin - avatar_size / 2
            avatar_fps = 25.0
            avatar_timebase = 25
            avatar_duration = max(1, round(total_seconds * avatar_fps))
            for episode in episodes:
                start = round(episode.sequence_start * fps)
                end = start + max(1, round(episode.duration * fps))
                source_in = max(0, round(episode.start * avatar_fps))
                source_out = min(avatar_duration, source_in + max(1, round(episode.duration * avatar_fps)))
                clip = ET.SubElement(avatar_track, "clipitem", id=f"avatar-{episode.index}")
                self._text(clip, "name", avatar_overlay.name)
                self._text(clip, "duration", avatar_duration)
                self._add_rate(clip, avatar_timebase, False)
                self._text(clip, "start", start)
                self._text(clip, "end", end)
                self._text(clip, "in", source_in)
                self._text(clip, "out", source_out)
                file_node = ET.SubElement(clip, "file", id=f"avatar-file-{episode.index}")
                self._text(file_node, "name", avatar_overlay.name)
                self._text(file_node, "pathurl", self._path_url(avatar_overlay))
                self._text(file_node, "duration", avatar_duration)
                self._add_rate(file_node, avatar_timebase, False)
                file_media = ET.SubElement(file_node, "media")
                file_video = ET.SubElement(file_media, "video")
                self._text(file_video, "alphatype", "straight")
                file_sample = ET.SubElement(file_video, "samplecharacteristics")
                self._add_rate(file_sample, avatar_timebase, False)
                self._text(file_sample, "width", avatar_size)
                self._text(file_sample, "height", avatar_size)
                self._add_basic_motion(clip, center_x, center_y)

        audio_node = ET.SubElement(media_node, "audio")
        if background_path is not None:
            background_track = ET.SubElement(audio_node, "track")
            self._text(background_track, "name", "Original Background")
            self._text(background_track, "enabled", "TRUE")
            self._text(background_track, "locked", "FALSE")
            self._add_audio_clip(
                background_track,
                clip_id="background-1",
                name=background_path.name,
                path=background_path,
                start=0,
                duration=total_frames,
                fps=fps,
                timebase=timebase,
                ntsc=ntsc,
                sample_rate=48_000,
                channels=2,
            )

        if music_path is not None:
            music_track = ET.SubElement(audio_node, "track")
            self._text(music_track, "name", "AI Music")
            self._text(music_track, "enabled", "TRUE")
            self._text(music_track, "locked", "FALSE")
            self._add_audio_clip(
                music_track,
                clip_id="ai-music-1",
                name=music_path.name,
                path=music_path,
                start=0,
                duration=total_frames,
                fps=fps,
                timebase=timebase,
                ntsc=ntsc,
                sample_rate=48_000,
                channels=2,
            )

        for track_index, (speaker, lane, track_segments) in enumerate(
            self._allocate_speaker_tracks(manifest.segments), start=1
        ):
            track = ET.SubElement(audio_node, "track")
            self._text(
                track,
                "name",
                f"Voice — {speaker}" + (f" ({lane})" if lane > 1 else ""),
            )
            self._text(track, "enabled", "TRUE")
            self._text(track, "locked", "FALSE")
            for segment in track_segments:
                timeline_start = (
                    segment.dub_start if segment.dub_start is not None else segment.start
                )
                timeline_duration = (
                    segment.dub_duration
                    if segment.dub_duration is not None
                    else segment.tts_duration or segment.duration
                )
                start = round(timeline_start * fps)
                duration = max(1, round(timeline_duration * fps))
                duration = min(duration, max(1, total_frames - start))
                self._add_audio_clip(
                    track,
                    clip_id=f"voice-{track_index}-{segment.id}",
                    name=dialogue_files[segment.id].name,
                    path=dialogue_files[segment.id],
                    start=start,
                    duration=duration,
                    fps=fps,
                    timebase=timebase,
                    ntsc=ntsc,
                    sample_rate=int(manifest.metadata.get("tts", {}).get("sample_rate") or 24_000),
                    channels=1,
                )

        for segment in manifest.segments:
            marker = ET.SubElement(sequence, "marker")
            self._text(marker, "name", f"Line {segment.id:04d}")
            comment = (f"Script: {segment.en or ''}" if manifest.metadata.get("input", {}).get("type") == "script"
                       else f"RU: {segment.ru}\nEN: {segment.en or ''}")
            self._text(marker, "comment", comment)
            self._text(marker, "in", round(segment.start * fps))
            self._text(marker, "out", round(segment.end * fps))

        for beat in visual_review_beats or []:
            start = max(0, min(total_frames - 1, round(beat["start"] * fps)))
            end = max(start + 1, min(total_frames, round(beat["end"] * fps)))
            marker = ET.SubElement(sequence, "marker")
            self._text(marker, "name", f"REPLACE VISUAL {beat['index']:04d}")
            self._text(marker, "comment", f"No verified media found. Narration: {beat['narration']}")
            self._text(marker, "in", start)
            self._text(marker, "out", end)

        ET.indent(root, space="  ")
        tree = ET.ElementTree(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _visual_review_beats(manifest: Manifest, job_dir: Path) -> list[dict[str, Any]]:
        if manifest.metadata.get("input", {}).get("type") != "script":
            return []
        relative = manifest.artifacts.get("visual_plan")
        if not relative:
            return []
        try:
            path = resolve_job_path(job_dir, relative, label="visual plan")
            payload = json.loads(path.read_text(encoding="utf-8"))
            beats = payload["beats"]
            if not isinstance(beats, list):
                raise ValueError("beats must be a list")
            review = []
            for item in beats:
                if item.get("status") != "no_safe_match":
                    continue
                index, start, end = int(item["index"]), float(item["start"]), float(item["end"])
                if index < 1 or not all(math.isfinite(value) for value in (start, end)) or end <= start:
                    raise ValueError("invalid visual review interval")
                review.append({"index": index, "start": start, "end": end,
                               "narration": str(item.get("narration") or "")[:500]})
            return review
        except (OSError, ValueError, TypeError, KeyError, MediaSecurityError) as error:
            raise PremiereExportError("script visual review plan is invalid") from error

    @staticmethod
    def _allocate_audio_tracks(segments: list[Segment]) -> list[list[Segment]]:
        tracks: list[list[Segment]] = []
        track_ends: list[float] = []
        for segment in sorted(
            segments,
            key=lambda item: (
                item.dub_start if item.dub_start is not None else item.start,
                item.id,
            ),
        ):
            audio_start = (
                segment.dub_start if segment.dub_start is not None else segment.start
            )
            audio_duration = (
                segment.dub_duration
                if segment.dub_duration is not None
                else segment.tts_duration or segment.duration
            )
            audio_end = audio_start + audio_duration
            for index, track_end in enumerate(track_ends):
                if track_end <= audio_start:
                    tracks[index].append(segment)
                    track_ends[index] = audio_end
                    break
            else:
                tracks.append([segment])
                track_ends.append(audio_end)
        return tracks

    @classmethod
    def _allocate_speaker_tracks(
        cls, segments: list[Segment]
    ) -> list[tuple[str, int, list[Segment]]]:
        """Keep every detected speaker on their own Premiere track group.

        A speaker gets an extra lane only when that speaker's own source files overlap.
        This preserves simultaneous dialogue without mixing different speakers together.
        """
        by_speaker: dict[str, list[Segment]] = {}
        for segment in sorted(segments, key=lambda item: (item.start, item.id)):
            by_speaker.setdefault(segment.speaker, []).append(segment)

        result: list[tuple[str, int, list[Segment]]] = []
        for speaker, speaker_segments in by_speaker.items():
            for lane, track_segments in enumerate(
                cls._allocate_audio_tracks(speaker_segments), start=1
            ):
                result.append((speaker, lane, track_segments))
        return result

    def _add_audio_clip(
        self,
        track: ET.Element,
        *,
        clip_id: str,
        name: str,
        path: Path,
        start: int,
        duration: int,
        fps: float,
        timebase: int,
        ntsc: bool,
        sample_rate: int,
        channels: int,
    ) -> None:
        clip = ET.SubElement(track, "clipitem", id=clip_id)
        self._text(clip, "name", name)
        self._text(clip, "duration", duration)
        self._add_rate(clip, timebase, ntsc)
        self._text(clip, "start", start)
        self._text(clip, "end", start + duration)
        self._text(clip, "in", 0)
        self._text(clip, "out", duration)
        file_node = ET.SubElement(clip, "file", id=f"file-{clip_id}")
        self._text(file_node, "name", name)
        self._text(file_node, "pathurl", self._path_url(path))
        self._text(file_node, "duration", duration)
        self._add_rate(file_node, timebase, ntsc)
        file_media = ET.SubElement(file_node, "media")
        audio = ET.SubElement(file_media, "audio")
        sample = ET.SubElement(audio, "samplecharacteristics")
        self._text(sample, "depth", 16)
        self._text(sample, "samplerate", sample_rate)
        self._text(audio, "channelcount", channels)

    def _add_basic_motion(
        self, clip: ET.Element, center_x: float, center_y: float
    ) -> None:
        filter_node = ET.SubElement(clip, "filter")
        effect = ET.SubElement(filter_node, "effect")
        self._text(effect, "name", "Basic Motion")
        self._text(effect, "effectid", "basic")
        self._text(effect, "effectcategory", "motion")
        self._text(effect, "effecttype", "motion")
        self._text(effect, "mediatype", "video")
        scale = ET.SubElement(effect, "parameter")
        self._text(scale, "parameterid", "scale")
        self._text(scale, "name", "Scale")
        self._text(scale, "value", "100")
        center = ET.SubElement(effect, "parameter")
        self._text(center, "parameterid", "center")
        self._text(center, "name", "Center")
        value = ET.SubElement(center, "value")
        self._text(value, "horiz", f"{center_x:.3f}")
        self._text(value, "vert", f"{center_y:.3f}")

    @staticmethod
    def _localized_font_size(
        text: str, bbox: dict[str, Any], width: int, height: int
    ) -> int:
        lines = text.splitlines() or [text]
        longest = max(1, max(len(line) for line in lines))
        box_width = max(1.0, float(bbox.get("width", 0.25)) * width)
        box_height = max(1.0, float(bbox.get("height", 0.08)) * height)
        by_height = box_height * 0.62 / max(1, len(lines))
        by_width = box_width / (longest * 0.58)
        return max(12, min(240, round(min(by_height, by_width))))

    def _add_generator_parameter(
        self, effect: ET.Element, parameter_id: str, name: str, value: object
    ) -> None:
        parameter = ET.SubElement(effect, "parameter")
        self._text(parameter, "parameterid", parameter_id)
        self._text(parameter, "name", name)
        self._text(parameter, "value", value)

    def _add_generator_color_parameter(
        self, effect: ET.Element, parameter_id: str, name: str, color: str
    ) -> None:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            color = "#FFFFFF"
        parameter = ET.SubElement(effect, "parameter")
        self._text(parameter, "parameterid", parameter_id)
        self._text(parameter, "name", name)
        value = ET.SubElement(parameter, "value")
        self._text(value, "alpha", 255)
        self._text(value, "red", int(color[1:3], 16))
        self._text(value, "green", int(color[3:5], 16))
        self._text(value, "blue", int(color[5:7], 16))

    def _add_generator_point_parameter(
        self,
        effect: ET.Element,
        parameter_id: str,
        name: str,
        horizontal: float,
        vertical: float,
    ) -> None:
        parameter = ET.SubElement(effect, "parameter")
        self._text(parameter, "parameterid", parameter_id)
        self._text(parameter, "name", name)
        value = ET.SubElement(parameter, "value")
        self._text(value, "horiz", f"{horizontal:.6f}")
        self._text(value, "vert", f"{vertical:.6f}")

    @staticmethod
    def _add_rate(parent: ET.Element, timebase: int, ntsc: bool) -> None:
        rate = ET.SubElement(parent, "rate")
        PremiereExportStage._text(rate, "timebase", timebase)
        PremiereExportStage._text(rate, "ntsc", "TRUE" if ntsc else "FALSE")

    @staticmethod
    def _text(parent: ET.Element, tag: str, value: object) -> ET.Element:
        child = ET.SubElement(parent, tag)
        child.text = str(value)
        return child

    @staticmethod
    def _parse_rate(value: Any) -> float:
        if value is None:
            raise PremiereExportError("video frame rate is unavailable")
        text = str(value)
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            if float(denominator) == 0:
                raise PremiereExportError("video frame rate is invalid")
            result = float(numerator) / float(denominator)
        else:
            result = float(text)
        if result <= 0:
            raise PremiereExportError("video frame rate is invalid")
        return result

    @staticmethod
    def _path_url(path: Path) -> str:
        resolved = path.resolve()
        if os.name == "nt":
            return "file://localhost/" + quote(resolved.as_posix(), safe="/:")
        return "file://localhost" + quote(resolved.as_posix(), safe="/")

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
            raise PremiereExportError(
                f"FFprobe failed: {result.stderr.strip() or 'unknown error'}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PremiereExportError("FFprobe returned invalid JSON") from error
        try:
            validate_probe_payload(
                payload,
                label="Premiere source video",
                require_video=True,
            )
        except MediaSecurityError as error:
            raise PremiereExportError(str(error)) from error
        return payload

    @staticmethod
    def _resolve_executable(command: str, label: str) -> str:
        candidate = Path(command).expanduser()
        if candidate.parent != Path("."):
            if candidate.is_file():
                return str(candidate.resolve())
            raise PremiereExportError(f"{label} not found: {candidate}")
        resolved = shutil.which(command)
        if resolved is None:
            raise PremiereExportError(f"{label} not found: {command}")
        return resolved

    @staticmethod
    def _next_export_dir(job_dir: Path) -> Path:
        first = job_dir / "premiere-export"
        if not first.exists():
            return first
        index = 2
        while True:
            candidate = job_dir / f"premiere-export-{index}"
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _write_instructions(path: Path) -> None:
        path.write_text(
            "Premiere editable dubbing package\n\n"
            "1. Open Premiere Pro and create a project.\n"
            "2. Choose File > Import and select premiere_sequence.xml.\n"
            "3. Open the imported sequence. Video episodes are on V1, translated "
            "editable screen text is on V2, and the avatar is on V3.\n"
            "4. Original Background, AI Music, and cloned Voice lines are on "
            "independent audio tracks.\n"
            "5. Replace any scene on V1 without moving the voice clips or overlays.\n"
            "6. If Premiere asks to relink media, point it at this folder.\n",
            encoding="utf-8",
        )
