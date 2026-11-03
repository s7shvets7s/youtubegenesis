from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.ingest.youtube import (
    METADATA_MARKER,
    YouTubeDownloadStage,
    is_youtube_url,
)
from ru_en_dubber.pipeline.runner import create_job_from_url


class YouTubeUrlTests(unittest.TestCase):
    def test_accepts_youtube_video_hosts(self) -> None:
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(is_youtube_url("https://youtu.be/abc"))
        self.assertTrue(is_youtube_url("https://youtube.com/shorts/abc"))

    def test_rejects_non_youtube_and_lookalike_hosts(self) -> None:
        self.assertFalse(is_youtube_url("https://example.com/video"))
        self.assertFalse(is_youtube_url("https://youtube.com.evil.example/video"))
        self.assertFalse(is_youtube_url("not a URL"))


class YouTubeDownloadTests(unittest.TestCase):
    def test_downloads_one_video_and_records_metadata(self) -> None:
        url = "https://youtu.be/test-video"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg = root / "ffmpeg.exe"
            ffmpeg.write_bytes(b"exe")
            manifest, job_dir = create_job_from_url(url, root / "jobs", "video_001")
            captured_command: list[str] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                captured_command.extend(command)
                (job_dir / "source.mp4").write_bytes(b"video")
                metadata = {
                    "filepath": str(job_dir / "source.mp4"),
                    "id": "test-video",
                    "title": "Test video",
                    "duration": 12.5,
                    "webpage_url": url,
                }
                return subprocess.CompletedProcess(
                    command, 0, METADATA_MARKER + json.dumps(metadata), ""
                )

            with patch(
                "ru_en_dubber.ingest.youtube.subprocess.run", side_effect=fake_run
            ):
                result = YouTubeDownloadStage(
                    python=sys.executable,
                    ffmpeg=str(ffmpeg),
                ).run(manifest, job_dir)

            self.assertIn("--no-playlist", captured_command)
            self.assertIn("bv*[height<=1080]+ba/b[height<=1080]", captured_command)
            self.assertNotIn("--cookies-from-browser", captured_command)
            self.assertEqual(result.artifacts["source_video"], "source.mp4")
            self.assertEqual(result.metadata["youtube"]["id"], "test-video")
            self.assertEqual(result.metadata["youtube"]["duration"], 12.5)

if __name__ == "__main__":
    unittest.main()
