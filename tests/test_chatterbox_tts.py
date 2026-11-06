from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.tts import ChatterboxExecutionError, ChatterboxTTSStage


class ChatterboxTTSTests(unittest.TestCase):
    def test_worker_timeout_kills_and_reaps_process(self) -> None:
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired(["tts"], 12.0)
        process.poll.return_value = None
        process.wait.return_value = -9
        stage = ChatterboxTTSStage(
            python="python",
            reference=Path("reference.wav"),
            timeout=12.0,
        )

        with patch("ru_en_dubber.tts.stage.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(ChatterboxExecutionError, "timed out"):
                stage._run_worker(["tts"], {}, 1)

        process.communicate.assert_called_once_with(timeout=12.0)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    def test_synthesizes_every_segment_and_records_timing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            python = root / "python.exe"
            reference = root / "reference.wav"
            python.write_bytes(b"python")
            reference.write_bytes(b"voice")
            manifest = Manifest(
                job=Job(id="job", source_file="source.mp4"),
                segments=[
                    Segment(id=1, start=0.0, end=2.0, ru="Привет", en="Hello"),
                    Segment(
                        id=2,
                        start=2.1,
                        end=4.1,
                        ru="Как дела?",
                        en="How are you?",
                    ),
                ],
                completed_stages=["transcription", "translation", "premiere_export"],
                artifacts={
                    "premiere_export": "premiere-export/premiere-export.json",
                    "premiere_xml": "premiere-export/premiere_sequence.xml",
                },
                metadata={"premiere_export": {"episode_count": 2}},
            )

            def run_worker(
                command: list[str], _: dict[str, str], __: int
            ) -> tuple[int, str]:
                report_path = Path(command[command.index("--report") + 1])
                output_dir = Path(command[command.index("--output-dir") + 1])
                output_dir.mkdir()
                (output_dir / "000001.wav").write_bytes(b"one")
                (output_dir / "000002.wav").write_bytes(b"two")
                report_path.write_text(
                    json.dumps(
                        {
                            "provider": "chatterbox",
                            "model": "multilingual-v3",
                            "requested_device": "auto",
                            "device": "cuda",
                            "fallback_reason": None,
                            "sample_rate": 24000,
                            "load_seconds": 3.2,
                            "generation_seconds": 5.4,
                            "segments": [
                                {
                                    "id": 1,
                                    "path": "tts/000001.wav",
                                    "duration_seconds": 1.9,
                                },
                                {
                                    "id": 2,
                                    "path": "tts/000002.wav",
                                    "duration_seconds": 2.5,
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                stdout = "\n".join(
                    [
                        '{"event":"segment_complete","completed":1,"total":2}',
                        '{"event":"segment_complete","completed":2,"total":2}',
                    ]
                )
                stage._replay_progress(stdout, 2)
                return 0, stdout

            progress: list[tuple[float | None, str | None]] = []
            stage = ChatterboxTTSStage(
                python=str(python),
                reference=reference,
                model_cache=root / ".models",
            )
            stage.set_progress_callback(lambda fraction, message: progress.append((fraction, message)))
            with patch.object(stage, "_run_worker", side_effect=run_worker):
                result = stage.run(manifest, job_dir)

            self.assertEqual(result.segments[0].tts_file, "tts/000001.wav")
            self.assertEqual(result.segments[1].tts_duration, 2.5)
            self.assertEqual(result.artifacts["tts_report"], "tts.json")
            self.assertEqual(result.metadata["tts"]["device"], "cuda")
            self.assertEqual(result.metadata["tts"]["over_timing_segment_ids"], [2])
            self.assertNotIn("premiere_export", result.metadata)
            self.assertNotIn("premiere_export", result.completed_stages)
            self.assertNotIn("premiere_xml", result.artifacts)
            self.assertTrue(any(message == "Voiced 2 of 2 segments" for _, message in progress))
            written_input = json.loads((job_dir / "tts-input.json").read_text(encoding="utf-8"))
            self.assertEqual(written_input["segments"][0]["text"], "Hello")

    def test_rejects_missing_translation_before_starting_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = Manifest(
                job=Job(id="job", source_file="source.mp4"),
                segments=[Segment(id=1, start=0.0, end=1.0, ru="Привет")],
            )
            stage = ChatterboxTTSStage(
                python=str(root / "python.exe"),
                reference=root / "reference.wav",
            )
            with self.assertRaisesRegex(ChatterboxExecutionError, "English text"):
                stage.run(manifest, root)

    def test_rejects_report_with_missing_segment(self) -> None:
        manifest = Manifest(
            job=Job(id="job", source_file="source.mp4"),
            segments=[Segment(id=1, start=0.0, end=1.0, ru="Да", en="Yes")],
        )
        with self.assertRaisesRegex(ChatterboxExecutionError, "different set"):
            ChatterboxTTSStage._validate_report({"segments": []}, manifest, Path.cwd())


if __name__ == "__main__":
    unittest.main()
