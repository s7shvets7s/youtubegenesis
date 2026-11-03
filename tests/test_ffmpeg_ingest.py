from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.cli import default_media_tool
from ru_en_dubber.ingest import (
    FFmpegAudioExtractionStage,
    FFmpegExecutionError,
    FFmpegNotFoundError,
)
from ru_en_dubber.models import Job, Manifest


class FFmpegAudioExtractionTests(unittest.TestCase):
    def test_cli_falls_back_to_tool_name_without_bundled_binary(self) -> None:
        with patch("pathlib.Path.is_file", return_value=False):
            self.assertEqual(default_media_tool("ffmpeg"), "ffmpeg")

    def test_extracts_audio_and_records_media_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            (job_dir / "source.mp4").write_bytes(b"video")
            manifest = Manifest(job=Job(id="video_001"))

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if "ffprobe" in command[0]:
                    payload = {
                        "streams": [
                            {
                                "codec_type": "audio",
                                "codec_name": "aac",
                                "sample_rate": "44100",
                                "channels": 2,
                            }
                        ],
                        "format": {"duration": "12.5", "format_name": "mov,mp4"},
                    }
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

                Path(command[-1]).write_bytes(b"wav")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch(
                    "ru_en_dubber.ingest.ffmpeg.shutil.which",
                    side_effect=lambda name: f"C:/tools/{name}.exe",
                ),
                patch("ru_en_dubber.ingest.ffmpeg.subprocess.run", side_effect=fake_run),
            ):
                result = FFmpegAudioExtractionStage().run(manifest, job_dir)

            self.assertEqual(result.artifacts["extracted_audio"], "audio.wav")
            self.assertEqual(result.metadata["source_media"]["duration"], 12.5)
            self.assertEqual(result.metadata["source_media"]["audio_codec"], "aac")
            self.assertEqual(result.metadata["extracted_audio"]["sample_rate"], 48_000)
            self.assertTrue((job_dir / "audio.wav").is_file())

    def test_missing_ffmpeg_has_actionable_error(self) -> None:
        with patch("ru_en_dubber.ingest.ffmpeg.shutil.which", return_value=None):
            with self.assertRaisesRegex(FFmpegNotFoundError, "install FFmpeg"):
                FFmpegAudioExtractionStage().run(
                    Manifest(job=Job(id="video_001")), Path("unused")
                )

    def test_video_without_audio_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            (job_dir / "source.mp4").write_bytes(b"video")
            probe_result = subprocess.CompletedProcess(
                ["ffprobe"],
                0,
                json.dumps({"streams": [{"codec_type": "video"}], "format": {}}),
                "",
            )

            with (
                patch(
                    "ru_en_dubber.ingest.ffmpeg.shutil.which",
                    side_effect=lambda name: f"C:/tools/{name}.exe",
                ),
                patch("ru_en_dubber.ingest.ffmpeg.subprocess.run", return_value=probe_result),
            ):
                with self.assertRaisesRegex(FFmpegExecutionError, "audio stream"):
                    FFmpegAudioExtractionStage().run(
                        Manifest(job=Job(id="video_001")), job_dir
                    )


if __name__ == "__main__":
    unittest.main()
