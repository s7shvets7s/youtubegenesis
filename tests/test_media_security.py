from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ru_en_dubber.security import (
    MediaSecurityError,
    resolve_job_path,
    validate_download_url,
    validate_probe_payload,
)


class MediaSecurityTests(unittest.TestCase):
    def test_artifact_cannot_escape_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MediaSecurityError, "outside"):
                resolve_job_path(Path(directory), "../secret.txt", label="artifact")

    def test_download_requires_exact_https_host(self) -> None:
        self.assertEqual(
            validate_download_url(
                "https://videos.pexels.com/video-files/1/1.mp4",
                allowed_hosts={"videos.pexels.com"},
            ),
            "https://videos.pexels.com/video-files/1/1.mp4",
        )
        for value in (
            "http://videos.pexels.com/video.mp4",
            "https://videos.pexels.com.evil.example/video.mp4",
            "https://user@videos.pexels.com/video.mp4",
        ):
            with self.subTest(value=value):
                with self.assertRaises(MediaSecurityError):
                    validate_download_url(
                        value, allowed_hosts={"videos.pexels.com"}
                    )

    def test_probe_rejects_embedded_data_and_resource_bombs(self) -> None:
        safe_video = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30/1",
                }
            ],
            "format": {"format_name": "mov,mp4", "duration": "12.5"},
        }
        validate_probe_payload(safe_video, label="video", require_video=True)

        validate_probe_payload(
            {
                **safe_video,
                "streams": safe_video["streams"]
                + [{"codec_type": "data", "codec_tag_string": "tmcd"}],
            },
            label="video",
            require_video=True,
        )

        with self.assertRaisesRegex(MediaSecurityError, "attachment"):
            validate_probe_payload(
                {
                    **safe_video,
                    "streams": safe_video["streams"]
                    + [{"codec_type": "attachment", "codec_name": "bin_data"}],
                },
                label="video",
                require_video=True,
            )
        with self.assertRaisesRegex(MediaSecurityError, "unsupported data"):
            validate_probe_payload(
                {
                    **safe_video,
                    "streams": safe_video["streams"]
                    + [{"codec_type": "data", "codec_tag_string": "evil"}],
                },
                label="video",
                require_video=True,
            )
        with self.assertRaisesRegex(MediaSecurityError, "dimensions"):
            validate_probe_payload(
                {
                    **safe_video,
                    "streams": [
                        {
                            **safe_video["streams"][0],
                            "width": 50_000,
                            "height": 50_000,
                        }
                    ],
                },
                label="video",
                require_video=True,
            )


if __name__ == "__main__":
    unittest.main()
