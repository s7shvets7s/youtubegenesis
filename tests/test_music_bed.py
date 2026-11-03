from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.music.jamendo import allowed_license
from ru_en_dubber.music.library import MusicCatalog, MusicCatalogError
from ru_en_dubber.music.stage import MusicBedStage, score_track, validate_music_plan


def plan(duration: float = 20.0) -> dict:
    return {
        "brief": {
            "genres": ["cinematic"],
            "moods": ["tense"],
            "energy": "medium",
            "instrumental_only": True,
            "search_terms": ["investigation"],
            "avoid": ["comedy"],
        },
        "slots": [{
            "start": 0.0, "target_duration": duration,
            "mood": "tense", "energy": "medium", "reason": "main story",
        }],
    }


def catalog_track(filename: str = "local.mp3") -> dict:
    return {
        "id": "local-track", "file": filename, "title": "Local",
        "artist": "Editor", "provider": "local", "provider_id": None,
        "genres": ["cinematic"], "moods": ["tense"], "energy": "medium",
        "bpm": 90, "instrumental": True, "use_for": ["investigation"],
        "avoid_for": ["comedy"], "license": "owned", "license_url": None,
        "source_url": None, "attribution": None, "approved": True,
    }


class MusicBedContracts(unittest.TestCase):
    def test_validates_closed_plan_and_scores_matching_local_track(self) -> None:
        result = validate_music_plan(plan(), 20.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog.json").write_text(json.dumps({
                "version": 1, "tracks": [catalog_track()],
            }), encoding="utf-8")
            track = MusicCatalog.load(root).tracks[0]
        self.assertEqual(result["brief"]["energy"], "medium")
        self.assertEqual(score_track(track, result), 1.0)
        with self.assertRaises(ValueError):
            validate_music_plan({**plan(), "command": "download"}, 20.0)
        invalid = plan()
        invalid["brief"]["instrumental_only"] = "true"
        with self.assertRaises(ValueError):
            validate_music_plan(invalid, 20.0)

    def test_catalog_rejects_paths_outside_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog.json").write_text(json.dumps({
                "version": 1, "tracks": [catalog_track("../secret.mp3")],
            }), encoding="utf-8")
            catalog = MusicCatalog.load(root)
            self.assertEqual(catalog.tracks, [])
            self.assertEqual(len(catalog.invalid_entries), 1)

    def test_only_accepts_conservative_jamendo_licenses(self) -> None:
        self.assertEqual(allowed_license("https://creativecommons.org/licenses/by/4.0/"), "CC BY")
        self.assertEqual(allowed_license("https://creativecommons.org/publicdomain/zero/1.0/"), "CC0")
        self.assertIsNone(allowed_license("https://creativecommons.org/licenses/by-nc/4.0/"))
        self.assertIsNone(allowed_license("https://creativecommons.org/licenses/by-sa/4.0/"))


