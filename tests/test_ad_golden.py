from __future__ import annotations

import json
import unittest
from pathlib import Path


class AdGoldenFixtureTests(unittest.TestCase):
    def test_complete_range_covers_spoken_and_visual_sponsor_tail(self) -> None:
        fixture = Path(__file__).with_name("fixtures") / "ad_sponsor_boundaries.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        expected = payload["expected_source_range"]
        incomplete = payload["known_incomplete_range"]
        evidence = payload["evidence"]

        self.assertEqual(payload["version"], 1)
        self.assertLessEqual(expected["start"], evidence["sponsor_lead_in"]["start"])
        self.assertGreaterEqual(expected["end"], evidence["visual_promo_tail"]["end"])
        self.assertLessEqual(expected["end"], evidence["main_topic_return"]["speech_start"])
        self.assertGreater(incomplete["start"], expected["start"])
        self.assertLess(incomplete["end"], evidence["visual_promo_tail"]["start"])


if __name__ == "__main__":
    unittest.main()
