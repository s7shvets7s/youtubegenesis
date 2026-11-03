from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_path


class DialogueTimingError(RuntimeError):
    """Raised when generated dialogue cannot be prepared for the timeline."""


@dataclass(frozen=True, slots=True)
class DialoguePlacement:
    segment_id: int
    start: float
    duration: float
    speed: float
    block: int
    needs_rewrite: bool


class DialogueTimingStage(PipelineStage):
    """Trim and pace generated speech while preserving real source pauses."""

    def __init__(
        self,
        *,
        ffmpeg: str,
        ffprobe: str,
        natural_pause_seconds: float = 0.75,
        minimum_speed: float = 0.75,
        maximum_speed: float = 1.20,
        maximum_anchor_drift_seconds: float = 2.0,
        silence_threshold_db: float = -50.0,
        command_timeout: float = 300.0,
        probe_timeout: float = 60.0,
    ) -> None:
        if natural_pause_seconds <= 0:
            raise ValueError("natural pause threshold must be positive")
        if not 0.5 <= minimum_speed <= 1.0:
            raise ValueError("minimum speed must be between 0.5 and 1")
        if not 1.0 <= maximum_speed <= 2.0:
            raise ValueError("maximum speed must be between 1 and 2")
        if maximum_anchor_drift_seconds <= 0:
            raise ValueError("maximum anchor drift must be positive")
        if silence_threshold_db >= 0:
            raise ValueError("silence threshold must be negative")
        if not math.isfinite(command_timeout) or command_timeout <= 0:
            raise ValueError("command timeout must be a positive finite number")
        if not math.isfinite(probe_timeout) or probe_timeout <= 0:
            raise ValueError("probe timeout must be a positive finite number")
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._natural_pause_seconds = natural_pause_seconds
        self._minimum_speed = minimum_speed
        self._maximum_speed = maximum_speed
        self._maximum_anchor_drift_seconds = maximum_anchor_drift_seconds
        self._silence_threshold_db = silence_threshold_db
        self._command_timeout = command_timeout
        self._probe_timeout = probe_timeout

    @property
    def name(self) -> str:
        return "timing_alignment"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if not manifest.segments:
            raise DialogueTimingError("timing alignment requires transcript segments")
        missing: list[int] = []
        sources: dict[int, Path] = {}
        for segment in manifest.segments:
            if not segment.tts_file or not segment.tts_duration:
                missing.append(segment.id)
                continue
            source = resolve_job_path(
                job_dir,
                segment.tts_file,
                label=f"TTS audio for segment {segment.id}",
            )
            if not source.is_file():
                missing.append(segment.id)
                continue
            sources[segment.id] = source
        if missing:
            raise DialogueTimingError(
                f"timing alignment requires TTS audio for segment IDs: {missing}"
            )

        ffmpeg = self._resolve_executable(self._ffmpeg, "FFmpeg")
        ffprobe = self._resolve_executable(self._ffprobe, "FFprobe")
        output_dir = job_dir / "aligned-tts"
        output_dir.mkdir(parents=True, exist_ok=True)

        self.report_progress(0.02, "Обрезка краевой тишины в репликах…")
        trimmed: dict[int, Path] = {}
        durations: dict[int, float] = {}
        for index, segment in enumerate(manifest.segments, start=1):
            source = sources[segment.id]
            destination = output_dir / f".{segment.id:06d}.trimmed.wav"
            self._trim_edges(ffmpeg, source, destination)
            duration = self._probe_duration(ffprobe, destination)
            if duration <= 0:
                raise DialogueTimingError(
                    f"trimmed TTS audio is empty for segment {segment.id}"
                )
            trimmed[segment.id] = destination
            durations[segment.id] = duration
            self.report_progress(
                0.02 + 0.30 * index / len(manifest.segments),
                f"Очищено реплик: {index} из {len(manifest.segments)}",
            )

        placements = self.plan(manifest.segments, durations)
        by_id = {placement.segment_id: placement for placement in placements}
        self.report_progress(0.34, "Выравнивание темпа речевых блоков…")
        actual_durations: dict[int, float] = {}
        for index, segment in enumerate(manifest.segments, start=1):
            placement = by_id[segment.id]
            destination = output_dir / f"{segment.id:06d}.wav"
            self._change_speed(
                ffmpeg,
                trimmed[segment.id],
                destination,
                placement.speed,
            )
            actual_durations[segment.id] = self._probe_duration(ffprobe, destination)
            trimmed[segment.id].unlink(missing_ok=True)
            segment.dub_file = destination.relative_to(job_dir).as_posix()
            segment.dub_start = placement.start
            segment.dub_duration = actual_durations[segment.id]
            segment.dub_speed = placement.speed
            self.report_progress(
                0.34 + 0.62 * index / len(manifest.segments),
                f"Выровнено реплик: {index} из {len(manifest.segments)}",
            )

        # Rebuild starts from measured output durations so rounding and codec padding
        # can never create overlaps between lines belonging to the same speaker.
        self._apply_measured_starts(manifest.segments)
        rewrite_ids = [item.segment_id for item in placements if item.needs_rewrite]
        speaker_count = len({segment.speaker for segment in manifest.segments})
        manifest.artifacts["aligned_tts"] = output_dir.name
        manifest.completed_stages = [
            stage for stage in manifest.completed_stages
            if stage not in {"avatar_overlay", "music_bed", "premiere_export"}
        ]
        manifest.artifacts.pop("premiere_export", None)
        manifest.artifacts.pop("premiere_xml", None)
        manifest.metadata.pop("premiere_export", None)
        manifest.metadata.pop("music_bed", None)
        manifest.metadata.pop("avatar", None)
        for artifact in ("avatar_video", "avatar_overlay_video", "avatar_base_video", "avatar_composite"):
            manifest.artifacts.pop(artifact, None)
        for artifact in ("music_report", "music_bed", "music_plan", "music_candidates", "music_credits"):
            manifest.artifacts.pop(artifact, None)
        manifest.metadata["timing_alignment"] = {
            "provider": "ffmpeg-atempo",
            "natural_pause_seconds": self._natural_pause_seconds,
            "minimum_speed": self._minimum_speed,
            "maximum_speed": self._maximum_speed,
            "maximum_anchor_drift_seconds": self._maximum_anchor_drift_seconds,
            "silence_threshold_db": self._silence_threshold_db,
            "segment_count": len(placements),
            "speaker_count": speaker_count,
            "needs_rewrite_segment_ids": rewrite_ids,
        }
        self.report_progress(
            0.99,
            f"Тайминг выровнен; требуют переписывания: {len(rewrite_ids)}",
        )
        return manifest

    def plan(
        self, segments: list[Segment], durations: dict[int, float]
    ) -> list[DialoguePlacement]:
        placements: list[DialoguePlacement] = []
        block_number = 0
        by_speaker: dict[str, list[Segment]] = {}
        for segment in sorted(segments, key=lambda item: (item.start, item.id)):
            by_speaker.setdefault(segment.speaker, []).append(segment)

        for speaker_segments in by_speaker.values():
            previous_end = 0.0
            for block in self._timing_blocks(speaker_segments, durations):
                block_number += 1
                original_speech = sum(segment.duration for segment in block)
                generated_speech = sum(durations[segment.id] for segment in block)
                requested_speed = generated_speech / original_speech
                speed = min(
                    self._maximum_speed,
                    max(self._minimum_speed, requested_speed),
                )
                needs_rewrite = not self._minimum_speed <= requested_speed <= self._maximum_speed
                cursor = max(block[0].start, previous_end)
                for index, segment in enumerate(block):
                    if index:
                        source_gap = max(0.0, segment.start - block[index - 1].end)
                        cursor += source_gap
                    output_duration = durations[segment.id] / speed
                    placements.append(
                        DialoguePlacement(
                            segment_id=segment.id,
                            start=cursor,
                            duration=output_duration,
                            speed=speed,
                            block=block_number,
                            needs_rewrite=needs_rewrite,
                        )
                    )
                    cursor += output_duration
                previous_end = cursor
        return sorted(placements, key=lambda item: item.segment_id)

    def _timing_blocks(
        self, segments: list[Segment], durations: dict[int, float]
    ) -> list[list[Segment]]:
        natural_runs: list[list[Segment]] = []
        current: list[Segment] = []
        for segment in segments:
            if current and segment.start - current[-1].end >= self._natural_pause_seconds:
                natural_runs.append(current)
                current = []
            current.append(segment)
        if current:
            natural_runs.append(current)

        blocks: list[list[Segment]] = []
        for run in natural_runs:
            offset = 0
            while offset < len(run):
                valid_ends: list[int] = []
                for end in range(offset, len(run)):
                    candidate = run[offset : end + 1]
                    speed, drift = self._block_metrics(candidate, durations)
                    if (
                        self._minimum_speed <= speed <= self._maximum_speed
                        and drift <= self._maximum_anchor_drift_seconds
                    ):
                        valid_ends.append(end)
                chosen_end = max(valid_ends) if valid_ends else offset
                blocks.append(run[offset : chosen_end + 1])
                offset = chosen_end + 1
        return blocks

    @staticmethod
    def _block_metrics(
        block: list[Segment], durations: dict[int, float]
    ) -> tuple[float, float]:
        original_speech = sum(segment.duration for segment in block)
        generated_speech = sum(durations[segment.id] for segment in block)
        speed = generated_speech / original_speech
        cursor = block[0].start
        maximum_drift = 0.0
        for index, segment in enumerate(block):
            maximum_drift = max(maximum_drift, abs(cursor - segment.start))
            cursor += durations[segment.id] / speed
            if index + 1 < len(block):
                cursor += max(0.0, block[index + 1].start - segment.end)
        return speed, maximum_drift

    @staticmethod
    def _apply_measured_starts(segments: list[Segment]) -> None:
        by_speaker: dict[str, list[Segment]] = {}
        for segment in sorted(segments, key=lambda item: (item.dub_start or item.start, item.id)):
            by_speaker.setdefault(segment.speaker, []).append(segment)
        for speaker_segments in by_speaker.values():
            previous: Segment | None = None
            for segment in speaker_segments:
                if segment.dub_start is None or segment.dub_duration is None:
                    continue
                if previous is not None:
                    assert previous.dub_start is not None
                    assert previous.dub_duration is not None
                    segment.dub_start = max(
                        segment.dub_start,
                        previous.dub_start + previous.dub_duration,
                    )
                previous = segment

    def _trim_edges(self, ffmpeg: str, source: Path, destination: Path) -> None:
        threshold = f"{self._silence_threshold_db:g}dB"
        audio_filter = (
            "silenceremove=start_periods=1:start_duration=0.02:"
            f"start_threshold={threshold},"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.02:"
            f"start_threshold={threshold},"
            "areverse"
        )
        self._run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-af",
                audio_filter,
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            "could not trim TTS audio",
        )

    def _change_speed(
        self, ffmpeg: str, source: Path, destination: Path, speed: float
    ) -> None:
        self._run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-af",
                f"atempo={speed:.8f}",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            "could not adjust TTS speed",
        )

    def _probe_duration(self, ffprobe: str, path: Path) -> float:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self._probe_timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise DialogueTimingError(
                f"FFprobe timed out after {self._probe_timeout:.0f} seconds for {path}"
            ) from error
        if result.returncode != 0:
            raise DialogueTimingError(
                f"could not read TTS duration: {result.stderr.strip() or path}"
            )
        try:
            duration = float(json.loads(result.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DialogueTimingError(f"invalid FFprobe output for {path}") from error
        return duration

    def _run(self, command: list[str], message: str) -> None:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self._command_timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise DialogueTimingError(
                f"{message}: timed out after {self._command_timeout:.0f} seconds"
            ) from error
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise DialogueTimingError(f"{message}: {details}")

    @staticmethod
    def _resolve_executable(command: str, label: str) -> str:
        command_path = Path(command).expanduser()
        if command_path.parent != Path("."):
            if command_path.is_file():
                return str(command_path.resolve())
            raise DialogueTimingError(f"{label} executable not found: {command_path}")
        resolved = shutil.which(command)
        if resolved is None:
            raise DialogueTimingError(f"{label} executable not found: {command}")
        return resolved
