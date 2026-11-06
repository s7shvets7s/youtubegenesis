from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.processes import stop_process
from ru_en_dubber.tts.delivery import prepare_delivery_plan


class TTSError(RuntimeError):
    """Base error raised by a text-to-speech adapter."""


class ChatterboxNotFoundError(TTSError):
    """Raised when the configured Chatterbox environment is unavailable."""


class ChatterboxExecutionError(TTSError):
    """Raised when Chatterbox does not produce a complete result."""


class ChatterboxTTSStage(PipelineStage):
    """Clone a reference voice and synthesize every translated segment."""

    def __init__(
        self,
        *,
        python: str,
        reference: Path,
        model: str = "v3",
        device: str = "auto",
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        model_cache: Path | None = None,
        timeout: float | None = None,
        provider: str = "chatterbox",
        model_dir: Path | None = None,
        direction_file: Path | None = None,
    ) -> None:
        if provider not in {"chatterbox", "cosyvoice3"}:
            raise ValueError("TTS provider must be chatterbox or cosyvoice3")
        if model not in {"v2", "v3"}:
            raise ValueError("Chatterbox model must be v2 or v3")
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("TTS device must be auto, cuda, or cpu")
        if not 0.0 <= exaggeration <= 2.0:
            raise ValueError("TTS exaggeration must be between 0 and 2")
        if not 0.0 <= cfg_weight <= 1.0:
            raise ValueError("TTS cfg weight must be between 0 and 1")
        if timeout is not None and timeout <= 0:
            raise ValueError("TTS timeout must be positive")

        self._python = python
        self._reference = reference
        self._model = model
        self._device = device
        self._exaggeration = exaggeration
        self._cfg_weight = cfg_weight
        self._model_cache = model_cache
        self._timeout = timeout
        self._provider = provider
        self._model_dir = model_dir
        self._direction_file = direction_file

    @property
    def name(self) -> str:
        return "tts"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if not manifest.segments:
            raise ChatterboxExecutionError("TTS requires translated segments")
        missing = [segment.id for segment in manifest.segments if not (segment.en or "").strip()]
        if missing:
            raise ChatterboxExecutionError(
                f"TTS requires English text for segment IDs: {missing}"
            )

        python = self._resolve_file(self._python, "TTS Python")
        reference = self._reference.expanduser().resolve()
        if not reference.is_file():
            raise ChatterboxNotFoundError(f"voice reference not found: {reference}")
        if self._provider == "cosyvoice3" and (
            self._model_dir is None
            or not (self._model_dir.expanduser().resolve() / "cosyvoice3.yaml").is_file()
        ):
            raise ChatterboxNotFoundError("CosyVoice 3 model directory is missing or invalid")

        delivery = prepare_delivery_plan(manifest, job_dir, self._direction_file)
        if self._provider == "chatterbox" and any(
            item["style"] != "neutral" for item in delivery
        ):
            raise ChatterboxExecutionError(
                "expressive TTS directions require --tts-provider cosyvoice3"
            )

        input_path = job_dir / "tts-input.json"
        report_path = job_dir / "tts.json"
        output_dir = job_dir / "tts"
        input_payload = {
            "segments": [
                {**item, "target_seconds": round(segment.duration, 6)}
                for item, segment in zip(delivery, manifest.segments)
            ]
        }
        self._write_json(input_path, input_payload)
        self.report_progress(0.02, "Checking the voice environment…")

        command = [
            python,
            "-m",
            ("ru_en_dubber.tts.worker" if self._provider == "chatterbox"
             else "ru_en_dubber.tts.cosyvoice_worker"),
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--report",
            str(report_path),
            "--reference",
            str(reference),
            "--device",
            self._device,
        ]
        if self._provider == "chatterbox":
            command += ["--model", self._model, "--exaggeration", str(self._exaggeration),
                        "--cfg-weight", str(self._cfg_weight)]
        else:
            assert self._model_dir is not None
            command += ["--model-dir", str(self._model_dir.expanduser().resolve())]
        environment = self._environment()
        self.report_progress(0.05, "Loading the model and voice sample…")
        try:
            return_code, worker_output = self._run_worker(
                command, environment, len(manifest.segments)
            )
        except OSError as error:
            raise ChatterboxNotFoundError(
                f"could not start {self._provider} worker: {error}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ChatterboxExecutionError(f"{self._provider} worker timed out") from error

        if return_code != 0:
            raise ChatterboxExecutionError(
                f"{self._provider} worker failed ({return_code}): "
                f"{worker_output[-4000:].strip() or 'no error output'}"
            )
        if not report_path.is_file():
            raise ChatterboxExecutionError(f"{self._provider} worker did not create tts.json")

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ChatterboxExecutionError("tts.json is unreadable or invalid") from error
        items = self._validate_report(report, manifest, job_dir)

        by_id = {int(item["id"]): item for item in items}
        over_timing: list[int] = []
        for segment in manifest.segments:
            item = by_id[segment.id]
            segment.tts_file = str(item["path"])
            segment.tts_duration = float(item["duration_seconds"])
            segment.dub_file = None
            segment.dub_start = None
            segment.dub_duration = None
            segment.dub_speed = None
            if segment.tts_duration > segment.duration * 1.10:
                over_timing.append(segment.id)

        manifest.artifacts["tts_report"] = report_path.name
        manifest.artifacts["tts_segments"] = output_dir.name
        manifest.artifacts.pop("speech_postprocess_report", None)
        manifest.artifacts.pop("speech_postprocess_audio", None)
        manifest.metadata.pop("speech_postprocess", None)
        manifest.completed_stages = [
            stage for stage in manifest.completed_stages
            if stage not in {"speech_postprocess", "avatar_overlay", "music_bed", "premiere_export"}
        ]
        manifest.artifacts.pop("premiere_export", None)
        manifest.artifacts.pop("premiere_xml", None)
        manifest.artifacts.pop("aligned_tts", None)
        manifest.metadata.pop("premiere_export", None)
        manifest.metadata.pop("music_bed", None)
        manifest.metadata.pop("timing_alignment", None)
        manifest.metadata.pop("avatar", None)
        for artifact in ("avatar_video", "avatar_overlay_video", "avatar_base_video", "avatar_composite"):
            manifest.artifacts.pop(artifact, None)
        for artifact in ("music_report", "music_bed", "music_plan", "music_candidates", "music_credits"):
            manifest.artifacts.pop(artifact, None)
        manifest.completed_stages = [
            stage for stage in manifest.completed_stages if stage != "timing_alignment"
        ]
        manifest.metadata["tts"] = {
            "provider": self._provider,
            "model": report.get("model", f"multilingual-{self._model}"),
            "requested_device": report.get("requested_device", self._device),
            "device": report.get("device"),
            "fallback_reason": report.get("fallback_reason"),
            "reference": reference.name,
            "segment_count": len(items),
            "sample_rate": report.get("sample_rate"),
            "load_seconds": report.get("load_seconds"),
            "generation_seconds": report.get("generation_seconds"),
            "over_timing_segment_ids": over_timing,
        }
        self.report_progress(
            0.99,
            f"Voiced {len(items)} segments, over timing: {len(over_timing)}",
        )
        return manifest

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[2])
        current_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_root + os.pathsep + current_pythonpath
            if current_pythonpath
            else source_root
        )
        environment["PYTHONUTF8"] = "1"
        if self._model_cache is not None:
            cache = self._model_cache.expanduser().resolve()
            hf_home = cache / "huggingface"
            environment["HF_HOME"] = str(hf_home)
            environment["HF_HUB_CACHE"] = str(hf_home / "hub")
            environment["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        return environment

    def _run_worker(
        self,
        command: list[str],
        environment: dict[str, str],
        segment_count: int,
    ) -> tuple[int, str]:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        try:
            output, _ = process.communicate(timeout=self._timeout)
        except subprocess.TimeoutExpired as error:
            stop_process(process, grace_seconds=0)
            raise ChatterboxExecutionError(
                f"TTS worker timed out after {self._timeout:.0f} seconds"
            ) from error
        self._replay_progress(output, segment_count)
        return_code = process.returncode
        if return_code is None:
            return_code = process.wait()
        return return_code, output

    def _replay_progress(self, stdout: str, segment_count: int) -> None:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "segment_complete":
                continue
            completed = int(event.get("completed", 0))
            total = int(event.get("total", segment_count)) or segment_count
            self.report_progress(
                0.08 + 0.88 * min(1.0, completed / total),
                f"Voiced {completed} of {total} segments",
            )

    @staticmethod
    def _validate_report(
        report: dict[str, Any], manifest: Manifest, job_dir: Path
    ) -> list[dict[str, Any]]:
        items = report.get("segments")
        if not isinstance(items, list):
            raise ChatterboxExecutionError("tts.json has no segment list")
        expected_ids = {segment.id for segment in manifest.segments}
        try:
            actual_ids = {int(item["id"]) for item in items}
        except (KeyError, TypeError, ValueError) as error:
            raise ChatterboxExecutionError("tts.json contains an invalid segment") from error
        if len(items) != len(actual_ids) or actual_ids != expected_ids:
            raise ChatterboxExecutionError(
                "tts.json contains a different set of segment IDs"
            )

        resolved_job_dir = job_dir.resolve()
        for item in items:
            try:
                duration = float(item["duration_seconds"])
                relative_path = Path(str(item["path"]))
            except (KeyError, TypeError, ValueError) as error:
                raise ChatterboxExecutionError("tts.json contains invalid output data") from error
            if duration <= 0 or relative_path.is_absolute():
                raise ChatterboxExecutionError("tts.json contains invalid output data")
            output_path = (job_dir / relative_path).resolve()
            try:
                output_path.relative_to(resolved_job_dir)
            except ValueError as error:
                raise ChatterboxExecutionError("TTS output escapes the job directory") from error
            if not output_path.is_file():
                raise ChatterboxExecutionError(f"TTS output not found: {output_path}")
        return items

    @staticmethod
    def _resolve_file(path: str, label: str) -> str:
        candidate = Path(path).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise ChatterboxNotFoundError(f"{label} not found: {candidate}")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
