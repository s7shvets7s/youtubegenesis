from __future__ import annotations

import tempfile
import struct
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.script import (
    NarrationExportStage,
    ScriptTimingStage,
    SubtitleExportStage,
    _read_narration_clip,
    create_job_from_script,
)


class ScriptJobTests(unittest.TestCase):
    def test_script_job_copies_text_and_creates_english_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.md"
            source.write_text(
                "First sentence. Second sentence!\n\nA new paragraph starts here.",
                encoding="utf-8",
            )

            manifest, job_dir = create_job_from_script(source, root / "jobs", "script_1")

            self.assertEqual(manifest.job.source_file, "script-source.mp4")
            self.assertEqual(manifest.metadata["input"]["type"], "script")
            self.assertEqual(manifest.metadata["video_mode"], "rebuild")
            self.assertEqual(manifest.artifacts["script"], "script.md")
            self.assertEqual((job_dir / "script.md").read_bytes(), source.read_bytes())
            self.assertFalse((job_dir / "script-source.mp4").exists())
            self.assertEqual(
                [segment.en for segment in manifest.segments],
                ["First sentence.", "Second sentence!", "A new paragraph starts here."],
            )
            self.assertEqual(
                [segment.ru for segment in manifest.segments],
                [segment.en for segment in manifest.segments],
            )
            self.assertEqual(
                Manifest.load(job_dir / "manifest.json").to_dict(), manifest.to_dict()
            )

    def test_script_job_rejects_invalid_input_before_creating_job(self) -> None:
        cases = (
            ("suffix", "input.pdf", b"An English script."),
            ("blank", "input.txt", b"  \n\n "),
            ("encoding", "input.txt", b"\xff"),
            ("oversize", "input.txt", b"a" * 128001),
            ("language", "input.txt", "Только русский текст.".encode("utf-8")),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label, name, content in cases:
                with self.subTest(label=label):
                    source = root / name
                    source.write_bytes(content)

                    with self.assertRaises(ValueError):
                        create_job_from_script(source, root / "jobs", "bad")

                    self.assertFalse((root / "jobs" / "bad").exists())

    def test_script_job_rejects_job_id_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text("A valid script.", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "job id"):
                create_job_from_script(source, root / "jobs", "../escaped")

            self.assertFalse((root / "escaped").exists())

    def test_script_timing_uses_actual_tts_durations_without_speed_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text(
                "First sentence. Second sentence.\n\nThird paragraph.", encoding="utf-8"
            )
            manifest, job_dir = create_job_from_script(source, root / "jobs", "timed")
            for segment, duration in zip(manifest.segments, (1.8, 2.1, 3.2)):
                path = job_dir / "tts" / f"{segment.id:06d}.wav"
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"audio")
                segment.tts_file = path.relative_to(job_dir).as_posix()
                segment.tts_duration = duration

            result = ScriptTimingStage().run(manifest, job_dir)

            self.assertIs(result, manifest)
            for actual, expected in zip(
                [segment.start for segment in result.segments], [0.0, 2.05, 4.75]
            ):
                self.assertAlmostEqual(actual, expected)
            for actual, expected in zip(
                [segment.end for segment in result.segments], [1.8, 4.15, 7.95]
            ):
                self.assertAlmostEqual(actual, expected)
            self.assertEqual(
                [segment.dub_start for segment in result.segments],
                [segment.start for segment in result.segments],
            )
            self.assertEqual(
                [segment.dub_duration for segment in result.segments], [1.8, 2.1, 3.2]
            )
            self.assertEqual([segment.dub_speed for segment in result.segments], [1.0] * 3)
            self.assertEqual(
                [segment.dub_file for segment in result.segments],
                [segment.tts_file for segment in result.segments],
            )
            self.assertEqual(ScriptTimingStage().name, "script_timing")

    def test_script_timing_requires_present_tts_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text("First sentence.", encoding="utf-8")
            manifest, job_dir = create_job_from_script(source, root / "jobs", "missing")
            manifest.segments[0].tts_file = "tts/000001.wav"
            manifest.segments[0].tts_duration = 2.0

            with self.assertRaisesRegex(ValueError, "TTS audio"):
                ScriptTimingStage().run(manifest, job_dir)

    def test_audio_only_exports_single_wav_with_pauses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text("First sentence. Second sentence.", encoding="utf-8")
            manifest, job_dir = create_job_from_script(source, root / "jobs", "audio")
            for segment in manifest.segments:
                path = job_dir / "tts" / f"{segment.id:06d}.wav"
                path.parent.mkdir(exist_ok=True)
                with wave.open(str(path), "wb") as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(1000)
                    clip.writeframes(bytes([segment.id, 0]) * 1000)
                segment.tts_file = path.relative_to(job_dir).as_posix()
                segment.tts_duration = 1.0

            ScriptTimingStage().run(manifest, job_dir)
            NarrationExportStage().run(manifest, job_dir)
            SubtitleExportStage().run(manifest, job_dir)

            self.assertEqual(manifest.artifacts["narration_audio"], "narration.wav")
            self.assertEqual(manifest.artifacts["subtitles_srt"], "narration.srt")
            captions = (job_dir / "narration.srt").read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,000", captions)
            self.assertIn("00:00:01,250 --> 00:00:02,250", captions)
            with wave.open(str(job_dir / "narration.wav"), "rb") as narration:
                self.assertEqual(narration.getnframes(), 2250)
                self.assertEqual(narration.readframes(1000), bytes([1, 0]) * 1000)
                self.assertEqual(narration.readframes(250), bytes(500))
                self.assertEqual(narration.readframes(1000), bytes([2, 0]) * 1000)

    def test_timed_script_keeps_fixed_cue_starts_and_rejects_overlong_speech(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "beats.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:10,000\nFirst beat.\n\n"
                "2\n00:00:10,000 --> 00:00:20,000\nSecond beat.\n",
                encoding="utf-8",
            )
            manifest, job_dir = create_job_from_script(source, root / "jobs", "beats")
            self.assertTrue(manifest.metadata["script"]["timed_cues"])
            self.assertEqual([segment.start for segment in manifest.segments], [0.0, 10.0])
            for segment, duration in zip(manifest.segments, (9.0, 9.0)):
                path = job_dir / "tts" / f"{segment.id:06d}.wav"
                path.parent.mkdir(exist_ok=True)
                with wave.open(str(path), "wb") as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(1000)
                    clip.writeframes(bytes(2) * round(duration * 1000))
                segment.tts_file = path.relative_to(job_dir).as_posix()
                segment.tts_duration = duration
            ScriptTimingStage().run(manifest, job_dir)
            NarrationExportStage().run(manifest, job_dir)
            SubtitleExportStage().run(manifest, job_dir)
            self.assertEqual([segment.dub_start for segment in manifest.segments], [0.0, 10.0])
            with wave.open(str(job_dir / "narration.wav"), "rb") as narration:
                self.assertEqual(narration.getnframes(), 20_000)
            self.assertIn(
                "00:00:10,000 --> 00:00:19,000",
                (job_dir / "narration.srt").read_text(encoding="utf-8"),
            )
            manifest.segments[0].tts_duration = 12.0
            with self.assertRaisesRegex(ValueError, "cannot reach its speech targets"):
                ScriptTimingStage().run(manifest, job_dir)

    def test_capcut_fixed_scene_choice_preserves_source_and_checks_fit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "beats.srt"
            original = (
                "1\n00:00:00,000 --> 00:00:10,000\nFirst beat.\n\n"
                "2\n00:00:10,000 --> 00:00:20,000\nSecond beat.\n"
            )
            source.write_text(original, encoding="utf-8")
            manifest, job_dir = create_job_from_script(
                source, root / "jobs", "fixed", capcut_scene_seconds=8
            )
            self.assertEqual([s.start for s in manifest.segments], [0, 8])
            self.assertEqual([s.end for s in manifest.segments], [8, 16])
            self.assertEqual((job_dir / "script.srt").read_text(encoding="utf-8"), original)
            self.assertIn("00:00:08,000 --> 00:00:16,000", (
                job_dir / manifest.artifacts["capcut_windows"]
            ).read_text(encoding="utf-8"))
            self.assertEqual(manifest.metadata["script"]["capcut_scene_seconds"], 8)
            for segment in manifest.segments:
                clip = job_dir / "tts" / f"{segment.id:06d}.wav"
                clip.parent.mkdir(exist_ok=True)
                clip.write_bytes(b"audio")
                segment.tts_file = clip.relative_to(job_dir).as_posix()
                segment.tts_duration = 9.0
            with self.assertRaisesRegex(ValueError, "cannot reach its speech targets"):
                ScriptTimingStage().run(manifest, job_dir)
            with self.assertRaisesRegex(ValueError, "2-30 seconds"):
                create_job_from_script(source, root / "jobs", "invalid", capcut_scene_seconds=1)

    def test_small_timed_overflow_is_fitted_without_moving_beat_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "beats.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:10,000\nA short beat.\n", encoding="utf-8"
            )
            manifest, job_dir = create_job_from_script(source, root / "jobs", "fit")
            tts = job_dir / "tts" / "000001.wav"
            tts.parent.mkdir()
            with wave.open(str(tts), "wb") as clip:
                clip.setnchannels(1)
                clip.setsampwidth(2)
                clip.setframerate(1000)
                clip.writeframes(bytes(2) * 10_320)
            segment = manifest.segments[0]
            segment.tts_file = "tts/000001.wav"
            segment.tts_duration = 10.32
            fake_ffmpeg = root / "ffmpeg.exe"
            fake_ffmpeg.write_bytes(b"exe")

            def fitted_audio(command, **_kwargs):
                with wave.open(command[-1], "wb") as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(1000)
                    clip.writeframes(bytes(2) * 9_000)
                return Mock(returncode=0, stderr="")

            with patch("ru_en_dubber.pipeline.script.subprocess.run", side_effect=fitted_audio):
                ScriptTimingStage(ffmpeg=str(fake_ffmpeg)).run(manifest, job_dir)
            self.assertEqual(segment.dub_start, 0)
            self.assertEqual(segment.dub_duration, 9.0)
            self.assertGreater(segment.dub_speed, 1)
            self.assertEqual(segment.dub_file, "timed-tts/000001.wav")
            self.assertEqual(
                manifest.metadata["script_timing"]["speed_adjusted_segment_ids"], [1]
            )

    def test_timed_script_rejects_speech_too_short_for_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "beats.srt"
            source.write_text(
                "1\n00:00:00,000 --> 00:00:10,000\nToo short.\n", encoding="utf-8"
            )
            manifest, job_dir = create_job_from_script(source, root / "jobs", "short")
            tts = job_dir / "tts" / "000001.wav"
            tts.parent.mkdir()
            tts.write_bytes(b"audio")
            manifest.segments[0].tts_file = "tts/000001.wav"
            manifest.segments[0].tts_duration = 6.0

            with self.assertRaisesRegex(ValueError, "cannot reach its speech targets"):
                ScriptTimingStage().run(manifest, job_dir)

    def test_chatterbox_float_wav_can_be_joined_as_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.wav"
            samples = struct.pack("<3f", -1.0, 0.0, 1.0)
            fmt = struct.pack("<HHIIHH", 3, 1, 24_000, 96_000, 4, 32)
            chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
            chunks += b"data" + struct.pack("<I", len(samples)) + samples
            path.write_bytes(b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks)
            clip_format, pcm = _read_narration_clip(path)
            self.assertEqual(clip_format, (1, 2, 24_000))
            self.assertEqual(struct.unpack("<3h", pcm), (-32767, 0, 32767))


if __name__ == "__main__":
    unittest.main()
