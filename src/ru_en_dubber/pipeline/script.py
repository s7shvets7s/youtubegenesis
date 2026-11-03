from __future__ import annotations

import math
import re
import shutil
import struct
import subprocess
import sys
import wave
from array import array
from pathlib import Path
from uuid import uuid4

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_directory, resolve_job_path


MAX_SCRIPT_BYTES = 128_000
MAX_SEGMENT_CHARS = 400
SENTENCE_PAUSE_SECONDS = 0.25
PARAGRAPH_PAUSE_SECONDS = 0.60
SRT_CUE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)


def _srt_seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise ValueError("SRT timecode has an invalid minute or second")
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000)


def _parse_timed_script(text: str) -> list[Segment]:
    segments: list[Segment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and lines[0].isdigit():
            lines.pop(0)
        if len(lines) < 2 or not (match := SRT_CUE.fullmatch(lines[0])):
            raise ValueError("timed script must contain valid SRT cues")
        start = _srt_seconds(match["start"])
        end = _srt_seconds(match["end"])
        if end <= start or (segments and start < segments[-1].end):
            raise ValueError("timed script cues must be positive and non-overlapping")
        spoken = " ".join(lines[1:])
        if not spoken or len(spoken) > MAX_SEGMENT_CHARS:
            raise ValueError("timed script cue text is empty or too long")
        segments.append(Segment(len(segments) + 1, start, end, spoken, en=spoken))
    if not segments:
        raise ValueError("timed script has no cues")
    return segments


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{fraction:03d}"


def _read_narration_clip(path: Path) -> tuple[tuple[int, int, int], bytes]:
    """Read PCM WAV or Chatterbox's IEEE float32 WAV as PCM for joining."""
    if path.stat().st_size > 256_000_000:
        raise ValueError("narration clip is too large")
    try:
        with wave.open(str(path), "rb") as clip:
            if clip.getcomptype() != "NONE":
                raise ValueError("narration WAV must contain uncompressed audio")
            clip_format = (clip.getnchannels(), clip.getsampwidth(), clip.getframerate())
            return clip_format, clip.readframes(clip.getnframes())
    except wave.Error as error:
        if "unknown format: 3" not in str(error):
            raise ValueError(f"unsupported narration WAV: {path.name}") from error

    with path.open("rb") as source:
        header = source.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("invalid float narration WAV")
        fmt: tuple[int, int, int] | None = None
        raw: bytes | None = None
        while chunk_header := source.read(8):
            if len(chunk_header) != 8:
                raise ValueError("truncated narration WAV chunk")
            chunk_name, size = struct.unpack("<4sI", chunk_header)
            if size > 256_000_000:
                raise ValueError("narration WAV chunk is too large")
            payload = source.read(size)
            if len(payload) != size:
                raise ValueError("truncated narration WAV data")
            if size % 2:
                source.read(1)
            if chunk_name == b"fmt ":
                if size < 16:
                    raise ValueError("invalid narration WAV format")
                codec, channels, sample_rate, _, _, bits = struct.unpack("<HHIIHH", payload[:16])
                if codec != 3 or channels < 1 or channels > 2 or bits != 32:
                    raise ValueError("unsupported float narration WAV format")
                fmt = channels, 2, sample_rate
            elif chunk_name == b"data":
                raw = payload
        if fmt is None or raw is None or len(raw) % 4:
            raise ValueError("invalid float narration WAV data")
        samples = array("h")
        for (value,) in struct.iter_unpack("<f", raw):
            bounded = max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0
            samples.append(round(bounded * 32767))
        if sys.byteorder != "little":
            samples.byteswap()
        return fmt, samples.tobytes()


def _clean_markdown(text: str) -> str:
    """Remove common formatting marks while leaving narration and punctuation intact."""
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("__", "").replace("`", "")


def _split_long_sentence(sentence: str) -> list[str]:
    if len(sentence) <= MAX_SEGMENT_CHARS:
        return [sentence]
    words = sentence.split()
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        if len(word) > MAX_SEGMENT_CHARS:
            raise ValueError("script contains a word too long for narration")
        if current and len(" ".join((*current, word))) > MAX_SEGMENT_CHARS:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _segment_script(text: str) -> tuple[list[str], list[int]]:
    paragraphs = re.split(r"\n\s*\n+", text.strip())
    lines: list[str] = []
    paragraph_start_ids: list[int] = []
    for paragraph in paragraphs:
        prose = re.sub(r"\s+", " ", _clean_markdown(paragraph)).strip()
        if not prose:
            continue
        paragraph_start_ids.append(len(lines) + 1)
        for sentence in re.split(r"(?<=[.!?])\s+(?=[\"'“‘(]*[A-Z0-9])", prose):
            sentence = sentence.strip()
            if sentence:
                lines.extend(_split_long_sentence(sentence))
    if not lines:
        raise ValueError("script has no narratable text")
    return lines, paragraph_start_ids


def create_job_from_script(
    source: Path, jobs_dir: Path, job_id: str | None = None,
    *, capcut_scene_seconds: int = 0,
) -> tuple[Manifest, Path]:
    """Create a job from a UTF-8 English script without inventing source video."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input script not found: {source}")
    suffix = source.suffix.lower()
    if capcut_scene_seconds and (suffix != ".srt" or not 2 <= capcut_scene_seconds <= 30):
        raise ValueError("fixed CapCut scenes require an SRT and 2-30 seconds")
    if suffix not in {".txt", ".md", ".srt"}:
        raise ValueError("script must be a .txt, .md, or .srt file")
    content = source.read_bytes()
    if not content or len(content) > MAX_SCRIPT_BYTES:
        raise ValueError(f"script must contain 1-{MAX_SCRIPT_BYTES} UTF-8 bytes")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("script must be UTF-8 text") from error
    if not text.strip():
        raise ValueError("script must contain narration text")
    letters = re.findall(r"[A-Za-z\u0400-\u04ff]", text)
    if not letters or sum(char.isascii() for char in letters) < len(letters) / 2:
        raise ValueError("script must contain English narration text")
    timed_segments = _parse_timed_script(text) if suffix == ".srt" else None
    lines, paragraph_start_ids = (
        ([], []) if timed_segments is not None else _segment_script(text)
    )

    resolved_job_id = job_id or uuid4().hex[:12]
    job_dir = resolve_job_directory(jobs_dir, resolved_job_id)
    job_dir.mkdir(parents=True, exist_ok=False)
    script_name = f"script{suffix}"
    shutil.copyfile(source, job_dir / script_name)

    # Chatterbox requires a positive target duration before audio is generated.
    # The timing stage replaces these estimates with measured TTS durations.
    cursor = 0.0
    segments: list[Segment] = []
    paragraph_starts = set(paragraph_start_ids)
    for segment_id, line in enumerate(lines, start=1):
        if segment_id > 1:
            cursor += (
                PARAGRAPH_PAUSE_SECONDS
                if segment_id in paragraph_starts
                else SENTENCE_PAUSE_SECONDS
            )
        duration = max(0.5, len(line.split()) / 2.8)
        segments.append(
            Segment(id=segment_id, start=cursor, end=cursor + duration, ru=line, en=line)
        )
        cursor += duration
    if timed_segments is not None:
        segments = timed_segments
        if capcut_scene_seconds:
            for index, segment in enumerate(segments):
                segment.start = index * capcut_scene_seconds
                segment.end = (index + 1) * capcut_scene_seconds

    artifacts = {"script": script_name}
    if capcut_scene_seconds:
        windows = job_dir / "capcut-windows.srt"
        windows.write_text("\n\n".join(
            f"{index}\n{_srt_time(segment.start)} --> {_srt_time(segment.end)}\n{segment.en}"
            for index, segment in enumerate(segments, start=1)
        ) + "\n", encoding="utf-8")
        artifacts["capcut_windows"] = windows.name

    manifest = Manifest(
        job=Job(id=resolved_job_id, source_file="script-source.mp4"),
        segments=segments,
        artifacts=artifacts,
        metadata={
            "input": {"type": "script", "original_path": str(source)},
            "video_mode": "rebuild",
            "script": {
                "paragraph_start_ids": paragraph_start_ids,
                "timed_cues": timed_segments is not None,
                "capcut_scene_seconds": capcut_scene_seconds,
            },
        },
    )
    manifest.save(job_dir / "manifest.json")
    return manifest, job_dir


class ScriptTimingStage(PipelineStage):
    """Place script speech; fit timed cues near 90% of each visual interval."""

    def __init__(
        self, *, ffmpeg: str = "ffmpeg", minimum_speed: float = 0.85,
        maximum_speed: float = 1.16, target_fraction: float = 0.9,
    ) -> None:
        if not 0.5 <= minimum_speed <= 1.0 or not 1.0 <= maximum_speed <= 2.0:
            raise ValueError("script speed bounds must surround 1.0")
        if not math.isfinite(target_fraction) or not 0.5 < target_fraction < 1.0:
            raise ValueError("target speech fraction must be between 0.5 and 1.0")
        self._ffmpeg = ffmpeg
        self._minimum_speed = minimum_speed
        self._maximum_speed = maximum_speed
        self._target_fraction = target_fraction

    @property
    def name(self) -> str:
        return "script_timing"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if manifest.metadata.get("input", {}).get("type") != "script":
            raise ValueError("script timing requires a script job")
        if not manifest.segments:
            raise ValueError("script timing requires narration segments")

        for segment in manifest.segments:
            duration = segment.tts_duration
            if (
                not segment.tts_file
                or duration is None
                or not math.isfinite(duration)
                or duration <= 0
            ):
                raise ValueError(f"script timing requires TTS audio for segment {segment.id}")
            source = resolve_job_path(
                job_dir, segment.tts_file, label=f"TTS audio for segment {segment.id}"
            )
            if not source.is_file():
                raise ValueError(f"script timing requires TTS audio for segment {segment.id}")

        script_info = manifest.metadata.get("script", {})
        if script_info.get("timed_cues"):
            targets = {
                segment.id: segment.duration * self._target_fraction
                for segment in manifest.segments
            }
            needs_rewrite = [
                (segment.id, round(segment.tts_duration or 0, 2), round(targets[segment.id], 2))
                for segment in manifest.segments
                if not self._minimum_speed <= (segment.tts_duration or 0) / targets[segment.id]
                <= self._maximum_speed
            ]
            if needs_rewrite:
                raise ValueError(
                    "timed narration cannot reach its speech targets "
                    f"(id, recorded seconds, target seconds): {needs_rewrite}; "
                    "rewrite these lines and regenerate TTS"
                )
            adjusted: list[int] = []
            for index, segment in enumerate(manifest.segments):
                duration = segment.tts_duration
                assert duration is not None
                speed = 1.0
                speech_file = segment.tts_file
                target = targets[segment.id]
                if abs(duration - target) > 0.05:
                    speed = duration / target
                    source = resolve_job_path(
                        job_dir, segment.tts_file,
                        label=f"TTS audio for segment {segment.id}",
                    )
                    output = job_dir / "timed-tts" / f"{segment.id:06d}.wav"
                    output.parent.mkdir(exist_ok=True)
                    temporary = output.with_name(f".{output.stem}.tmp.wav")
                    executable = self._ffmpeg if Path(self._ffmpeg).is_file() else shutil.which(
                        self._ffmpeg
                    )
                    if not executable:
                        raise ValueError("FFmpeg is required to fit timed narration")
                    try:
                        result = subprocess.run(
                            [executable, "-y", "-nostdin", "-hide_banner", "-loglevel",
                             "error", "-i", str(source), "-filter:a", f"atempo={speed:.6f}",
                             "-c:a", "pcm_s16le", str(temporary)],
                            capture_output=True, text=True, timeout=120, check=False,
                        )
                        if result.returncode != 0:
                            raise ValueError(
                                f"FFmpeg could not fit segment {segment.id}: "
                                f"{result.stderr[-300:]}"
                            )
                        with wave.open(str(temporary), "rb") as clip:
                            duration = clip.getnframes() / clip.getframerate()
                        if abs(duration - target) > 0.1 or duration > segment.duration:
                            raise ValueError(
                                f"segment {segment.id} missed its {target:.2f}s speech target"
                            )
                        temporary.replace(output)
                    finally:
                        temporary.unlink(missing_ok=True)
                    speech_file = output.relative_to(job_dir).as_posix()
                    adjusted.append(segment.id)
                segment.dub_start = segment.start
                segment.dub_duration = duration
                segment.dub_file = speech_file
                segment.dub_speed = speed
                self.report_progress((index + 1) / len(manifest.segments))
            if adjusted:
                manifest.artifacts["timed_tts"] = "timed-tts"
            manifest.metadata["script_timing"] = {
                "provider": "fixed-srt-cues",
                "total_duration": manifest.segments[-1].end,
                "segment_count": len(manifest.segments),
                "target_fraction": self._target_fraction,
                "minimum_speed": self._minimum_speed,
                "maximum_speed": self._maximum_speed,
                "speed_adjusted_segment_ids": adjusted,
            }
            return manifest

        paragraph_starts = set(script_info.get("paragraph_start_ids", []))
        cursor = 0.0
        for index, segment in enumerate(manifest.segments):
            if index:
                cursor += (
                    PARAGRAPH_PAUSE_SECONDS
                    if segment.id in paragraph_starts
                    else SENTENCE_PAUSE_SECONDS
                )
            duration = segment.tts_duration
            assert duration is not None
            segment.start = cursor
            segment.end = cursor + duration
            segment.dub_start = cursor
            segment.dub_duration = duration
            segment.dub_file = segment.tts_file
            segment.dub_speed = 1.0
            cursor = segment.end
            self.report_progress((index + 1) / len(manifest.segments))
        manifest.metadata["script_timing"] = {
            "provider": "tts-duration",
            "sentence_pause_seconds": SENTENCE_PAUSE_SECONDS,
            "paragraph_pause_seconds": PARAGRAPH_PAUSE_SECONDS,
            "total_duration": cursor,
        }
        return manifest


class NarrationExportStage(PipelineStage):
    """Join script speech and its pauses into one portable WAV file."""

    @property
    def name(self) -> str:
        return "narration_export"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if manifest.metadata.get("input", {}).get("type") != "script":
            raise ValueError("narration export requires a script job")
        if not manifest.segments:
            raise ValueError("narration export requires voiced segments")

        output = job_dir / "narration.wav"
        temporary = job_dir / ".narration.tmp.wav"
        try:
            with wave.open(str(temporary), "wb") as joined:
                format_key: tuple[int, int, int] | None = None
                written_frames = 0
                for index, segment in enumerate(manifest.segments):
                    if segment.dub_start is None or not segment.dub_file:
                        raise ValueError(f"segment {segment.id} has no timed TTS audio")
                    source = resolve_job_path(
                        job_dir, segment.dub_file, label=f"narration segment {segment.id}"
                    )
                    clip_format, samples = _read_narration_clip(source)
                    if format_key is None:
                        format_key = clip_format
                        joined.setnchannels(clip_format[0])
                        joined.setsampwidth(clip_format[1])
                        joined.setframerate(clip_format[2])
                    elif clip_format != format_key:
                        raise ValueError("narration clips have incompatible WAV formats")
                    target_frame = round(segment.dub_start * clip_format[2])
                    if target_frame < written_frames:
                        raise ValueError(f"narration speech overlaps at segment {segment.id}")
                    gap = target_frame - written_frames
                    silent_frame = (
                        bytes([128]) * clip_format[0]
                        if clip_format[1] == 1
                        else bytes(clip_format[0] * clip_format[1])
                    )
                    while gap:
                        frames = min(gap, 65536)
                        joined.writeframesraw(silent_frame * frames)
                        written_frames += frames
                        gap -= frames
                    joined.writeframesraw(samples)
                    written_frames += len(samples) // (clip_format[0] * clip_format[1])
                    self.report_progress((index + 1) / len(manifest.segments))
                if manifest.metadata.get("script", {}).get("timed_cues") and format_key:
                    target_frames = round(manifest.segments[-1].end * format_key[2])
                    if written_frames > target_frames:
                        raise ValueError("narration exceeds the last timed cue")
                    frame_size = format_key[0] * format_key[1]
                    silent_frame = bytes([128]) * format_key[0] if format_key[1] == 1 else bytes(frame_size)
                    while written_frames < target_frames:
                        frames = min(target_frames - written_frames, 65536)
                        joined.writeframesraw(silent_frame * frames)
                        written_frames += frames
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        manifest.artifacts["narration_audio"] = output.name
        return manifest


class SubtitleExportStage(PipelineStage):
    """Export script captions from placed audio, using measured speech durations."""

    @property
    def name(self) -> str:
        return "subtitle_export"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if manifest.metadata.get("input", {}).get("type") != "script":
            raise ValueError("subtitle export requires a script job")
        cues: list[str] = []
        previous_end = 0.0
        for index, segment in enumerate(manifest.segments, start=1):
            start, duration = segment.dub_start, segment.dub_duration
            if start is None or duration is None or not segment.dub_file:
                raise ValueError(f"segment {segment.id} has no timed speech")
            end = start + duration
            if start < previous_end - 0.005:
                raise ValueError(f"subtitle cues overlap at segment {segment.id}")
            text = (segment.en or "").strip()
            if not text:
                raise ValueError(f"segment {segment.id} has no subtitle text")
            cues.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
            previous_end = end
            self.report_progress(index / len(manifest.segments))
        destination = job_dir / "narration.srt"
        temporary = job_dir / ".narration.srt.tmp"
        try:
            temporary.write_text("\n".join(cues), encoding="utf-8")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        manifest.artifacts["subtitles_srt"] = destination.name
        return manifest