class MusicBedStageTests(unittest.TestCase):
    def _stage(self, library: Path, **values) -> MusicBedStage:
        return MusicBedStage(
            mode="hybrid", library=library, server="llama-server",
            model=Path("model.gguf"), ffmpeg="ffmpeg", ffprobe="ffprobe",
            jamendo_client_id="client", min_score=0.72, **values,
        )

    @staticmethod
    def _manifest() -> Manifest:
        return Manifest(
            Job("job", "source.mp4"),
            segments=[Segment(1, 0, 20, "Текст", en="An investigation")],
        )

    def test_uses_local_library_before_calling_jamendo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            library = root / "source" / "music"
            job.mkdir()
            library.mkdir(parents=True)
            (job / "source.mp4").write_bytes(b"video")
            (library / "local.mp3").write_bytes(b"ID3music")
            (library / "catalog.json").write_text(json.dumps({
                "version": 1, "tracks": [catalog_track()],
            }), encoding="utf-8")
            stage = self._stage(library)

            def validate(_ffprobe, path, **_kwargs):
                return 25.0 if path.name != "ai-music.wav" else 20.0

            def render(_ffmpeg, _paths, _durations, output, _target):
                output.write_bytes(b"RIFFmusic")

            with (
                patch.object(stage, "_timeline_duration", return_value=20.0),
                patch.object(stage, "_plan", return_value=plan()),
                patch.object(stage, "_validate_audio", side_effect=validate),
                patch.object(stage, "_render", side_effect=render),
                patch("ru_en_dubber.music.stage.JamendoProvider.search") as search,
                patch("ru_en_dubber.music.stage.QwenTranslationStage._resolve_executable",
                      side_effect=lambda value: value),
            ):
                result = stage.run(self._manifest(), job)

            search.assert_not_called()
            self.assertEqual(result.metadata["music_bed"]["status"], "ready")
            self.assertEqual(result.metadata["music_bed"]["unique_track_count"], 1)
            self.assertTrue((job / "music/ai-music.wav").is_file())
            candidates = json.loads((job / "music/candidates.json").read_text(encoding="utf-8"))
            self.assertTrue(candidates["local"][0]["selected"])
            self.assertEqual(result.artifacts["music_candidates"], "music/candidates.json")

    def test_jamendo_download_is_persisted_in_shared_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            library = root / "source" / "music"
            job.mkdir()
            library.mkdir(parents=True)
            (job / "source.mp4").write_bytes(b"video")
            (library / "catalog.json").write_text(
                '{"version": 1, "tracks": []}', encoding="utf-8"
            )
            stage = self._stage(library)
            candidate = {
                "provider_id": "42", "title": "Remote", "artist": "Artist",
                "duration": 30.0, "download_url": "https://prod-1.storage.jamendo.com/file",
                "source_url": "https://www.jamendo.com/track/42",
                "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            }

            def download(_url, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"ID3remote")

            def render(_ffmpeg, _paths, _durations, output, _target):
                output.write_bytes(b"RIFFmusic")

            with (
                patch.object(stage, "_timeline_duration", return_value=20.0),
                patch.object(stage, "_plan", return_value=plan()),
                patch.object(stage, "_validate_audio", return_value=30.0),
                patch.object(stage, "_render", side_effect=render),
                patch("ru_en_dubber.music.stage.JamendoProvider.search", return_value=[candidate]),
                patch("ru_en_dubber.music.stage.JamendoProvider.download", side_effect=download),
                patch("ru_en_dubber.music.stage.QwenTranslationStage._resolve_executable",
                      side_effect=lambda value: value),
            ):
                result = stage.run(self._manifest(), job)

            saved = library / "jamendo/jamendo-42.mp3"
            catalog = json.loads((library / "catalog.json").read_text(encoding="utf-8"))
            self.assertTrue(saved.is_file())
            self.assertEqual(catalog["tracks"][0]["provider_id"], "42")
            self.assertEqual(catalog["tracks"][0]["file"], "jamendo/jamendo-42.mp3")
            self.assertEqual(result.metadata["music_bed"]["status"], "ready")

    def test_empty_library_without_key_does_not_block_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            library = root / "source" / "music"
            job.mkdir()
            library.mkdir(parents=True)
            (job / "source.mp4").write_bytes(b"video")
            (library / "catalog.json").write_text(
                '{"version": 1, "tracks": []}', encoding="utf-8"
            )
            stage = MusicBedStage(
                mode="hybrid", library=library, server="server",
                model=Path("model"), ffmpeg="ffmpeg", ffprobe="ffprobe",
            )
            with (
                patch.object(stage, "_timeline_duration", return_value=20.0),
                patch("ru_en_dubber.music.stage.QwenTranslationStage._resolve_executable",
                      side_effect=lambda value: value),
            ):
                result = stage.run(self._manifest(), job)
            self.assertEqual(result.metadata["music_bed"]["status"], "no_safe_music")
            self.assertNotIn("music_bed", result.artifacts)

    def test_real_ffmpeg_renders_exact_crossfaded_bed(self) -> None:
        project = Path(__file__).resolve().parents[1]
        ffmpeg = project / ".tools/ffmpeg/bin/ffmpeg.exe"
        ffprobe = project / ".tools/ffmpeg/bin/ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = [root / "one.wav", root / "two.wav"]
            for index, output in enumerate(inputs):
                subprocess.run([
                    str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency={330 + index * 110}:duration=2.5",
                    "-c:a", "pcm_s16le", str(output),
                ], check=True, timeout=30)
            stage = self._stage(root, crossfade_seconds=0.5)
            output = root / "bed.wav"
            stage._render(str(ffmpeg), inputs, [2.5, 2.5], output, 4.0)
            duration = stage._validate_audio(str(ffprobe), output, maximum_duration=4.1)
            self.assertAlmostEqual(duration, 4.0, places=2)


if __name__ == "__main__":
    unittest.main()
