from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.models.job import Job
from ru_en_dubber.security import MediaSecurityError
from ru_en_dubber.tts.postprocess import SpeechPostprocessError, SpeechPostprocessStage


def make_wave(path: Path, *, seconds: float = 4, silent: bool = False) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        samples = []
        for index in range(round(seconds * 24000)):
            amplitude = 0 if silent else (0.95 if index // 12000 % 2 else 0.08)
            samples.append(round(amplitude * 32767 * math.sin(2 * math.pi * 270 * index / 24000)))
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def manifest_for(root: Path) -> Manifest:
    make_wave(root / "raw.wav")
    return Manifest(
        job=Job(id="speech-test", source_file="source.mp4"),
        segments=[Segment(id=1, speaker="SPEAKER_00", start=0, end=4, ru="Hello.",
                          en="Hello.", tts_file="raw.wav", tts_duration=4)],
    )


class SpeechPostprocessTests(unittest.TestCase):
    def test_rejects_nonfinite_and_out_of_range_settings(self) -> None:
        for settings in ({"target_lufs": float("nan")}, {"target_lufs": -4},
                         {"true_peak_db": 0}, {"true_peak_db": float("inf")},
                         {"timeout": 0}, {"profile": "unknown"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                SpeechPostprocessStage(**settings)

    def test_untrusted_raw_path_is_rejected_before_starting_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = manifest_for(root)
            manifest.segments[0].tts_file = "../outside.wav"
            with patch("ru_en_dubber.tts.postprocess.subprocess.run") as run:
                with self.assertRaises(MediaSecurityError):
                    SpeechPostprocessStage().run(manifest, root)
            run.assert_not_called()

    def test_failure_does_not_publish_partial_segment_paths_or_invalidate_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = manifest_for(root)
            manifest.segments.append(Segment(
                id=2, speaker="SPEAKER_00", start=4, end=8, ru="Again.", en="Again.",
                tts_file="raw.wav", tts_duration=4,
            ))
            manifest.completed_stages = ["tts", "premiere_export"]
            manifest.artifacts["premiere_xml"] = "existing.xml"
            before = manifest.to_dict()
            calls = 0

            def process(ffmpeg: str, source: Path, destination: Path, frames: int) -> dict:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise SpeechPostprocessError("filter failed")
                shutil.copyfile(source, destination)
                return {"duration_seconds": 4, "source_duration_seconds": 4}

            with patch.object(SpeechPostprocessStage, "_executable", return_value="ffmpeg"), \
                    patch.object(SpeechPostprocessStage, "_probe", return_value=4), \
                    patch.object(SpeechPostprocessStage, "_process",
                                 side_effect=process):
                with self.assertRaisesRegex(SpeechPostprocessError, "filter failed"):
                    SpeechPostprocessStage().run(manifest, root)
            self.assertEqual(manifest.to_dict(), before)
            self.assertEqual(calls, 2)

    def test_bypass_restores_raw_provider_paths_and_invalidates_old_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = manifest_for(root)
            manifest.segments[0].tts_file = "already-mastered.wav"
            manifest.segments[0].dub_file = "old-aligned.wav"
            manifest.completed_stages = ["tts", "speech_postprocess", "premiere_export"]
            manifest.artifacts.update(tts_report="tts.json", premiere_xml="old.xml")
            (root / "tts.json").write_text(json.dumps({"segments": [
                {"id": 1, "path": "raw.wav", "duration_seconds": 4},
            ]}), encoding="utf-8")
            SpeechPostprocessStage(profile="off").run(manifest, root)
            self.assertEqual(manifest.segments[0].tts_file, "raw.wav")
            self.assertIsNone(manifest.segments[0].dub_file)
            self.assertNotIn("premiere_xml", manifest.artifacts)
            self.assertEqual(manifest.completed_stages, ["tts"])


class SpeechPostprocessFFmpegTests(unittest.TestCase):
    def setUp(self) -> None:
        bundled = Path(__file__).resolve().parents[1] / ".tools/ffmpeg/bin"
        self.ffmpeg = shutil.which("ffmpeg") or str(bundled / "ffmpeg.exe")
        self.ffprobe = shutil.which("ffprobe") or str(bundled / "ffprobe.exe")
        if not shutil.which(self.ffmpeg) or not shutil.which(self.ffprobe):
            self.skipTest("FFmpeg and FFprobe are unavailable")

    def test_real_output_preserves_raw_timing_limits_peaks_and_reruns_from_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = manifest_for(root)
            raw_hash = hashlib.sha256((root / "raw.wav").read_bytes()).hexdigest()
            stage = SpeechPostprocessStage(ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
            stage.run(manifest, root)
            report = json.loads((root / manifest.artifacts["speech_postprocess_report"])
                                .read_text(encoding="utf-8"))
            item = report["segments"][0]
            self.assertLessEqual(item["output"]["input_tp"], -1.85)
            self.assertEqual(item["duration_seconds"], 4)
            self.assertEqual(hashlib.sha256((root / "raw.wav").read_bytes()).hexdigest(), raw_hash)
            self.assertNotEqual(manifest.segments[0].tts_file, "raw.wav")
            with wave.open(str(root / manifest.segments[0].tts_file), "rb") as audio:
                self.assertEqual((audio.getframerate(), audio.getnframes(), audio.getnchannels()),
                                 (48000, 192000, 1))
            first_path = manifest.segments[0].tts_file
            stage.run(manifest, root)
            second = json.loads((root / manifest.artifacts["speech_postprocess_report"])
                                .read_text(encoding="utf-8"))["segments"][0]
            self.assertEqual(second["source_file"], "raw.wav")
            self.assertEqual(second["source_sha256"], raw_hash)
            self.assertTrue((root / first_path).is_file())

    def test_silence_remains_silent_without_infinite_measurements_or_duration_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = manifest_for(root)
            make_wave(root / "raw.wav", silent=True)
            stage = SpeechPostprocessStage(ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
            stage.run(manifest, root)
            report = json.loads((root / manifest.artifacts["speech_postprocess_report"])
                                .read_text(encoding="utf-8"))
            self.assertEqual(report["segments"][0]["normalization"],
                             "peak_only_no_loudness_boost")
            with wave.open(str(root / manifest.segments[0].tts_file), "rb") as audio:
                self.assertFalse(any(audio.readframes(audio.getnframes())))

    def test_quiet_speech_like_audio_is_not_boosted_by_more_than_six_db(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = manifest_for(root)
            with wave.open(str(root / "raw.wav"), "wb") as audio:
                audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                samples = [round(32 * math.sin(2 * math.pi * 270 * i / 24000))
                           for i in range(96000)]
                audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
            SpeechPostprocessStage(ffmpeg=self.ffmpeg, ffprobe=self.ffprobe).run(manifest, root)
            item = json.loads((root / manifest.artifacts["speech_postprocess_report"])
                              .read_text(encoding="utf-8"))["segments"][0]
            self.assertLessEqual(item["output"]["input_i"] - item["conditioned"]["input_i"], 6.5)


if __name__ == "__main__":
    unittest.main()
