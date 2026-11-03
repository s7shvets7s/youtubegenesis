from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.editing import DialogueTimingStage
from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.security import MediaSecurityError


class DialogueTimingTests(unittest.TestCase):
    def test_ffmpeg_timeout_is_bounded_and_actionable(self) -> None:
        stage = DialogueTimingStage(
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            command_timeout=15.0,
        )

        with patch(
            "ru_en_dubber.editing.timing.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["ffmpeg"], 15.0),
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out after 15 seconds"):
                stage._run(["ffmpeg"], "could not process audio")

        self.assertEqual(run.call_args.kwargs["timeout"], 15.0)

    def test_rejects_tts_artifact_outside_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            (root / "outside.wav").write_bytes(b"audio")
            manifest = Manifest(
                job=Job(id="job"),
                segments=[
                    Segment(
                        id=1,
                        start=0.0,
                        end=1.0,
                        ru="Текст",
                        tts_file="../outside.wav",
                        tts_duration=1.0,
                    )
                ],
            )
            stage = DialogueTimingStage(ffmpeg="ffmpeg", ffprobe="ffprobe")

            with patch.object(
                stage,
                "_resolve_executable",
                side_effect=AssertionError("FFmpeg must not be resolved"),
            ):
                with self.assertRaises(MediaSecurityError):
                    stage.run(manifest, job_dir)

    def test_balances_neighboring_lines_and_preserves_source_pause(self) -> None:
        segments = [
            Segment(id=1, start=0.0, end=2.0, ru="Один", speaker="A"),
            Segment(id=2, start=2.1, end=4.1, ru="Два", speaker="A"),
            Segment(id=3, start=6.0, end=8.0, ru="Три", speaker="A"),
        ]
        stage = DialogueTimingStage(ffmpeg="ffmpeg", ffprobe="ffprobe")

        placements = stage.plan(segments, {1: 3.0, 2: 1.0, 3: 2.0})
        by_id = {item.segment_id: item for item in placements}

        self.assertAlmostEqual(by_id[1].speed, 1.0)
        self.assertAlmostEqual(by_id[2].start, 3.1)
        self.assertAlmostEqual(
            by_id[2].start + by_id[2].duration,
            4.1,
        )
        self.assertAlmostEqual(by_id[3].start, 6.0)
        self.assertNotEqual(by_id[2].block, by_id[3].block)

    def test_different_speakers_can_overlap_on_separate_tracks(self) -> None:
        segments = [
            Segment(id=1, start=0.0, end=2.0, ru="Один", speaker="A"),
            Segment(id=2, start=1.0, end=3.0, ru="Два", speaker="B"),
        ]
        stage = DialogueTimingStage(ffmpeg="ffmpeg", ffprobe="ffprobe")

        placements = stage.plan(segments, {1: 2.0, 2: 2.0})

        self.assertEqual(placements[0].start, 0.0)
        self.assertEqual(placements[1].start, 1.0)

    def test_marks_extreme_block_for_translation_rewrite_and_clamps_speed(self) -> None:
        segment = Segment(id=1, start=0.0, end=1.0, ru="Очень короткое окно")
        stage = DialogueTimingStage(
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            maximum_speed=1.2,
        )

        placement = stage.plan([segment], {1: 3.0})[0]

        self.assertEqual(placement.speed, 1.2)
        self.assertTrue(placement.needs_rewrite)
        self.assertAlmostEqual(placement.duration, 2.5)


if __name__ == "__main__":
    unittest.main()
