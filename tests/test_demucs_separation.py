from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.models import Job, Manifest
from ru_en_dubber.security import MediaSecurityError
from ru_en_dubber.separation import (
    DemucsExecutionError,
    DemucsSeparationStage,
)


class DemucsSeparationTests(unittest.TestCase):
    def test_timeout_scales_with_source_duration_and_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            (job_dir / "audio.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="video_001"),
                artifacts={"extracted_audio": "audio.wav"},
                metadata={"source_media": {"duration": 600.0}},
            )
            stage = DemucsSeparationStage(
                python=sys.executable,
                device="cpu",
                processing_timeout=7_200.0,
            )

            with patch.object(stage, "_probe_environment", return_value="cpu"):
                with patch(
                    "ru_en_dubber.separation.demucs.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["demucs"], 12_000.0),
                ) as run:
                    with self.assertRaisesRegex(
                        DemucsExecutionError, "timed out after 12000 seconds"
                    ):
                        stage.run(manifest, job_dir)

            self.assertEqual(run.call_args.kwargs["timeout"], 12_000.0)

    def test_rejects_input_artifact_outside_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            (root / "outside.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="job"),
                artifacts={"extracted_audio": "../outside.wav"},
            )
            stage = DemucsSeparationStage(python=sys.executable, device="cpu")

            with patch.object(stage, "_probe_environment", return_value="cpu"):
                with patch(
                    "ru_en_dubber.separation.demucs.subprocess.run",
                    side_effect=AssertionError("Demucs must not run"),
                ):
                    with self.assertRaises(MediaSecurityError):
                        stage.run(manifest, job_dir)

    def test_creates_vocal_and_background_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            (job_dir / "audio.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="video_001"),
                artifacts={"extracted_audio": "audio.wav"},
            )

            separation_environment: dict[str, str] = {}

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[1] == "-c":
                    return subprocess.CompletedProcess(command, 0, "cuda\n", "")

                separation_environment.update(kwargs["env"])  # type: ignore[arg-type]
                output_root = Path(command[command.index("-o") + 1])
                model = command[command.index("-n") + 1]
                input_path = Path(command[-1])
                stem_dir = output_root / model / input_path.stem
                stem_dir.mkdir(parents=True)
                (stem_dir / "vocals.wav").write_bytes(b"vocals")
                (stem_dir / "no_vocals.wav").write_bytes(b"background")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch(
                "ru_en_dubber.separation.demucs.subprocess.run", side_effect=fake_run
            ):
                result = DemucsSeparationStage(
                    python=sys.executable,
                    model_cache=job_dir / ".models",
                ).run(manifest, job_dir)

            self.assertEqual(result.artifacts["vocals"], "vocals.wav")
            self.assertEqual(result.artifacts["background"], "background.wav")
            self.assertEqual(result.metadata["source_separation"]["device"], "cuda")
            self.assertEqual((job_dir / "vocals.wav").read_bytes(), b"vocals")
            self.assertEqual(
                (job_dir / "background.wav").read_bytes(), b"background"
            )
            expected_cache = (job_dir / ".models").resolve()
            self.assertEqual(
                Path(separation_environment["HF_HOME"]).parent,
                expected_cache,
            )
            self.assertEqual(
                Path(separation_environment["TORCH_HOME"]).parent,
                expected_cache,
            )
            self.assertFalse((job_dir / ".demucs-output").exists())

    def test_requires_extracted_audio_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DemucsExecutionError, "artifact"):
                DemucsSeparationStage(python=sys.executable).run(
                    Manifest(job=Job(id="video_001")), Path(directory)
                )

    def test_rejects_segment_larger_than_htdemucs_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 7"):
            DemucsSeparationStage(python=sys.executable, segment_seconds=8)


if __name__ == "__main__":
    unittest.main()
