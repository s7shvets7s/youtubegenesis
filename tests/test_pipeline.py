from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ru_en_dubber.models import JobStatus, Manifest
from ru_en_dubber.pipeline.fake import FakeTranscriptionStage, FakeTranslationStage
from ru_en_dubber.pipeline.runner import (
    PipelineRunner,
    create_job,
    create_job_from_url,
)


class FakePipelineTests(unittest.TestCase):
    def test_job_creation_rejects_ids_that_escape_jobs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            jobs = root / "jobs"

            for job_id in ("../escaped", "nested/job", str(root / "absolute")):
                with self.subTest(job_id=job_id):
                    with self.assertRaisesRegex(ValueError, "job id"):
                        create_job(source, jobs, job_id)

            with self.assertRaisesRegex(ValueError, "job id"):
                create_job_from_url(
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    jobs,
                    "../escaped-url",
                )
            self.assertFalse((root / "escaped").exists())
            self.assertFalse((root / "escaped-url").exists())

    def test_pipeline_creates_translated_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mp4"
            source.write_bytes(b"not-a-real-video-yet")

            manifest, job_dir = create_job(source, root / "jobs", "video_001")
            runner = PipelineRunner(
                [FakeTranscriptionStage(), FakeTranslationStage()]
            )
            result = runner.run(manifest, job_dir)
            saved = Manifest.load(job_dir / "manifest.json")

            self.assertEqual(result.job.status, JobStatus.COMPLETED)
            self.assertEqual(saved.job.status, JobStatus.COMPLETED)
            self.assertEqual(
                saved.completed_stages,
                ["fake_transcription", "fake_translation"],
            )
            self.assertTrue(all(segment.en for segment in saved.segments))
            self.assertTrue((job_dir / "source.mp4").is_file())

    def test_reports_stage_and_pipeline_progress(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.events: list[tuple[object, ...]] = []

            def start_pipeline(self, job_id: str, stage_count: int) -> None:
                self.events.append(("pipeline_start", job_id, stage_count))

            def start_stage(self, name: str, index: int, stage_count: int) -> None:
                self.events.append(("stage_start", name, index, stage_count))

            def update_stage(
                self, fraction: float | None, message: str | None = None
            ) -> None:
                self.events.append(("stage_update", fraction, message))

            def complete_stage(self, name: str) -> None:
                self.events.append(("stage_complete", name))

            def fail_stage(self, name: str, error: Exception) -> None:
                self.events.append(("stage_fail", name, type(error).__name__))

            def finish_pipeline(self, success: bool) -> None:
                self.events.append(("pipeline_finish", success))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            manifest, job_dir = create_job(source, root / "jobs", "progress_job")
            recorder = Recorder()
            runner = PipelineRunner([FakeTranscriptionStage()], progress=recorder)

            runner.run(manifest, job_dir)

            self.assertEqual(recorder.events[0], ("pipeline_start", "progress_job", 1))
            self.assertIn(
                ("stage_start", "fake_transcription", 1, 1), recorder.events
            )
            self.assertIn(("stage_complete", "fake_transcription"), recorder.events)
            self.assertEqual(recorder.events[-1], ("pipeline_finish", True))


if __name__ == "__main__":
    unittest.main()
