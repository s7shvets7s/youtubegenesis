from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.models import Job, Manifest
from ru_en_dubber.security import MediaSecurityError
from ru_en_dubber.transcription import (
    WhisperXExecutionError,
    WhisperXTranscriptionStage,
)


class WhisperXTranscriptionTests(unittest.TestCase):
    def test_timeout_scales_with_source_duration_and_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            python = root / "python.exe"
            python.write_bytes(b"")
            (job_dir / "vocals.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="job", source_file="source.mp4"),
                artifacts={"vocals": "vocals.wav"},
                metadata={"source_media": {"duration": 800.0}},
            )
            stage = WhisperXTranscriptionStage(
                python=str(python),
                device="cpu",
                model_cache=root / ".models",
                processing_timeout=7_200.0,
            )

            with patch.object(stage, "_probe_environment", return_value="cpu"):
                with patch(
                    "ru_en_dubber.transcription.whisperx.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["whisperx"], 12_000.0),
                ) as run:
                    with self.assertRaisesRegex(
                        WhisperXExecutionError, "timed out after 12000 seconds"
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
                artifacts={"vocals": "../outside.wav"},
            )
            stage = WhisperXTranscriptionStage(
                python="python",
                device="cpu",
                model_cache=root / ".models",
            )

            with patch.object(stage, "_resolve_python", return_value="python"):
                with patch(
                    "ru_en_dubber.transcription.whisperx.subprocess.run",
                    side_effect=AssertionError("WhisperX must not run"),
                ):
                    with self.assertRaises(MediaSecurityError):
                        stage.run(manifest, job_dir)

    def test_auto_device_rejects_implicit_cpu_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            python = root / "python.exe"
            python.write_bytes(b"")
            (job_dir / "vocals.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="job", source_file="source.mp4"),
                artifacts={"vocals": "vocals.wav"},
            )
            stage = WhisperXTranscriptionStage(
                python=str(python),
                model_cache=root / ".models",
            )

            with patch.object(stage, "_probe_environment", return_value="cpu"):
                with self.assertRaisesRegex(RuntimeError, "CUDA is unavailable"):
                    stage.run(manifest, job_dir)

    def test_transcribes_all_segments_and_replaces_fake_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            python = root / "python.exe"
            python.write_bytes(b"")
            (job_dir / "vocals.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="job", source_file="source.mp4"),
                completed_stages=["source_separation", "fake_transcription", "fake_translation"],
                artifacts={"vocals": "vocals.wav"},
                metadata={
                    "transcription_provider": "fake",
                    "translation_provider": "fake",
                },
            )
            payload = {
                "language": "ru",
                "segments": [
                    {
                        "start": 0.25,
                        "end": 2.0,
                        "text": " Первый фрагмент. ",
                        "speaker": "SPEAKER_00",
                    },
                    {
                        "start": 63.5,
                        "end": 66.1,
                        "text": "Последний фрагмент.",
                        "speaker": "SPEAKER_01",
                    },
                ],
            }
            transcription_command: list[str] = []

            def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if "-c" in command:
                    return subprocess.CompletedProcess(command, 0, "cuda\n", "")
                output_dir = Path(command[command.index("--output_dir") + 1])
                transcription_command.extend(command)
                (output_dir / "vocals.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            stage = WhisperXTranscriptionStage(
                python=str(python),
                model="medium",
                diarize=True,
                min_speakers=2,
                max_speakers=3,
                model_cache=root / ".models",
            )
            with patch("ru_en_dubber.transcription.whisperx.subprocess.run", side_effect=run):
                result = stage.run(manifest, job_dir)

            self.assertEqual(len(result.segments), 2)
            self.assertEqual(result.segments[-1].end, 66.1)
            self.assertEqual(result.segments[0].ru, "Первый фрагмент.")
            self.assertEqual(result.segments[-1].speaker, "SPEAKER_01")
            self.assertEqual(result.artifacts["transcript"], "transcript.json")
            self.assertTrue((job_dir / "transcript.json").is_file())
            self.assertNotIn("fake_transcription", result.completed_stages)
            self.assertNotIn("fake_translation", result.completed_stages)
            self.assertNotIn("transcription_provider", result.metadata)
            self.assertNotIn("translation_provider", result.metadata)
            self.assertEqual(result.metadata["transcription"]["device"], "cuda")
            self.assertEqual(result.metadata["transcription"]["segment_count"], 2)
            self.assertEqual(result.metadata["transcription"]["speaker_count"], 2)
            self.assertEqual(
                result.metadata["transcription"]["speakers"],
                ["SPEAKER_00", "SPEAKER_01"],
            )
            self.assertIn("--diarize", transcription_command)
            self.assertEqual(
                transcription_command[transcription_command.index("--min_speakers") + 1],
                "2",
            )

    def test_rejects_json_without_segments(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no segment list"):
            WhisperXTranscriptionStage._parse_segments({})


if __name__ == "__main__":
    unittest.main()
