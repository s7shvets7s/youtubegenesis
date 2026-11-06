from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_path


class WhisperXError(RuntimeError):
    """Base error raised by the WhisperX adapter."""


class WhisperXNotFoundError(WhisperXError):
    """Raised when the configured environment cannot import WhisperX."""


class WhisperXExecutionError(WhisperXError):
    """Raised when transcription does not produce a usable result."""


class WhisperXTranscriptionStage(PipelineStage):
    """Transcribe the complete isolated vocal track with word-aligned WhisperX."""

    def __init__(
        self,
        *,
        python: str,
        model: str = "medium",
        language: str = "ru",
        device: str = "auto",
        batch_size: int = 4,
        diarize: bool = False,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        model_cache: Path | None = None,
        ffmpeg_bin: Path | None = None,
        input_artifact: str = "vocals",
        processing_timeout: float = 7_200.0,
        probe_timeout: float = 60.0,
    ) -> None:
        if not model.strip():
            raise ValueError("WhisperX model must not be empty")
        if not language.strip():
            raise ValueError("transcription language must not be empty")
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("WhisperX device must be auto, cuda or cpu")
        if not 1 <= batch_size <= 32:
            raise ValueError("WhisperX batch size must be between 1 and 32")
        if min_speakers is not None and min_speakers < 1:
            raise ValueError("minimum speaker count must be positive")
        if max_speakers is not None and max_speakers < 1:
            raise ValueError("maximum speaker count must be positive")
        if (
            min_speakers is not None
            and max_speakers is not None
            and min_speakers > max_speakers
        ):
            raise ValueError("minimum speaker count must not exceed maximum")
        if not diarize and (min_speakers is not None or max_speakers is not None):
            raise ValueError("speaker count hints require diarization")
        if not math.isfinite(processing_timeout) or processing_timeout <= 0:
            raise ValueError("processing_timeout must be a positive finite number")
        if not math.isfinite(probe_timeout) or probe_timeout <= 0:
            raise ValueError("probe_timeout must be a positive finite number")

        self._python = python
        self._model = model
        self._language = language
        self._device = device
        self._batch_size = batch_size
        self._diarize = diarize
        self._min_speakers = min_speakers
        self._max_speakers = max_speakers
        self._model_cache = model_cache
        self._ffmpeg_bin = ffmpeg_bin
        self._input_artifact = input_artifact
        self._processing_timeout = processing_timeout
        self._probe_timeout = probe_timeout

    @property
    def name(self) -> str:
        return "transcription"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        self.report_progress(0.02, "Checking the WhisperX environment…")
        python = self._resolve_python(self._python)
        audio_name = manifest.artifacts.get(self._input_artifact)
        if audio_name is None:
            raise WhisperXExecutionError(
                f"manifest artifact is missing: {self._input_artifact}"
            )

        audio_path = resolve_job_path(
            job_dir, audio_name, label=f"{self._input_artifact} artifact"
        )
        if not audio_path.is_file():
            raise FileNotFoundError(f"audio for transcription not found: {audio_path}")

        cache_root = (self._model_cache or job_dir.parent / ".models").resolve()
        model_dir = cache_root / "whisperx"
        hf_home = cache_root / "huggingface"
        model_dir.mkdir(parents=True, exist_ok=True)
        hf_home.mkdir(parents=True, exist_ok=True)
        environment = self._build_environment(python, cache_root, hf_home)
        self.report_progress(0.06, "Checking CUDA and models…")
        detected_device = self._probe_environment(
            python, environment, timeout=self._probe_timeout
        )
        if self._device == "cpu":
            device = "cpu"
        elif detected_device != "cuda":
            raise WhisperXExecutionError(
                "WhisperX CUDA is unavailable in the configured environment. "
                "Reinstall .venv-whisperx using requirements/whisperx.txt, or pass "
                "--whisperx-device cpu to explicitly allow slow CPU transcription."
            )
        else:
            device = "cuda"
        compute_type = "float16" if device == "cuda" else "int8"

        output_dir = job_dir / ".whisperx-output"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)

        command = [
            python,
            "-m",
            "whisperx",
            str(audio_path),
            "--model",
            self._model,
            "--model_dir",
            str(model_dir),
            "--language",
            self._language,
            "--task",
            "transcribe",
            "--device",
            device,
            "--compute_type",
            compute_type,
            "--batch_size",
            str(self._batch_size),
            "--vad_method",
            "silero",
            "--output_dir",
            str(output_dir),
            "--output_format",
            "json",
            "--print_progress",
            "True",
            "--verbose",
            "False",
        ]
        if self._diarize:
            command.append("--diarize")
            if self._min_speakers is not None:
                command.extend(["--min_speakers", str(self._min_speakers)])
            if self._max_speakers is not None:
                command.extend(["--max_speakers", str(self._max_speakers)])
        device_label = "GPU" if device == "cuda" else "CPU"
        self.report_progress(None, f"Recognition and alignment on {device_label}…")
        duration = self._source_duration(manifest)
        timeout = max(self._processing_timeout, duration * 15.0)
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
            raise WhisperXExecutionError(
                f"transcription timed out after {timeout:.0f} seconds; "
                "reduce the input length or increase processing_timeout"
            ) from error
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise WhisperXExecutionError(f"transcription failed: {details}")

        self.report_progress(0.92, "Verifying and parsing segments…")
        generated_json = output_dir / f"{audio_path.stem}.json"
        if not generated_json.is_file():
            raise WhisperXExecutionError(
                "WhisperX completed but did not create the transcript JSON"
            )
        try:
            data = json.loads(generated_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WhisperXExecutionError("WhisperX returned invalid JSON") from error

        segments = self._parse_segments(data)
        if not segments:
            raise WhisperXExecutionError("WhisperX did not detect any speech")

        transcript_path = job_dir / "transcript.json"
        temporary_path = transcript_path.with_suffix(".json.tmp")
        shutil.copy2(generated_json, temporary_path)
        temporary_path.replace(transcript_path)
        shutil.rmtree(output_dir)

        manifest.segments = segments
        manifest.completed_stages = [
            stage
            for stage in manifest.completed_stages
            if stage
            not in {
                "fake_transcription",
                "fake_translation",
                "translation",
                "content_analysis",
                "animation_plan",
                "tts",
                "timing_alignment",
                "visual_rebuild",
                "avatar_overlay",
                "music_bed",
                "premiere_export",
            }
        ]
        manifest.metadata.pop("translation_provider", None)
        manifest.metadata.pop("translation", None)
        manifest.metadata.pop("tts", None)
        manifest.metadata.pop("content_analysis", None)
        manifest.metadata.pop("animation_plan", None)
        manifest.metadata.pop("visual_rebuild", None)
        manifest.metadata.pop("avatar", None)
        manifest.metadata.pop("music_bed", None)
        manifest.metadata.pop("premiere_export", None)
        manifest.metadata.pop("ad_removal", None)
        for artifact in (
            "translation",
            "translation_log",
            "tts_report",
            "tts_segments",
            "aligned_tts",
            "content_analysis",
            "episode_plan",
            "animation_plan",
            "visual_plan",
            "rebuild_video",
            "visual_credits",
            "avatar_video",
            "avatar_overlay_video",
            "avatar_base_video",
            "avatar_composite",
            "music_report",
            "music_bed",
            "music_plan",
            "music_candidates",
            "music_credits",
            "premiere_export",
            "premiere_xml",
            "ad_removal",
        ):
            manifest.artifacts.pop(artifact, None)
        manifest.artifacts["transcript"] = transcript_path.name
        speakers = list(dict.fromkeys(segment.speaker for segment in segments))
        manifest.metadata["transcription"] = {
            "provider": "whisperx",
            "model": self._model,
            "language": data.get("language", self._language),
            "device": device,
            "compute_type": compute_type,
            "batch_size": self._batch_size,
            "diarization": self._diarize,
            "speakers": speakers,
            "speaker_count": len(speakers),
            "segment_count": len(segments),
        }
        manifest.metadata.pop("transcription_provider", None)
        self.report_progress(0.98, f"Segments recognized: {len(segments)}")
        return manifest

    def _build_environment(
        self, python: str, cache_root: Path, hf_home: Path
    ) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HF_HOME"] = str(hf_home)
        environment["HF_HUB_CACHE"] = str(hf_home / "hub")
        environment["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        environment["TORCH_HOME"] = str(cache_root / "torch")
        environment["NLTK_DATA"] = str(cache_root / "nltk_data")
        environment["PYTHONUTF8"] = "1"

        extra_paths: list[str] = []
        python_path = Path(python)
        torch_lib = python_path.parent.parent / "Lib" / "site-packages" / "torch" / "lib"
        if torch_lib.is_dir():
            extra_paths.append(str(torch_lib))
        if self._ffmpeg_bin is not None and self._ffmpeg_bin.is_dir():
            extra_paths.append(str(self._ffmpeg_bin.resolve()))
        if extra_paths:
            environment["PATH"] = os.pathsep.join(
                extra_paths + [environment.get("PATH", "")]
            )
        return environment

    @staticmethod
    def _parse_segments(data: dict[str, Any]) -> list[Segment]:
        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list):
            raise WhisperXExecutionError("WhisperX JSON has no segment list")

        segments: list[Segment] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            start = item.get("start")
            end = item.get("end")
            if not text or start is None or end is None:
                continue
            start_value = max(0.0, float(start))
            end_value = float(end)
            if end_value <= start_value:
                continue
            speaker = str(item.get("speaker") or "SPEAKER_00")
            segments.append(
                Segment(
                    id=len(segments) + 1,
                    start=start_value,
                    end=end_value,
                    ru=text,
                    speaker=speaker,
                )
            )
        return segments

    @staticmethod
    def _resolve_python(command: str) -> str:
        command_path = Path(command).expanduser()
        if command_path.parent != Path("."):
            if command_path.is_file():
                return str(command_path.resolve())
            raise WhisperXNotFoundError(f"Python executable not found: {command_path}")

        resolved = shutil.which(command)
        if resolved is None:
            raise WhisperXNotFoundError(f"Python executable not found: {command}")
        return resolved

    @staticmethod
    def _probe_environment(
        python: str, environment: dict[str, str], *, timeout: float = 60.0
    ) -> str:
        result = subprocess.run(
            [
                python,
                "-c",
                (
                    "import torch, whisperx; "
                    "print('cuda' if torch.cuda.is_available() else 'cpu')"
                ),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or "WhisperX import failed"
            raise WhisperXNotFoundError(
                f"WhisperX is unavailable in the configured environment: {details}"
            )
        device = result.stdout.strip().splitlines()[-1]
        if device not in {"cuda", "cpu"}:
            raise WhisperXExecutionError(f"unexpected device probe result: {device!r}")
        return device

    @staticmethod
    def _source_duration(manifest: Manifest) -> float:
        value = manifest.metadata.get("source_media", {}).get("duration", 0.0)
        try:
            duration = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return duration if math.isfinite(duration) and duration > 0 else 0.0
