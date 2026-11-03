from __future__ import annotations

import os
import math
import shutil
import subprocess
from pathlib import Path

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_path


class DemucsError(RuntimeError):
    """Base error raised by the Demucs adapter."""


class DemucsNotFoundError(DemucsError):
    """Raised when the configured Python environment has no Demucs package."""


class DemucsExecutionError(DemucsError):
    """Raised when source separation does not complete successfully."""


class DemucsSeparationStage(PipelineStage):
    """Split extracted audio into vocals and background stems with Demucs."""

    def __init__(
        self,
        *,
        python: str,
        model: str = "htdemucs",
        device: str = "auto",
        segment_seconds: int = 7,
        model_cache: Path | None = None,
        input_artifact: str = "extracted_audio",
        processing_timeout: float = 7_200.0,
        probe_timeout: float = 60.0,
    ) -> None:
        if not model or model.isspace():
            raise ValueError("Demucs model must not be empty")
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("Demucs device must be auto, cuda or cpu")
        if not 1 <= segment_seconds <= 7:
            raise ValueError("segment_seconds must be between 1 and 7")
        if not math.isfinite(processing_timeout) or processing_timeout <= 0:
            raise ValueError("processing_timeout must be a positive finite number")
        if not math.isfinite(probe_timeout) or probe_timeout <= 0:
            raise ValueError("probe_timeout must be a positive finite number")

        self._python = python
        self._model = model
        self._device = device
        self._segment_seconds = segment_seconds
        self._model_cache = model_cache
        self._input_artifact = input_artifact
        self._processing_timeout = processing_timeout
        self._probe_timeout = probe_timeout

    @property
    def name(self) -> str:
        return "source_separation"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        self.report_progress(0.03, "Проверка окружения Demucs…")
        python = self._resolve_python(self._python)
        audio_name = manifest.artifacts.get(self._input_artifact)
        if audio_name is None:
            raise DemucsExecutionError(
                f"manifest artifact is missing: {self._input_artifact}"
            )

        audio_path = resolve_job_path(
            job_dir, audio_name, label=f"{self._input_artifact} artifact"
        )
        if not audio_path.is_file():
            raise FileNotFoundError(f"audio for separation not found: {audio_path}")

        self.report_progress(0.08, "Проверка CUDA…")
        device = self._probe_environment(python, timeout=self._probe_timeout)
        if self._device != "auto":
            device = self._device

        model_cache = (self._model_cache or job_dir.parent / ".models").resolve()
        torch_cache = model_cache / "torch"
        huggingface_cache = model_cache / "huggingface"
        torch_cache.mkdir(parents=True, exist_ok=True)
        huggingface_cache.mkdir(parents=True, exist_ok=True)
        output_root = job_dir / ".demucs-output"
        if output_root.exists():
            shutil.rmtree(output_root)

        environment = os.environ.copy()
        environment["TORCH_HOME"] = str(torch_cache)
        environment["HF_HOME"] = str(huggingface_cache)
        environment["HF_HUB_CACHE"] = str(huggingface_cache / "hub")
        command = [
            python,
            "-m",
            "demucs.separate",
            "--two-stems",
            "vocals",
            "--other-method",
            "add",
            "-n",
            self._model,
            "-d",
            device,
            "--segment",
            str(self._segment_seconds),
            "-o",
            str(output_root),
            str(audio_path),
        ]
        device_label = "GPU" if device == "cuda" else "CPU"
        self.report_progress(None, f"Разделение дорожек на {device_label}…")
        duration = self._source_duration(manifest)
        timeout = max(self._processing_timeout, duration * 20.0)
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
            raise DemucsExecutionError(
                f"source separation timed out after {timeout:.0f} seconds; "
                "reduce the input length or increase processing_timeout"
            ) from error
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise DemucsExecutionError(f"source separation failed: {details}")

        stem_dir = output_root / self._model / audio_path.stem
        generated_vocals = stem_dir / "vocals.wav"
        generated_background = stem_dir / "no_vocals.wav"
        if not generated_vocals.is_file() or not generated_background.is_file():
            raise DemucsExecutionError(
                "Demucs completed but vocals.wav or no_vocals.wav is missing"
            )

        self.report_progress(0.90, "Сохранение вокала и фона…")
        vocals_path = job_dir / "vocals.wav"
        background_path = job_dir / "background.wav"
        self._copy_atomically(generated_vocals, vocals_path)
        self._copy_atomically(generated_background, background_path)
        shutil.rmtree(output_root)

        manifest.artifacts["vocals"] = vocals_path.name
        manifest.artifacts["background"] = background_path.name
        manifest.metadata["source_separation"] = {
            "provider": "demucs",
            "model": self._model,
            "device": device,
            "segment_seconds": self._segment_seconds,
            "mode": "two_stems_vocals",
        }
        self.report_progress(0.98, "Дорожки разделены")
        return manifest

    @staticmethod
    def _resolve_python(command: str) -> str:
        command_path = Path(command).expanduser()
        if command_path.parent != Path("."):
            if command_path.is_file():
                return str(command_path.resolve())
            raise DemucsNotFoundError(f"Python executable not found: {command_path}")

        resolved = shutil.which(command)
        if resolved is None:
            raise DemucsNotFoundError(f"Python executable not found: {command}")
        return resolved

    @staticmethod
    def _probe_environment(python: str, *, timeout: float = 60.0) -> str:
        result = subprocess.run(
            [
                python,
                "-c",
                (
                    "import demucs, torch; "
                    "print('cuda' if torch.cuda.is_available() else 'cpu')"
                ),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or "Demucs import failed"
            raise DemucsNotFoundError(
                f"Demucs is unavailable in the configured environment: {details}"
            )
        device = result.stdout.strip()
        if device not in {"cuda", "cpu"}:
            raise DemucsExecutionError(f"unexpected device probe result: {device!r}")
        return device

    @staticmethod
    def _source_duration(manifest: Manifest) -> float:
        value = manifest.metadata.get("source_media", {}).get("duration", 0.0)
        try:
            duration = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return duration if math.isfinite(duration) and duration > 0 else 0.0

    @staticmethod
    def _copy_atomically(source: Path, destination: Path) -> None:
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
