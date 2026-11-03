"""Conservative, reversible mastering of generated speech before timing and export."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_path, validate_media_file


class SpeechPostprocessError(RuntimeError):
    """Speech could not be processed and verified without changing its timing."""


class SpeechPostprocessStage(PipelineStage):
    """Keep raw TTS, smooth dynamics, and export measured, peak-safe 48 kHz PCM."""

    CONDITIONING = (
        "highpass=f=65,"
        "deesser=i=0.15:m=0.25:f=0.5,"
        "acompressor=threshold=0.125:ratio=1.8:attack=12:release=100:makeup=1:knee=2.8"
    )

    def __init__(
        self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
        profile: str = "speech", target_lufs: float = -18.0,
        true_peak_db: float = -2.0, timeout: float = 180.0,
    ) -> None:
        if profile not in {"speech", "off"}:
            raise ValueError("speech postprocess profile must be speech or off")
        if not math.isfinite(target_lufs) or not -24 <= target_lufs <= -14:
            raise ValueError("speech target loudness must be between -24 and -14 LUFS")
        if not math.isfinite(true_peak_db) or not -6 <= true_peak_db <= -1:
            raise ValueError("speech true peak must be between -6 and -1 dBTP")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("speech postprocess timeout must be positive and finite")
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self.profile = profile
        self.target_lufs = target_lufs
        self.true_peak_db = true_peak_db
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "speech_postprocess"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if not manifest.segments or len({s.id for s in manifest.segments}) != len(manifest.segments):
            raise SpeechPostprocessError("speech processing requires unique TTS segments")
        sources = self._sources(manifest, job_dir)
        if self.profile == "off":
            # An explicit bypass on a previously mastered job restores raw files too.
            for segment in manifest.segments:
                segment.tts_file, segment.tts_duration = sources[segment.id]
            self._invalidate(manifest)
            manifest.metadata[self.name] = {"profile": "off", "enabled": False}
            return manifest

        ffmpeg = self._executable(self._ffmpeg)
        ffprobe = self._executable(self._ffprobe)
        relative_dir = f"speech-postprocess/{uuid4().hex}"
        output_dir = resolve_job_path(job_dir, relative_dir, label="processed speech directory")
        output_dir.mkdir(parents=True, exist_ok=False)
        results = []
        for index, segment in enumerate(manifest.segments, start=1):
            relative, _ = sources[segment.id]
            source = resolve_job_path(job_dir, relative, label=f"raw TTS {segment.id}")
            duration = self._probe(ffprobe, source)
            frames = round(duration * 48000)
            destination = output_dir / f"{segment.id:06d}.wav"
            result = self._process(ffmpeg, source, destination, frames)
            result.update(
                id=segment.id, source_file=relative,
                source_sha256=self._hash(source),
                output_file=destination.relative_to(job_dir).as_posix(),
                output_sha256=self._hash(destination),
            )
            results.append(result)
            self.report_progress(
                index / len(manifest.segments),
                f"Обработано реплик: {index} из {len(manifest.segments)}",
            )

        report = {
            "version": 1, "profile": self.profile, "target_lufs": self.target_lufs,
            "true_peak_limit_db": self.true_peak_db, "maximum_loudness_boost_db": 6,
            "conditioning": self.CONDITIONING, "sample_rate": 48000, "channels": 1,
            "speed_change": False, "trim_silence": False, "segments": results,
        }
        report_path = output_dir / "report.json"
        temporary = report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(report_path)
        # Commit paths only after every output and its report have passed verification.
        by_id = {item["id"]: item for item in results}
        for segment in manifest.segments:
            item = by_id[segment.id]
            segment.tts_file = item["output_file"]
            segment.tts_duration = item["duration_seconds"]
        self._invalidate(manifest)
        manifest.artifacts["speech_postprocess_report"] = report_path.relative_to(job_dir).as_posix()
        manifest.artifacts["speech_postprocess_audio"] = relative_dir
        manifest.metadata[self.name] = {
            "enabled": True, "profile": self.profile, "target_lufs": self.target_lufs,
            "true_peak_limit_db": self.true_peak_db, "segment_count": len(results),
            "sample_rate": 48000, "speed_change": False,
        }
        return manifest

    def _sources(self, manifest: Manifest, job_dir: Path) -> dict[int, tuple[str, float]]:
        # Prefer the provider's immutable raw report, never recursively master mastered WAVs.
        report_key = "tts_report" if "tts_report" in manifest.artifacts else "speech_postprocess_report"
        report_file = manifest.artifacts.get(report_key)
        if report_file:
            path = resolve_job_path(job_dir, report_file, label="raw speech source report")
            validate_media_file(path, label="speech source report", maximum_bytes=4 * 1024 * 1024)
            try:
                items = json.loads(path.read_text(encoding="utf-8"))["segments"]
                key = "path" if report_key == "tts_report" else "source_file"
                duration_key = "duration_seconds" if key == "path" else "source_duration_seconds"
                sources = {int(item["id"]): (str(item[key]), float(item[duration_key]))
                           for item in items}
                if len(sources) != len(items):
                    raise ValueError("duplicate source IDs")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise SpeechPostprocessError("invalid raw speech source report") from error
        else:
            sources = {s.id: (s.tts_file or "", s.tts_duration or 0) for s in manifest.segments}
        if set(sources) != {s.id for s in manifest.segments}:
            raise SpeechPostprocessError("raw speech source IDs do not match the job")
        for relative, duration in sources.values():
            if not relative or not math.isfinite(duration) or not 0 < duration <= 600:
                raise SpeechPostprocessError("invalid raw speech duration or path")
            path = resolve_job_path(job_dir, relative, label="raw TTS audio")
            validate_media_file(path, label="raw TTS audio", maximum_bytes=512 * 1024 * 1024)
        return sources

    def _probe(self, ffprobe: str, source: Path) -> float:
        output = self._run([
            ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-f", "wav",
            "-show_format", "-show_streams", "-of", "json", str(source),
        ])
        try:
            payload = json.loads(output.stdout)
            streams = payload["streams"]
            duration = float(payload["format"]["duration"])
            codecs = {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le", "pcm_u8"}
            valid = (
                len(streams) == 1 and streams[0]["codec_type"] == "audio"
                and streams[0]["codec_name"] in codecs
                and int(streams[0]["channels"]) in {1, 2}
                and 8000 <= int(streams[0]["sample_rate"]) <= 192000
                and math.isfinite(duration) and 0 < duration <= 600
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SpeechPostprocessError("invalid raw TTS audio probe") from error
        if not valid:
            raise SpeechPostprocessError("unsupported or excessive raw TTS WAV")
        return duration

    def _measure(self, ffmpeg: str, source: Path, conditioning: str = "anull") -> dict:
        normalization = (
            f"loudnorm=I={self.target_lufs}:TP={self.true_peak_db - 0.3}:LRA=7:print_format=json"
        )
        result = self._run([
            ffmpeg, "-nostdin", "-hide_banner", "-v", "info", "-protocol_whitelist", "file,pipe",
            "-f", "wav", "-i", str(source), "-map", "0:a:0", "-af",
            f"{conditioning},{normalization}", "-f", "null", "-",
        ])
        try:
            values, _ = json.JSONDecoder().raw_decode(result.stderr[result.stderr.rfind("{"):])
            return {key: self._finite(values[key]) for key in
                    ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        except (KeyError, ValueError, TypeError) as error:
            raise SpeechPostprocessError("FFmpeg returned invalid loudness measurements") from error

    def _process(self, ffmpeg: str, source: Path, destination: Path, frames: int) -> dict:
        before = self._measure(ffmpeg, source)
        conditioned = self._measure(ffmpeg, source, self.CONDITIONING)
        integrated = conditioned["input_i"]
        valid = all(value is not None for value in conditioned.values())
        if valid and integrated is not None:
            target = min(self.target_lufs, integrated + 6)
            offset = conditioned["target_offset"] if target == self.target_lufs else 0
            # Leave a small resampling margin below the user-facing true-peak ceiling.
            normalization = (
                f"loudnorm=I={max(-70, target)}:TP={self.true_peak_db - 0.3}:LRA=7:"
                f"measured_I={integrated}:measured_TP={conditioned['input_tp']}:"
                f"measured_LRA={conditioned['input_lra']}:"
                f"measured_thresh={conditioned['input_thresh']}:"
                f"offset={offset}:linear=true"
            )
            mode = "measured_two_pass"
        else:
            # Silence / ungated very short sounds must not receive an arbitrary loudness boost.
            ceiling = 10 ** ((self.true_peak_db - 0.3) / 20)
            normalization = (
                f"aresample=192000,alimiter=limit={ceiling}:level=false:latency=true"
            )
            mode = "peak_only_no_loudness_boost"
        temporary = destination.with_suffix(".tmp.wav")
        try:
            self._run([
                ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y",
                "-protocol_whitelist", "file,pipe", "-f", "wav", "-i", str(source),
                "-map", "0:a:0", "-af",
                f"{self.CONDITIONING},{normalization},aresample=48000,"
                f"apad=whole_len={frames},atrim=end_sample={frames}",
                "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(temporary),
            ])
            with wave.open(str(temporary), "rb") as recording:
                if (recording.getnframes() != frames or recording.getframerate() != 48000
                        or recording.getnchannels() != 1 or recording.getsampwidth() != 2):
                    raise SpeechPostprocessError("processed speech changed duration or format")
            after = self._measure(ffmpeg, temporary)
            if after["input_tp"] is not None and after["input_tp"] > self.true_peak_db + 0.15:
                raise SpeechPostprocessError("processed speech exceeds the true-peak ceiling")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "source_duration_seconds": frames / 48000, "duration_seconds": frames / 48000,
            "input": before, "conditioned": conditioned, "output": after,
            "normalization": mode,
        }

    def _run(self, command: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
            detail = str(getattr(error, "stderr", "") or str(error))[-1600:]
            raise SpeechPostprocessError(f"speech processing failed: {detail}") from error

    @staticmethod
    def _finite(value: Any) -> float | None:
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _hash(path: Path) -> str:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()

    @staticmethod
    def _executable(command: str) -> str:
        found = shutil.which(command)
        if found is None:
            raise SpeechPostprocessError(f"speech processing executable not found: {command}")
        return found

    @staticmethod
    def _invalidate(manifest: Manifest) -> None:
        stale = {
            "speech_postprocess", "timing_alignment", "script_timing", "short_narration_timing",
            "narration_export", "subtitle_export", "avatar_overlay", "music_bed", "premiere_export",
        }
        manifest.completed_stages = [stage for stage in manifest.completed_stages if stage not in stale]
        for segment in manifest.segments:
            segment.dub_file = segment.dub_start = segment.dub_duration = segment.dub_speed = None
        for key in (
            "speech_postprocess_report", "speech_postprocess_audio", "aligned_tts",
            "narration_audio", "subtitles_srt", "premiere_export", "premiere_xml",
            "music_report", "music_bed", "music_plan", "music_candidates", "music_credits",
            "avatar_video", "avatar_overlay_video", "avatar_base_video", "avatar_composite",
        ):
            manifest.artifacts.pop(key, None)
        for key in ("speech_postprocess", "timing_alignment", "script_timing", "avatar",
                    "music_bed", "premiere_export"):
            manifest.metadata.pop(key, None)
