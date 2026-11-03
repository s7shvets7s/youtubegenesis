from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.visuals.animations import (
    AnimationError,
    AnimationPlanStage,
    fallback_spec,
    parse_agent_plan,
    parse_agent_spec,
    render_animation,
    validate_spec,
)
from ru_en_dubber.visuals.rebuild import VisualRebuildStage


class AnimationContracts(unittest.TestCase):
    @staticmethod
    def freeform_scene() -> dict:
        return {
            "episode_index": 4,
            "kind": "scene",
            "title": "How the signal moves",
            "subtitle": "A visual explanation built from primitives",
            "background": "#121416",
            "accent": "#49C6E5",
            "grid": True,
            "elements": [
                {"id": "source", "type": "circle", "x": 0.2, "y": 0.55,
                 "width": 0.14, "height": 0.24, "color": "#49C6E5",
                 "fill_color": "#183746", "opacity": 1.0},
                {"id": "flow", "type": "arrow", "x": 0.29, "y": 0.55,
                 "x2": 0.7, "y2": 0.55, "color": "#FFD166", "stroke_width": 4},
                {"id": "result", "type": "rectangle", "x": 0.78, "y": 0.55,
                 "width": 0.2, "height": 0.24, "color": "#83C167",
                 "fill_color": "#20382A"},
                {"id": "count", "type": "counter", "text": "Packets",
                 "x": 0.5, "y": 0.78, "value": 0, "color": "#F2F2EE"},
            ],
            "motions": [
                {"target": "source", "property": "scale", "start": 0.0, "end": 0.18,
                 "from": 0.1, "to": 1.0, "easing": "overshoot"},
                {"target": "flow", "property": "draw", "start": 0.18, "end": 0.55,
                 "from": 0.0, "to": 1.0, "easing": "ease_in_out"},
                {"target": "result", "property": "opacity", "start": 0.48, "end": 0.68,
                 "from": 0.0, "to": 1.0, "easing": "ease_out"},
                {"target": "count", "property": "value", "start": 0.28, "end": 0.82,
                 "from": 0.0, "to": 1200.0, "easing": "ease_out"},
            ],
        }

    def test_fallback_compacts_long_transcript_subjects(self) -> None:
        subject = (
            "Many habits, including speech habits, we adopt from others without realizing it. "
            "The people you speak with will influence how you speak. Be mindful of this factor. Second."
        )
        spec = fallback_spec({
            "index": 14,
            "visual_intent": "abstract",
            "topic": "Building a richer vocabulary",
            "subject": subject,
        })

        self.assertTrue(spec["items"])
        self.assertTrue(all(len(item) <= 80 for item in spec["items"]))
        self.assertLessEqual(len(spec["items"]), 5)

    def test_agent_schema_is_closed_and_requires_every_episode(self) -> None:
        episodes = [{"index": 4, "visual_intent": "map", "topic": "Route", "subject": "A to B"}]
        spec = fallback_spec(episodes[0])
        self.assertEqual(parse_agent_plan({"animations": [spec]}, episodes), [spec])
        with self.assertRaises(ValueError):
            validate_spec(dict(spec, command="run me"))
        with self.assertRaises(ValueError):
            parse_agent_plan({"animations": []}, episodes)
        rebound = parse_agent_spec({"animation": dict(spec, episode_index=1)}, 4)
        self.assertEqual(rebound["episode_index"], 4)

    def test_agent_can_compose_a_safe_original_scene(self) -> None:
        scene = self.freeform_scene()
        checked = validate_spec(scene)

        self.assertEqual(checked["kind"], "scene")
        self.assertEqual([item["id"] for item in checked["elements"]],
                         ["source", "flow", "result", "count"])
        self.assertEqual(parse_agent_spec({"animation": scene}, 9)["episode_index"], 9)

        with self.assertRaises(ValueError):
            validate_spec(dict(scene, command="run arbitrary code"))
        with self.assertRaises(ValueError):
            validate_spec(dict(scene, motions=[dict(scene["motions"][0], target="missing")]))

    def test_scene_motion_contract_rejects_unsafe_or_incompatible_actions(self) -> None:
        scene = self.freeform_scene()
        with self.assertRaises(ValueError):
            validate_spec(dict(scene, motions=[{
                "target": "source", "property": "draw", "start": 0.0, "end": 0.5,
                "from": 0.0, "to": 1.0, "easing": "linear",
            }]))
        with self.assertRaises(ValueError):
            validate_spec(dict(scene, elements=[dict(scene["elements"][0], url="https://example.com")]))

    def test_template_mode_uses_episode_plan_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_dir = root / "content-analysis"
            plan_dir.mkdir()
            (plan_dir / "episode-plan.json").write_text(json.dumps({"episodes": [
                {"index": 1, "action": "keep"},
                {"index": 2, "action": "replace_animation", "visual_intent": "process",
                 "topic": "Production", "subject": "mine then refinery then factory"},
            ]}), encoding="utf-8")
            manifest = Manifest(Job("job", "source.mp4"),
                                segments=[Segment(1, 0, 5, "текст", en="Text")],
                                artifacts={"episode_plan": "content-analysis/episode-plan.json"})
            result = AnimationPlanStage(mode="templates").run(manifest, root)
            payload = json.loads((root / result.artifacts["animation_plan"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["planned_count"], 1)
            self.assertEqual(payload["animations"][0]["episode_index"], 2)
            self.assertEqual(payload["animations"][0]["template"], "process")

    def test_episode_beats_preserve_source_gaps_and_author_policy(self) -> None:
        manifest = Manifest(Job("job", "source.mp4"), segments=[
            Segment(1, 0, 4, "а", en="Intro"), Segment(2, 8, 12, "б", en="Route")])
        plan = {"source_mode": "hybrid", "episodes": [
            {"index": 1, "source_start": 0, "source_end": 4, "action": "replace_media",
             "visual_intent": "person", "subject": "narrator", "contains_author": True},
            {"index": 2, "source_start": 8, "source_end": 12, "action": "replace_animation",
             "visual_intent": "map", "subject": "route", "contains_author": False},
        ]}
        beats = VisualRebuildStage.build_episode_beats(manifest, plan, source_duration=12)
        self.assertEqual([beat.action for beat in beats], ["replace_media", "keep", "replace_animation"])
        self.assertTrue(beats[0].contains_author)
        self.assertEqual(beats[-1].episode_index, 2)

    def test_episode_beats_absorb_one_frame_float_gaps(self) -> None:
        manifest = Manifest(Job("job", "source.mp4"), segments=[
            Segment(1, 0, 105, "а", en="Vocabulary")])
        plan = {"source_mode": "rebuild", "episodes": [
            {"index": 1, "source_start": 0, "source_end": 104.04,
             "action": "replace_animation", "visual_intent": "process"},
            {"index": 2, "source_start": 104.08, "source_end": 105,
             "action": "replace_animation", "visual_intent": "title"},
        ]}

        beats = VisualRebuildStage.build_episode_beats(manifest, plan, source_duration=105)

        self.assertEqual(len(beats), 2)
        self.assertAlmostEqual(beats[0].end, beats[1].start)
        self.assertAlmostEqual(sum(beat.duration for beat in beats), 105)

    def test_real_renderer_produces_h264_clip(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "animation.mp4"
            render_animation(str(ffmpeg), output, fallback_spec({
                "index": 1, "visual_intent": "map", "topic": "Route", "subject": "Paris to Berlin"
            }), 0.5, 320, 180, 25)
            probe = subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "stream=codec_name",
                                    "-of", "default=nw=1:nk=1", str(output)], capture_output=True,
                                   text=True, check=True, timeout=30)
            self.assertEqual(probe.stdout.strip(), "h264")

    def test_renderer_kills_and_reaps_ffmpeg_after_timeout(self) -> None:
        process = Mock()
        process.stdin = Mock()
        process.stderr = Mock()
        process.stderr.read.return_value = b""
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired(["ffmpeg"], 120),
            0,
        ]
        frame = Mock()
        frame.tobytes.return_value = b"frame"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timed-out.mp4"
            output.write_bytes(b"partial")
            with (
                patch("ru_en_dubber.visuals.animations.subprocess.Popen", return_value=process),
                patch("ru_en_dubber.visuals.animations._draw_frame", return_value=frame),
                self.assertRaisesRegex(AnimationError, "timed out"),
            ):
                render_animation(
                    "ffmpeg",
                    output,
                    fallback_spec({"index": 1, "visual_intent": "map"}),
                    0.04,
                    16,
                    16,
                    1,
                )
            process.kill.assert_called_once_with()
            self.assertEqual(process.wait.call_count, 2)
            self.assertFalse(output.exists())

    def test_real_renderer_produces_declarative_scene(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "original-scene.mp4"
            render_animation(str(ffmpeg), output, self.freeform_scene(), 0.6, 480, 270, 25)
            probe = subprocess.run([
                str(ffprobe), "-v", "error", "-show_entries", "stream=codec_name,width,height",
                "-of", "json", str(output),
            ], capture_output=True, text=True, check=True, timeout=30)
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual((stream["codec_name"], stream["width"], stream["height"]),
                             ("h264", 480, 270))

    def test_real_renderer_accepts_one_frame_float_rounding(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffmpeg.is_file() or not ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "one-frame.mp4"
            duration = 104.08 - 104.04
            render_animation(str(ffmpeg), output, fallback_spec({
                "index": 13,
                "visual_intent": "abstract",
                "topic": "Vocabulary",
                "subject": "One frame",
            }), duration, 320, 180, 25)
            probe = subprocess.run([
                str(ffprobe), "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(output),
            ], capture_output=True, text=True, check=True, timeout=30)
            self.assertAlmostEqual(float(probe.stdout.strip()), 0.04, places=2)


if __name__ == "__main__":
    unittest.main()
