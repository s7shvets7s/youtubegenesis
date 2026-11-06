from __future__ import annotations

import subprocess
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch, Mock

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.visuals import VisualRebuildError, VisualRebuildStage
from ru_en_dubber.visuals.engine import render
from ru_en_dubber.visuals.models import AssetCandidate, VisualRequest
from ru_en_dubber.visuals.rebuild import VisualBeat


class VisualRebuildTests(unittest.TestCase):
    def test_rejects_unknown_media_type_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "media type mode"):
            VisualRebuildStage(ffmpeg="ffmpeg", ffprobe="ffprobe", media_type_mode="audio")

    def test_image_mode_does_not_render_planned_animation(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            job_dir = Path(directory)
            animation_plan = job_dir / "animation-plan.json"
            animation_plan.write_text(json.dumps({"animations": [{"episode_index": 1}]}),
                                      encoding="utf-8")
            manifest = Manifest(
                job=Job(id="script", source_file="script-source.mp4"),
                segments=[Segment(id=1, start=0, end=1, ru="A city.", en="A city.")],
                artifacts={"animation_plan": animation_plan.name},
                metadata={"input": {"type": "script"}},
            )
            beat = VisualBeat(index=1, start=0, end=1, segment_ids=(1,),
                              narration="A city.", search_query="city",
                              action="replace_animation", episode_index=1)
            stage = VisualRebuildStage(ffmpeg="ffmpeg", ffprobe="ffprobe",
                                       planner_mode="legacy", providers=[],
                                       media_type_mode="image")
            media = {"streams": [{"codec_type": "video", "codec_name": "h264",
                                  "width": 1280, "height": 720, "avg_frame_rate": "24/1"}],
                     "format": {"format_name": "mov,mp4", "duration": "1"}}

            def fake_render(*args):
                args[3].write_bytes(b"clip")

            def fake_concat(_, __, destination, **___):
                destination.write_bytes(b"rebuilt")

            with (patch.object(stage, "build_beats", return_value=[beat]),
                  patch.object(stage, "_resolve_executable", side_effect=lambda name, _: name),
                  patch.object(stage, "_probe", return_value=media),
                  patch("ru_en_dubber.visuals.engine.render", side_effect=fake_render),
                  patch("ru_en_dubber.visuals.engine.render_animation") as animation,
                  patch.object(stage, "_concat_clips", side_effect=fake_concat)):
                result = stage.run(manifest, job_dir)

            animation.assert_not_called()
            plan = json.loads((job_dir / result.artifacts["visual_plan"]).read_text(encoding="utf-8"))
            self.assertEqual(plan["beats"][0]["action"], "replace_media")
            self.assertEqual(plan["beats"][0]["fallback"], "card")

    def test_script_without_source_uses_timeline_and_card_fallback(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            job_dir = Path(directory)
            manifest = Manifest(
                job=Job(id="script", source_file="script.txt"),
                segments=[Segment(id=1, start=0, end=2.5, ru="A distant city grows.", en="A distant city grows.")],
                metadata={"input": {"type": "script"}},
            )
            stage = VisualRebuildStage(
                ffmpeg="ffmpeg", ffprobe="ffprobe",
                planner_mode="auto",
                providers=[],
                media_type_mode="image",
            )
            media = {"streams": [{"codec_type": "video", "codec_name": "h264",
                                  "width": 1280, "height": 720, "avg_frame_rate": "24/1"}],
                     "format": {"format_name": "mov,mp4", "duration": "2.5"}}
            rendered = []

            def fake_render(*args):
                rendered.append(args[-2])
                args[3].write_bytes(b"clip")

            def fake_concat(_, __, destination, **___):
                destination.write_bytes(b"rebuilt")

            with (patch.object(stage, "_resolve_executable", side_effect=lambda name, _: name),
                  patch.object(stage, "_probe", return_value=media),
                  patch("ru_en_dubber.visuals.engine.render", side_effect=fake_render),
                  patch.object(stage, "_concat_clips", side_effect=fake_concat)):
                result = stage.run(manifest, job_dir)

            plan = json.loads((job_dir / result.artifacts["visual_plan"]).read_text(encoding="utf-8"))
            requests = json.loads((job_dir / "visual-rebuild/visual-requests.json").read_text(encoding="utf-8"))
            self.assertEqual(rendered, ["card"])
            self.assertEqual(plan["beats"][0]["status"], "no_safe_match")
            self.assertEqual(plan["beats"][0]["fallback"], "card")
            self.assertEqual(plan["source_duration"], 2.5)
            self.assertEqual(requests[0]["media_types"], ["image"])
            self.assertEqual(result.artifacts["source_video"], "visual-rebuild/rebuilt-video.mp4")

    def test_media_type_mode_overrides_user_request_before_search(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            job_dir = Path(directory)
            override = job_dir / "override.json"
            override.write_text(json.dumps([{
                "subject": "city skyline", "visual_intent": "atmosphere",
                "accuracy": "illustrative", "entities": [], "media_types": ["video"],
            }]), encoding="utf-8")
            manifest = Manifest(
                job=Job(id="script", source_file="script.txt"),
                segments=[Segment(id=1, start=0, end=1, ru="City skyline.", en="City skyline.")],
                metadata={"input": {"type": "script"}},
            )
            stage = VisualRebuildStage(
                ffmpeg="ffmpeg", ffprobe="ffprobe",
                planner_mode="legacy", providers=[], requests_file=override,
                media_type_mode="image",
            )
            provider = Mock()
            provider.name = "pexels"
            provider.enabled = True
            provider.supports.return_value = True
            provider.media_hosts = frozenset({"videos.pexels.com"})
            provider.search.return_value = [AssetCandidate(
                provider="pexels", asset_id="1", download_url="https://videos.pexels.com/1.mp4",
                page_url="https://www.pexels.com/video/1/", license_code="Pexels",
                rights_evidence="Pexels API", media_type="video", title="city skyline",
            )]
            stage._providers = [provider]
            media = {"streams": [{"codec_type": "video", "codec_name": "h264",
                                  "width": 1280, "height": 720, "avg_frame_rate": "24/1"}],
                     "format": {"format_name": "mov,mp4", "duration": "1"}}

            def fake_render(*args):
                args[3].write_bytes(b"clip")

            def fake_concat(_, __, destination, **___):
                destination.write_bytes(b"rebuilt")

            with (patch.object(stage, "_resolve_executable", side_effect=lambda name, _: name),
                  patch.object(stage, "_probe", return_value=media),
                  patch("ru_en_dubber.visuals.engine.render", side_effect=fake_render),
                  patch("ru_en_dubber.visuals.engine.download") as download,
                  patch.object(stage, "_concat_clips", side_effect=fake_concat)):
                result = stage.run(manifest, job_dir)

            download.assert_not_called()
            request = json.loads((job_dir / "visual-rebuild/visual-requests.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(request["media_types"], ["image"])
            candidates = json.loads((job_dir / "visual-rebuild/asset-candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(candidates[0]["rejection"], "wrong_media_type")
            self.assertEqual(result.metadata["visual_rebuild"]["media_type_mode"], "image")

    def test_real_ffmpeg_renders_one_frame_at_source_tail(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            source = output_dir / "source.mp4"
            output = output_dir / "tail.mp4"
            subprocess.run([
                str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30000/1001:d=1.001",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1.101",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], check=True, timeout=30)
            frame = 1001 / 30000
            source_probe = VisualRebuildStage._probe(str(ffprobe), source)
            video_duration = float(next(
                item["duration"] for item in source_probe["streams"]
                if item.get("codec_type") == "video"
            ))
            container_duration = float(source_probe["format"]["duration"])
            self.assertGreater(container_duration, video_duration)
            render(
                None, str(ffmpeg), source, output, frame, 320, 180,
                30000 / 1001, "original", video_duration,
            )
            payload = VisualRebuildStage._probe(str(ffprobe), output)
            video = [
                item for item in payload["streams"]
                if item.get("codec_type") == "video"
            ]
            self.assertEqual(len(video), 1)
            self.assertGreaterEqual(
                VisualRebuildStage._video_properties(payload)[3], frame - 0.005
            )

    def test_real_ffmpeg_renders_script_fallback_without_source_video(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fallback.mp4"
            render(None, str(ffmpeg), None, output, 0.5, 320, 180, 25.0, "card")
            media = VisualRebuildStage._probe(str(ffprobe), output)
            self.assertTrue(output.is_file())
            self.assertAlmostEqual(VisualRebuildStage._video_properties(media)[3], 0.5,
                                   delta=0.05)

    def test_script_mixed_media_renders_process_animation_alongside_media_beats(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = ffmpeg.with_name("ffprobe.exe")
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        requests = [
            VisualRequest(1, 0, 1, "Fold the paper.", "Fold and press", visual_intent="process"),
            VisualRequest(2, 1, 2, "Look at the paper.", "Paper", visual_intent="object"),
        ]
        manifest = Manifest(
            job=Job(id="script", source_file="script.txt"),
            segments=[Segment(id=r.index, start=r.start, end=r.end, ru=r.narration,
                              en=r.narration) for r in requests],
            metadata={"input": {"type": "script"}},
        )
        stage = VisualRebuildStage(ffmpeg=str(ffmpeg), ffprobe=str(ffprobe), providers=[],
                                   script_animation_mode="templates")
        with (tempfile.TemporaryDirectory(dir=root) as directory,
              patch.object(stage, "build_beats", return_value=[
                  VisualBeat(r.index, r.start, r.end, (r.index,), r.narration, r.subject)
                  for r in requests
              ]),
              patch("ru_en_dubber.visuals.engine.VisualPlanner.plan", return_value=requests)):
            job_dir = Path(directory)
            result = stage.run(manifest, job_dir)
            plan = json.loads((job_dir / result.artifacts["visual_plan"]).read_text("utf-8"))
            self.assertEqual([beat["status"] for beat in plan["beats"]],
                             ["animation", "no_safe_match"])
            self.assertTrue((job_dir / result.artifacts["rebuild_video"]).is_file())

    def test_real_ffmpeg_can_render_and_concatenate_safe_local_clips(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            media = output_dir / "material.mp4"
            subprocess.run(
                [
                    str(ffmpeg),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x180:r=25:d=1",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(media),
                ],
                check=True,
                timeout=30,
            )
            clips_dir = output_dir / "clips"
            clips_dir.mkdir()
            clips = [clips_dir / "beat-0001.mp4", clips_dir / "beat-0002.mp4"]
            for clip in clips:
                VisualRebuildStage._render_beat(
                    str(ffmpeg),
                    media,
                    clip,
                    duration=0.5,
                    width=320,
                    height=180,
                    fps=25.0,
                )
            rebuilt = output_dir / "rebuilt.mp4"
            VisualRebuildStage._concat_clips(
                str(ffmpeg), clips, rebuilt, durations=[0.5, 0.5]
            )
            payload = VisualRebuildStage._probe(str(ffprobe), rebuilt)

            self.assertTrue(rebuilt.is_file())
            self.assertAlmostEqual(
                VisualRebuildStage._video_properties(payload)[3], 1.0, delta=0.1
            )

    def test_groups_translation_into_timeline_beats(self) -> None:
        beats = VisualRebuildStage.build_beats(
            [
                Segment(id=1, start=1.0, end=5.0, ru="a", en="Ancient Rome grew."),
                Segment(id=2, start=5.0, end=11.0, ru="b", en="The city expanded."),
                Segment(id=3, start=12.0, end=18.0, ru="c", en="Trade crossed Europe."),
            ],
            source_duration=20.0,
            target_seconds=10.0,
        )

        self.assertEqual(beats[0].start, 0.0)
        self.assertEqual(beats[-1].end, 20.0)
        self.assertEqual(beats[0].segment_ids, (1, 2))
        self.assertIn("ancient", beats[0].search_query)

    def test_rejects_untrusted_stock_host_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(VisualRebuildError, "allowed HTTPS"):
                VisualRebuildStage._download_video(
                    "https://evil.example/payload.mp4",
                    Path(directory) / "asset.mp4",
                )

    def test_builds_replacement_and_sets_active_source_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            (job_dir / "source.mp4").write_bytes(b"source")
            ffmpeg = job_dir / "ffmpeg.exe"
            ffprobe = job_dir / "ffprobe.exe"
            ffmpeg.write_bytes(b"exe")
            ffprobe.write_bytes(b"exe")
            manifest = Manifest(
                job=Job(id="demo", source_file="source.mp4"),
                segments=[
                    Segment(
                        id=1,
                        start=0.0,
                        end=10.0,
                        ru="История города",
                        en="The ancient city grew beside the river.",
                    )
                ],
            )
            stage = VisualRebuildStage(
                ffmpeg=str(ffmpeg),
                ffprobe=str(ffprobe),
                api_key="test-key",
                planner_mode="legacy",
                providers=[],
            )
            media = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1280,
                        "height": 720,
                        "avg_frame_rate": "30/1",
                    }
                ],
                "format": {"format_name": "mov,mp4", "duration": "10"},
            }
            asset = AssetCandidate(
                provider="pexels",
                license_code="Pexels",
                rights_evidence="Pexels API license",
                asset_id="123",
                download_url="https://videos.pexels.com/video-files/123/123.mp4",
                page_url="https://www.pexels.com/video/123/",
                author="Tester",
                width=1280,
                height=720,
                duration=10.0,
            )

            def download(_: str, destination: Path, *args) -> None:
                destination.write_bytes(b"stock")

            def render(
                _: str,
                __: Path,
                destination: Path,
                **___: object,
            ) -> None:
                destination.write_bytes(b"clip")

            def concatenate(
                _: str, __: list[Path], destination: Path, **___: object
            ) -> None:
                destination.write_bytes(b"rebuilt")

            provider = Mock()
            provider.name = "pexels"
            provider.enabled = True
            provider.supports.return_value = True
            provider.media_hosts = frozenset({"videos.pexels.com"})
            provider.search.return_value = [asset]
            stage._providers = [provider]
            with (
                patch.object(stage, "_probe", return_value=media),
                patch("ru_en_dubber.visuals.engine.download", side_effect=download),
                patch("ru_en_dubber.visuals.engine.render", side_effect=lambda stage, exe, src, dst, *args: dst.write_bytes(b"clip")),
                patch.object(stage, "_concat_clips", side_effect=concatenate),
            ):
                result = stage.run(manifest, job_dir)

            self.assertEqual(result.metadata["video_mode"], "rebuild")
            self.assertEqual(
                result.artifacts["source_video"],
                "visual-rebuild/rebuilt-video.mp4",
            )
            self.assertTrue((job_dir / result.artifacts["visual_plan"]).is_file())
            self.assertFalse(result.metadata["visual_rebuild"]["source_background_audio"])


if __name__ == "__main__":
    unittest.main()
