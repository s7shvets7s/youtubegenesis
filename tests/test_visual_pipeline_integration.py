"""Real FFmpeg acceptance checks; API responses are deterministic offline fixtures."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.visuals import VisualRebuildStage
from ru_en_dubber.visuals.animations import fallback_spec
from ru_en_dubber.visuals.models import AssetCandidate, VisualRequest
from ru_en_dubber.visuals.transport import ProviderError


class FixtureProvider:
    media_hosts = frozenset({"upload.wikimedia.org"})
    enabled = True

    def __init__(self, name, assets=(), fail=False):
        self.name, self.assets, self.fail = name, list(assets), fail
        self.calls = 0

    def supports(self, request):
        return True

    def search(self, request):
        self.calls += 1
        if self.fail:
            raise ProviderError("fixture outage")
        return self.assets


class VisualAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.ffmpeg = root / ".tools/ffmpeg/bin/ffmpeg.exe"
        cls.ffprobe = root / ".tools/ffmpeg/bin/ffprobe.exe"
        if not cls.ffmpeg.is_file():
            raise unittest.SkipTest("bundled FFmpeg unavailable")
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.video, cls.photo = cls.root / "fixture.mp4", cls.root / "fixture.png"
        cls.command("-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.video))
        cls.command("-f", "lavfi", "-i", "color=c=orange:s=240x180", "-frames:v", "1", str(cls.photo))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def command(cls, *args):
        subprocess.run([str(cls.ffmpeg), "-nostdin", "-y", "-loglevel", "error", *args], check=True, timeout=30,
                       capture_output=True)

    def job(self, name, providers, **kwargs):
        directory = self.root / name
        directory.mkdir()
        shutil.copyfile(self.video, directory / "source.mp4")
        manifest = Manifest(job=Job(id=name, source_file="source.mp4"), segments=[
            Segment(id=1, start=0, end=1, ru="Тест", en="The steel factory is operating.")])
        stage = VisualRebuildStage(ffmpeg=str(self.ffmpeg), ffprobe=str(self.ffprobe),
                                   providers=providers, planner_mode="legacy", **kwargs)
        return directory, manifest, stage

    @staticmethod
    def asset(provider="wikimedia", media_type="image", **kwargs):
        return AssetCandidate(provider, kwargs.pop("asset_id", "123"), "https://upload.wikimedia.org/fixture.png",
            "https://commons.wikimedia.org/wiki/File:Fixture.png", author="Test", media_type=media_type,
            title="steel factory operating", license_code="CC0", rights_evidence="CC0", **kwargs)

    def download(self, url, destination, hosts, kind):
        shutil.copyfile(self.photo if kind == "image" else self.video, destination)

    def test_five_domains_render_images_and_resume_without_provider_calls(self):
        cases = [
            ("history", "Vietnam patrol", "event", "exact", ("Vietnam",), 1968),
            ("economics", "steel factory", "process", "illustrative", (), None),
            ("science", "microscopic cells", "object", "illustrative", (), None),
            ("biography", "Marie Curie", "person", "exact", ("Marie Curie",), None),
            ("technology", "silicon wafer", "object", "illustrative", (), None),
        ]
        for domain, subject, intent, accuracy, entities, year in cases:
            with self.subTest(domain=domain):
                candidate = replace(self.asset(), title=subject, year_from=year, year_to=year)
                provider = FixtureProvider("wikimedia", [candidate])
                directory, manifest, stage = self.job(domain, [provider])
                req = VisualRequest(1, 0, 1, subject, subject, domain=domain,
                                    visual_intent=intent, accuracy=accuracy, media_types=("image",),
                                    entities=entities, year_from=year, year_to=year)
                with patch("ru_en_dubber.visuals.engine.VisualPlanner.plan", return_value=[req]), patch("ru_en_dubber.visuals.engine.download", side_effect=self.download):
                    stage.run(manifest, directory)
                    provider.fail = True
                    stage.run(manifest, directory)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(manifest.metadata["visual_rebuild"]["no_safe_match_count"], 0)
                credits = json.loads((directory / "visual-rebuild/credits.json").read_text())
                self.assertEqual(credits[0]["provider"], "wikimedia")
                probe = stage._probe(str(self.ffprobe), directory / manifest.artifacts["source_video"])
                self.assertAlmostEqual(stage._video_properties(probe)[3], 1, delta=0.05)
                self.assertEqual([s["codec_type"] for s in probe["streams"]], ["video"])

    def test_failure_of_each_provider_continues_with_another_source(self):
        for name in ("pexels", "pixabay", "wikimedia", "nara", "loc"):
            with self.subTest(provider=name):
                failed = FixtureProvider(name, fail=True)
                backup = FixtureProvider("fixture", [self.asset(provider="fixture", media_type="video")])
                directory, manifest, stage = self.job("failure-" + name, [failed, backup])
                with patch("ru_en_dubber.visuals.engine.download", side_effect=self.download):
                    stage.run(manifest, directory)
                self.assertEqual(manifest.metadata["visual_rebuild"]["no_safe_match_count"], 0)
                report = json.loads((directory / "visual-rebuild/provider-report.json").read_text())
                self.assertEqual(report["errors"][0]["provider"], name)

    def test_corrupt_first_download_tries_next_candidate(self):
        provider = FixtureProvider("fixture", [self.asset(provider="fixture", media_type="video", asset_id="2"),
                                                 self.asset(provider="fixture", media_type="video", asset_id="1")])
        directory, manifest, stage = self.job("corrupt", [provider])
        attempts = []

        def download(url, dest, hosts, kind):
            attempts.append(dest)
            if len(attempts) == 1:
                dest.write_bytes(b"not a video")
            else:
                self.download(url, dest, hosts, kind)

        with patch("ru_en_dubber.visuals.engine.download", side_effect=download):
            stage.run(manifest, directory)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(manifest.metadata["visual_rebuild"]["no_safe_match_count"], 0)

    def test_no_sources_and_exact_number_produce_reported_fallback(self):
        directory, manifest, stage = self.job("no-sources", [])
        stage.run(manifest, directory)
        self.assertEqual(manifest.metadata["visual_rebuild"]["no_safe_match_count"], 1)
        plan = json.loads((directory / "visual-rebuild/visual-plan.json").read_text())
        self.assertEqual(plan["beats"][0]["status"], "no_safe_match")
        self.assertTrue(plan["complete"])

    def test_unlicensed_asset_never_downloaded(self):
        bad = replace(self.asset(media_type="video"), license_code="unknown")
        directory, manifest, stage = self.job("unlicensed", [FixtureProvider("wikimedia", [bad])])
        with patch("ru_en_dubber.visuals.engine.download") as download:
            stage.run(manifest, directory)
        download.assert_not_called()
        self.assertEqual(manifest.metadata["visual_rebuild"]["no_safe_match_count"], 1)

    def test_hybrid_keeps_timeline_renders_animation_and_never_falls_back_to_author(self):
        directory, manifest, stage = self.job("hybrid", [], fallback="original")
        manifest.metadata["video_mode"] = "hybrid"
        content = directory / "content-analysis"
        animations = directory / "visual-rebuild" / "animations"
        content.mkdir()
        animations.mkdir(parents=True)
        episodes = [
            {"index": 1, "source_start": 0, "source_end": 0.4, "action": "replace_media",
             "visual_intent": "person", "subject": "presenter", "contains_author": True},
            {"index": 2, "source_start": 0.4, "source_end": 1, "action": "replace_animation",
             "visual_intent": "map", "subject": "Paris to Berlin", "contains_author": False},
        ]
        (content / "episode-plan.json").write_text(json.dumps({"source_mode": "hybrid", "episodes": episodes}))
        (animations / "animation-plan.json").write_text(json.dumps({"animations": [fallback_spec({
            "index": 2, "visual_intent": "map", "topic": "Route", "subject": "Paris to Berlin"
        })]}))
        manifest.artifacts.update(episode_plan="content-analysis/episode-plan.json",
                                  animation_plan="visual-rebuild/animations/animation-plan.json")
        stage.run(manifest, directory)
        plan = json.loads((directory / "visual-rebuild/visual-plan.json").read_text())
        self.assertEqual([item["status"] for item in plan["beats"]], ["no_safe_match", "animation"])
        self.assertEqual(plan["beats"][0]["fallback"], "original")
        self.assertEqual(manifest.metadata["video_mode"], "hybrid")
        self.assertTrue(manifest.metadata["visual_rebuild"]["source_background_audio"])


if __name__ == "__main__":
    unittest.main()
