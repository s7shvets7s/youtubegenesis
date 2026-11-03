from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Job, Manifest, Segment


class SegmentTests(unittest.TestCase):
    def test_non_finite_numeric_fields_are_rejected(self) -> None:
        numeric_fields = (
            "id",
            "start",
            "end",
            "tts_duration",
            "dub_start",
            "dub_duration",
            "dub_speed",
        )
        for field in numeric_fields:
            for value in (float("nan"), float("inf"), float("-inf")):
                values: dict[str, Any] = {
                    "id": 1,
                    "start": 0.0,
                    "end": 1.0,
                    "ru": "Текст",
                }
                values[field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        Segment(**values)

    def test_duration_and_timing_error_are_derived(self) -> None:
        segment = Segment(
            id=1,
            start=12.42,
            end=17.61,
            ru="Тестовая реплика.",
            tts_duration=5.5,
        )

        self.assertAlmostEqual(segment.duration, 5.19)
        self.assertAlmostEqual(segment.timing_error_ratio, (5.5 - 5.19) / 5.19)

    def test_aligned_dubbing_fields_survive_round_trip(self) -> None:
        segment = Segment(
            id=1,
            start=1.0,
            end=3.0,
            ru="Тест",
            dub_file="aligned-tts/000001.wav",
            dub_start=1.1,
            dub_duration=1.8,
            dub_speed=1.12,
        )

        loaded = Segment.from_dict(segment.to_dict())

        self.assertEqual(loaded.dub_file, segment.dub_file)
        self.assertEqual(loaded.dub_start, 1.1)
        self.assertEqual(loaded.dub_duration, 1.8)
        self.assertEqual(loaded.dub_speed, 1.12)

    def test_invalid_time_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Segment(id=1, start=3.0, end=3.0, ru="Текст")

    def test_inconsistent_stored_duration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duration"):
            Segment.from_dict(
                {"id": 1, "start": 0, "end": 2, "duration": 9, "ru": "Текст"}
            )


class ManifestTests(unittest.TestCase):
    def test_save_rejects_non_finite_json_values(self) -> None:
        manifest = Manifest(
            job=Job(id="video_001"),
            metadata={"confidence": float("nan")},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with self.assertRaises(ValueError):
                manifest.save(path)
            self.assertFalse(path.exists())

    def test_json_round_trip_preserves_unicode(self) -> None:
        now = datetime.now(UTC)
        manifest = Manifest(
            job=Job(id="video_001", created_at=now, updated_at=now),
            segments=[Segment(id=1, start=0, end=2.5, ru="Привет", en="Hello")],
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            manifest.save(path)
            loaded = Manifest.load(path)

        self.assertEqual(loaded.to_dict(), manifest.to_dict())

    def test_duplicate_segment_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            Manifest(
                job=Job(id="video_001"),
                segments=[
                    Segment(id=1, start=0, end=1, ru="Один"),
                    Segment(id=1, start=1, end=2, ru="Два"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
