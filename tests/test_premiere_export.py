from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.editing import PremiereExportError, PremiereExportStage
from ru_en_dubber.editing.premiere import Episode
from ru_en_dubber.models import Job, Manifest, Segment


class PremiereExportTests(unittest.TestCase):

    def test_script_marks_unmatched_visuals_in_premiere_xml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job_dir = Path(directory)
            plan = job_dir / "visual-plan.json"
            plan.write_text(json.dumps({"beats": [
                {"index": 1, "start": 0, "end": 2, "status": "no_safe_match",
                 "narration": "A distant city grows."},
            ]}), encoding="utf-8")
            voice = job_dir / "voice.wav"
            video = job_dir / "scene.mp4"
            voice.write_bytes(b"voice")
            video.write_bytes(b"video")
            manifest = Manifest(
                job=Job(id="script", source_file="script-source.mp4"),
                segments=[Segment(id=1, start=0, end=2, ru="A distant city grows.",
                                  en="A distant city grows.", tts_file="voice.wav",
                                  tts_duration=2)],
                artifacts={"visual_plan": "visual-plan.json"},
                metadata={"input": {"type": "script"}},
            )
            stage = PremiereExportStage(python="python", ffmpeg="ffmpeg", ffprobe="ffprobe")
            reviews = stage._visual_review_beats(manifest, job_dir)
            xml_path = job_dir / "premiere_sequence.xml"
            stage._write_xml(
                xml_path, manifest,
                {"streams": [{"codec_type": "video", "avg_frame_rate": "24/1",
                              "width": 1280, "height": 720}]},
                [Episode(index=1, start=0, end=2, shot_indices=(1,), path=video)],
                {1: voice}, None, 2.0, visual_review_beats=reviews,
            )
            markers = ET.parse(xml_path).findall(".//sequence/marker")
            self.assertEqual([marker.findtext("name") for marker in markers],
                             ["Line 0001", "REPLACE VISUAL 0001"])
            self.assertIn("Script: A distant city grows.", markers[0].findtext("comment"))
            self.assertEqual((markers[1].findtext("in"), markers[1].findtext("out")),
                             ("0", "48"))

    def test_scene_detection_timeout_is_actionable(self) -> None:
        stage = PremiereExportStage(
            python="python",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            detection_timeout=7200,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "shots.json"
            with patch(
                "ru_en_dubber.editing.premiere.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["python", "detector"], 7200),
            ) as run:
                with self.assertRaisesRegex(
                    PremiereExportError, "scene detection timed out.*7200s"
                ):
                    stage._detect_shots(
                        "python", Path("source.mp4"), output, source_duration=100.0
                    )
        self.assertEqual(run.call_args.kwargs["timeout"], 7200.0)

    def test_creates_editable_package_with_video_voice_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            tts_dir = job_dir / "tts"
            job_dir.mkdir()
            tts_dir.mkdir()
            python = root / "python.exe"
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            for executable in (python, ffmpeg, ffprobe):
                executable.write_bytes(b"executable")
            (job_dir / "source.mp4").write_bytes(b"video")
            (job_dir / "avatar-overlay.mov").write_bytes(b"avatar")
            (job_dir / "background.wav").write_bytes(b"background")
            (job_dir / "music").mkdir()
            (job_dir / "music/ai-music.wav").write_bytes(b"music")
            (job_dir / "music/music-credits.json").write_text(
                '{"version": 1, "tracks": []}', encoding="utf-8"
            )
            (tts_dir / "000001.wav").write_bytes(b"voice one")
            (tts_dir / "000002.wav").write_bytes(b"voice two")
            manifest = Manifest(
                job=Job(id="demo", source_file="source.mp4"),
                segments=[
                    Segment(
                        id=1,
                        start=0.25,
                        end=2.0,
                        ru="Первая.",
                        en="First.",
                        tts_file="tts/000001.wav",
                        tts_duration=1.5,
                    ),
                    Segment(
                        id=2,
                        start=1.5,
                        end=3.5,
                        ru="Вторая.",
                        en="Second.",
                        tts_file="tts/000002.wav",
                        tts_duration=2.2,
                        speaker="SPEAKER_01",
                    ),
                ],
                artifacts={
                    "background": "background.wav",
                    "avatar_base_video": "source.mp4",
                    "avatar_overlay_video": "avatar-overlay.mov",
                    "episode_plan": "episode-plan.json",
                    "music_bed": "music/ai-music.wav",
                    "music_credits": "music/music-credits.json",
                },
                metadata={
                    "video_mode": "rebuild",
                    "tts": {"sample_rate": 24_000},
                    "avatar": {"size": 360, "position": "bottom-right", "margin": 48},
                },
            )
            (job_dir / "episode-plan.json").write_text(json.dumps({
                "episodes": [{
                    "index": 1,
                    "source_start": 0.5,
                    "source_end": 1.5,
                    "timeline_start": 0.5,
                    "timeline_end": 1.5,
                    "action": "localize_text",
                    "detected_text": "Следующий эпизод",
                    "translated_text": "Next episode",
                    "text_role": "editorial_overlay",
                    "text_cleanup_status": "static_ready",
                    "text_regions": [{
                        "detected_text": "Следующий эпизод",
                        "translated_text": "Next episode",
                        "bbox": {"x": 0.25, "y": 0.1, "width": 0.5, "height": 0.12},
                        "motion": "static",
                        "background": "simple",
                        "visible_in_all_frames": True,
                        "confidence": 0.98,
                        "localizable": True,
                        "style": {"alignment": "center", "text_color": "#FFFFFF"},
                    }],
                }]
            }), encoding="utf-8")
            stage = PremiereExportStage(
                python=str(python),
                ffmpeg=str(ffmpeg),
                ffprobe=str(ffprobe),
            )
            detection = {
                "provider": "pyscenedetect",
                "scenes": [
                    {"index": 1, "start_seconds": 0.0, "end_seconds": 2.0},
                    {"index": 2, "start_seconds": 2.0, "end_seconds": 4.0},
                ],
            }
            media = {
                "streams": [
                    {
                        "codec_type": "video",
                        "avg_frame_rate": "30000/1001",
                        "width": 1920,
                        "height": 1080,
                    }
                ],
                "format": {"duration": "4.0"},
            }

            def render(
                _: str,
                __: Path,
                output_dir: Path,
                episodes: list[Episode],
                **kwargs: object,
            ) -> list[Episode]:
                for item in kwargs.get("text_localizations", []):
                    item["cleanup_status"] = "passed"
                rendered = []
                for episode in episodes:
                    output = output_dir / f"scene_{episode.index:04d}.mp4"
                    output.write_bytes(b"clip")
                    rendered.append(
                        Episode(
                            index=episode.index,
                            start=episode.start,
                            end=episode.end,
                            shot_indices=episode.shot_indices,
                            path=output,
                        )
                    )
                return rendered

            with (
                patch.object(stage, "_probe", return_value=media),
                patch.object(
                    stage,
                    "_select_render_backend",
                    return_value=stage._cpu_backend(),
                ),
                patch.object(stage, "_detect_shots", return_value=detection),
                patch.object(stage, "_render_episodes", side_effect=render),
            ):
                result = stage.run(manifest, job_dir)

            export_dir = job_dir / "premiere-export"
            xml_path = export_dir / "premiere_sequence.xml"
            xml_root = ET.parse(xml_path).getroot()
            video_tracks = xml_root.findall(".//video/track")
            self.assertEqual([track.findtext("name") for track in video_tracks],
                             ["Source Video", "Localized Text", "Avatar"])
            self.assertEqual(len(video_tracks[0].findall("clipitem")), 2)
            self.assertEqual(video_tracks[1].findtext("generatoritem/effect/parameter/value"),
                             "Next episode")
            text_parameters = {
                parameter.findtext("parameterid"): parameter
                for parameter in video_tracks[1].findall("generatoritem/effect/parameter")
            }
            self.assertEqual(text_parameters["fontname"].findtext("value"), "Arial")
            self.assertEqual(text_parameters["fontalign"].findtext("value"), "2")
            self.assertEqual(text_parameters["origin"].findtext("value/horiz"), "0.000000")
            self.assertEqual(text_parameters["origin"].findtext("value/vert"), "-0.340000")
            self.assertIsNone(video_tracks[1].find("generatoritem/filter"))
            self.assertEqual(len(video_tracks[2].findall("clipitem")), 2)
            self.assertTrue((export_dir / "overlays/avatar-overlay.mov").is_file())
            self.assertEqual(len(xml_root.findall(".//marker")), 2)
            audio_names = [
                node.text for node in xml_root.findall(".//audio/track/clipitem/name")
            ]
            path_urls = [node.text for node in xml_root.findall(".//pathurl")]
            self.assertIn("background.wav", audio_names)
            self.assertIn("ai-music.wav", audio_names)
            self.assertIn("line_0001_000000250.wav", audio_names)
            self.assertIn("line_0002_000001500.wav", audio_names)
            self.assertGreaterEqual(len(xml_root.findall(".//audio/track")), 3)
            track_names = [
                node.text for node in xml_root.findall(".//audio/track/name")
            ]
            self.assertIn("Voice — SPEAKER_00", track_names)
            self.assertIn("Voice — SPEAKER_01", track_names)
            self.assertEqual(track_names[:2], ["Original Background", "AI Music"])
            self.assertTrue((export_dir / "music-credits.json").is_file())
            self.assertTrue(all(url.startswith("file://localhost/") for url in path_urls))
            voice_names = [name for name in audio_names if name.startswith("line_")]
            self.assertTrue((export_dir / "voice" / voice_names[0]).is_file())
            self.assertEqual(
                result.artifacts["premiere_xml"],
                "premiere-export/premiere_sequence.xml",
            )
            with (export_dir / "dialogue.csv").open(
                encoding="utf-8-sig", newline=""
            ) as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["en"], "First.")

    def test_groups_short_shots_and_splits_long_shots(self) -> None:
        stage = PremiereExportStage(
            python="python",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            minimum_episode_seconds=2.0,
            maximum_episode_seconds=5.0,
        )
        episodes = stage._group_episodes(
            [
                {"index": 1, "start_seconds": 0.0, "end_seconds": 0.8},
                {"index": 2, "start_seconds": 0.8, "end_seconds": 2.4},
                {"index": 3, "start_seconds": 2.4, "end_seconds": 9.4},
            ]
        )
        self.assertEqual((episodes[0].start, episodes[0].end), (0.0, 2.4))
        self.assertTrue(all(episode.duration <= 5.0 for episode in episodes))
        self.assertEqual(episodes[-1].end, 9.4)

    def test_allocates_each_speaker_to_a_separate_track_group(self) -> None:
        segments = [
            Segment(
                id=1,
                start=0.0,
                end=2.0,
                ru="Первый",
                speaker="SPEAKER_00",
                tts_duration=2.5,
            ),
            Segment(
                id=2,
                start=1.0,
                end=2.0,
                ru="Второй",
                speaker="SPEAKER_01",
                tts_duration=1.0,
            ),
            Segment(
                id=3,
                start=2.0,
                end=3.0,
                ru="Снова первый",
                speaker="SPEAKER_00",
                tts_duration=1.0,
            ),
        ]

        tracks = PremiereExportStage._allocate_speaker_tracks(segments)

        self.assertEqual(
            [(speaker, lane, [item.id for item in items]) for speaker, lane, items in tracks],
            [
                ("SPEAKER_00", 1, [1]),
                ("SPEAKER_00", 2, [3]),
                ("SPEAKER_01", 1, [2]),
            ],
        )

    def test_aligned_lines_for_one_speaker_share_one_track(self) -> None:
        segments = [
            Segment(
                id=1,
                start=0.0,
                end=2.0,
                ru="Один",
                tts_duration=3.0,
                dub_start=0.0,
                dub_duration=2.0,
            ),
            Segment(
                id=2,
                start=1.8,
                end=3.8,
                ru="Два",
                tts_duration=2.5,
                dub_start=2.0,
                dub_duration=1.8,
            ),
        ]

        tracks = PremiereExportStage._allocate_speaker_tracks(segments)

        self.assertEqual(len(tracks), 1)
        self.assertEqual([item.id for item in tracks[0][2]], [1, 2])

    def test_uses_nvenc_with_software_decode_for_av1(self) -> None:
        stage = PremiereExportStage(
            python="python",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            device="auto",
        )
        media = {"streams": [{"codec_type": "video", "codec_name": "av1"}]}

        with patch.object(stage, "_probe_backend", return_value=None) as probe:
            backend = stage._select_render_backend(
                "ffmpeg", Path("source.mp4"), media
            )

        self.assertEqual(backend.device, "cuda")
        self.assertEqual(backend.decoder, "software")
        self.assertEqual(backend.encoder, "h264_nvenc")
        self.assertEqual(probe.call_count, 1)

    def test_falls_back_to_cpu_when_nvenc_probe_fails(self) -> None:
        stage = PremiereExportStage(
            python="python",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        )
        media = {"streams": [{"codec_type": "video", "codec_name": "av1"}]}

        with patch.object(stage, "_probe_backend", return_value="no compatible GPU"):
            backend = stage._select_render_backend(
                "ffmpeg", Path("source.mp4"), media
            )

        self.assertEqual(backend.device, "cpu")
        self.assertEqual(backend.encoder, "libx264")
        self.assertIn("NVENC unavailable", backend.fallback_reason or "")

    def test_episode_uses_fast_input_seek_before_source(self) -> None:
        stage = PremiereExportStage(
            python="python",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            device="cpu",
        )
        episode = Episode(index=1, start=42.5, end=45.0, shot_indices=(1,))
        completed = subprocess.CompletedProcess([], 0, "", "")

        with patch(
            "ru_en_dubber.editing.premiere.subprocess.run",
            return_value=completed,
        ) as run:
            stage._run_episode(
                "ffmpeg",
                Path("source.mp4"),
                Path("output.mp4"),
                episode,
                stage._cpu_backend(),
            )

        command = run.call_args.args[0]
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")

    def test_static_text_cleanup_uses_opaque_padded_cover(self) -> None:
        stage = PremiereExportStage(
            python="python", ffmpeg="ffmpeg", ffprobe="ffprobe", device="cpu"
        )
        episode = Episode(index=1, start=10.0, end=14.0, shot_indices=(1,))
        filters = stage._text_cover_filters(episode, [{
            "source_start": 11.0,
            "source_end": 13.0,
            "bbox": {"x": 0.25, "y": 0.2, "width": 0.5, "height": 0.1},
            "style": {"panel_color": "#223344"},
        }], 1920, 1080)

        self.assertEqual(len(filters), 1)
        self.assertIn(
            "drawbox=x=384:y=197:w=1152:h=146:color=0x223344:t=fill",
            filters[0],
        )
        self.assertNotIn("delogo", filters[0])
        self.assertIn("between(t,1.000000,3.000000)", filters[0])

    def test_real_ffmpeg_removes_pixels_inside_static_text_box(self) -> None:
        project = Path(__file__).resolve().parents[1]
        ffmpeg = project / ".tools/ffmpeg/bin/ffmpeg.exe"
        if not ffmpeg.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            cleaned = root / "cleaned.mp4"
            subprocess.run([
                str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=0x204060:s=640x360:r=25:d=1",
                "-vf", "drawbox=x=200:y=130:w=240:h=60:color=white:t=fill",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True)
            stage = PremiereExportStage(
                python="python", ffmpeg=str(ffmpeg), ffprobe="ffprobe", device="cpu"
            )
            episode = Episode(index=1, start=0.0, end=1.0, shot_indices=(1,))
            filters = stage._text_cover_filters(episode, [{
                "source_start": 0.0,
                "source_end": 1.0,
                "bbox": {
                    "x": 200 / 640,
                    "y": 130 / 360,
                    "width": 240 / 640,
                    "height": 60 / 360,
                },
                "style": {"panel_color": "#121416"},
            }], 640, 360)
            result = stage._run_episode(
                str(ffmpeg), source, cleaned, episode, stage._cpu_backend(),
                video_filters=filters,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            def center_pixel(path: Path) -> tuple[int, int, int]:
                frame = subprocess.run([
                    str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-",
                ], check=True, capture_output=True).stdout
                offset = (180 * 640 + 320) * 3
                return tuple(frame[offset:offset + 3])

            source_pixel = center_pixel(source)
            cleaned_pixel = center_pixel(cleaned)

        self.assertGreater(sum(source_pixel), 700)
        self.assertLess(sum(cleaned_pixel), 260)

    def test_failed_cleanup_keeps_original_and_suppresses_english_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = PremiereExportStage(
                python="python", ffmpeg="ffmpeg", ffprobe="ffprobe", device="cpu"
            )
            localization = {
                "source_start": 1.0,
                "source_end": 2.0,
                "start": 1.0,
                "end": 2.0,
                "bbox": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.1},
                "text": "TITLE",
                "source_text": "ЗАГОЛОВОК",
            }

            def render_file(
                _ffmpeg: str,
                _source: Path,
                output: Path,
                _episode: Episode,
                _backend: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                if kwargs.get("video_filters"):
                    return subprocess.CompletedProcess([], 1, "", "bad cleanup")
                output.write_bytes(b"original video")
                return subprocess.CompletedProcess([], 0, "", "")

            with patch.object(stage, "_run_episode", side_effect=render_file):
                stage._render_episodes(
                    "ffmpeg",
                    root / "source.mp4",
                    root,
                    [Episode(index=1, start=0.0, end=3.0, shot_indices=(1,))],
                    text_localizations=[localization],
                    frame_size=(1920, 1080),
                )

        self.assertEqual(localization["cleanup_status"], "text_cleanup_review")
        self.assertEqual(PremiereExportStage._localized_text_overlays([localization]), [])

    def test_static_text_candidates_exclude_ads_and_non_cyrillic_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "episode-plan.json"
            common_region = {
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.1},
                "motion": "static",
                "background": "simple",
                "visible_in_all_frames": True,
                "confidence": 0.99,
                "localizable": True,
            }
            plan.write_text(json.dumps({"episodes": [
                {
                    "index": 1,
                    "source_start": 2.0,
                    "source_end": 4.0,
                    "action": "localize_text",
                    "text_role": "editorial_overlay",
                    "text_cleanup_status": "static_ready",
                    "text_regions": [dict(
                        common_region,
                        detected_text="ЗАГОЛОВОК",
                        translated_text="TITLE",
                    )],
                },
                {
                    "index": 2,
                    "source_start": 4.05,
                    "source_end": 6.0,
                    "action": "localize_text",
                    "text_role": "editorial_overlay",
                    "text_cleanup_status": "static_ready",
                    "text_regions": [dict(
                        common_region,
                        bbox={"x": 0.108, "y": 0.1, "width": 0.3, "height": 0.1},
                        detected_text="ЗАГОЛОВОК",
                        translated_text="TITLE",
                    )],
                },
                {
                    "index": 3,
                    "source_start": 10.0,
                    "source_end": 12.0,
                    "action": "localize_text",
                    "text_role": "editorial_overlay",
                    "text_cleanup_status": "static_ready",
                    "text_regions": [dict(
                        common_region,
                        detected_text="РЕКЛАМА",
                        translated_text="ADVERTISEMENT",
                    )],
                },
                {
                    "index": 4,
                    "source_start": 14.0,
                    "source_end": 16.0,
                    "action": "localize_text",
                    "text_role": "title_card",
                    "text_cleanup_status": "static_ready",
                    "text_regions": [dict(
                        common_region,
                        detected_text="ENGLISH TITLE",
                        translated_text="ENGLISH TITLE",
                    )],
                },
            ]}), encoding="utf-8")
            manifest = Manifest(
                job=Job(id="demo", source_file="source.mp4"),
                artifacts={"episode_plan": "episode-plan.json"},
            )

            result = PremiereExportStage._static_text_localizations(
                manifest, root, [{"start": 9.0, "end": 13.0}]
            )

        self.assertEqual([item["source_text"] for item in result], ["ЗАГОЛОВОК"])
        self.assertEqual(result[0]["source_end"], 6.0)

    def test_snaps_ad_range_and_ripple_removes_video_and_dialogue(self) -> None:
        manifest = Manifest(
            job=Job(id="demo", source_file="source.mp4"),
            segments=[
                Segment(id=1, start=0.0, end=2.0, ru="Контент"),
                Segment(id=2, start=3.2, end=5.8, ru="Реклама"),
                Segment(id=3, start=6.1, end=9.0, ru="Продолжение"),
            ],
            metadata={
                "ad_removal": {
                    "mode": "remove",
                    "ranges": [{"start": 3.2, "end": 5.8, "confidence": 0.99}],
                }
            },
        )
        shots = [
            {"start_seconds": 0.0, "end_seconds": 3.0},
            {"start_seconds": 3.0, "end_seconds": 6.0},
            {"start_seconds": 6.0, "end_seconds": 10.0},
        ]

        ranges = PremiereExportStage._resolved_ad_ranges(
            manifest, shots, 10.0
        )
        episodes = PremiereExportStage._remove_episode_ranges(
            [Episode(index=1, start=0.0, end=10.0, shot_indices=(1, 2, 3))],
            ranges,
        )
        segments = PremiereExportStage._segments_for_export(
            manifest.segments, ranges
        )

        self.assertEqual([(item["start"], item["end"]) for item in ranges], [(3.0, 6.0)])
        self.assertEqual(
            [(item.start, item.end, item.sequence_start) for item in episodes],
            [(0.0, 3.0, 0.0), (6.0, 10.0, 3.0)],
        )
        self.assertEqual([item.id for item in segments], [1, 3])
        self.assertAlmostEqual(segments[-1].start, 3.1)

    def test_expands_ad_to_prior_brand_mentions_and_snaps_outward(self) -> None:
        manifest = Manifest(
            job=Job(id="demo", source_file="source.mp4"),
            segments=[
                Segment(id=9, start=57.1, end=63.4, ru="Основная тема."),
                Segment(id=10, start=63.5, end=74.1, ru="На помощь приходит GoLogin."),
                Segment(id=11, start=74.2, end=86.7, ru="GoLogin защищает данные."),
                Segment(id=13, start=101.9, end=103.3, ru="Основная тема."),
            ],
            metadata={
                "ad_removal": {
                    "mode": "remove",
                    "ranges": [
                        {"start": 87.0, "end": 100.4, "brand": "GoLogin"}
                    ],
                }
            },
        )
        shots = [
            {"start_seconds": 0.0, "end_seconds": 63.4},
            {"start_seconds": 63.4, "end_seconds": 87.7},
            {"start_seconds": 87.7, "end_seconds": 100.7},
            {"start_seconds": 100.7, "end_seconds": 110.0},
        ]

        ranges = PremiereExportStage._resolved_ad_ranges(manifest, shots, 110.0)

        self.assertEqual(ranges[0]["start"], 63.4)
        self.assertEqual(ranges[0]["end"], 100.7)

    def test_keeps_dialogue_that_ends_before_outward_scene_cut(self) -> None:
        segment = Segment(
            id=9,
            start=57.1,
            end=63.46,
            ru="Последняя фраза перед рекламой.",
            tts_duration=4.5,
            dub_start=57.1,
            dub_duration=4.4,
        )

        exported = PremiereExportStage._segments_for_export(
            [segment], [{"start": 63.4, "end": 100.7}]
        )

        self.assertEqual([item.id for item in exported], [9])
        self.assertAlmostEqual(exported[0].end, 63.4)

    def test_render_preserves_ripple_timeline_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = PremiereExportStage(
                python="python", ffmpeg="ffmpeg", ffprobe="ffprobe"
            )
            episode = Episode(
                index=1,
                start=100.0,
                end=103.0,
                shot_indices=(1,),
                timeline_start=87.0,
            )

            def render_file(
                _ffmpeg: str,
                _source: Path,
                output: Path,
                _episode: Episode,
                _backend: object,
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                output.write_bytes(b"video")
                return subprocess.CompletedProcess([], 0, "", "")

            with patch.object(stage, "_run_episode", side_effect=render_file):
                rendered = stage._render_episodes(
                    "ffmpeg", root / "source.mp4", root, [episode]
                )

        self.assertEqual(rendered[0].sequence_start, 87.0)

    def test_requires_generated_voice_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.mp4").write_bytes(b"video")
            manifest = Manifest(
                job=Job(id="demo", source_file="source.mp4"),
                segments=[Segment(id=1, start=0.0, end=1.0, ru="Да", en="Yes")],
            )
            stage = PremiereExportStage(
                python="python", ffmpeg="ffmpeg", ffprobe="ffprobe"
            )
            with self.assertRaisesRegex(PremiereExportError, "TTS audio"):
                stage.run(manifest, root)


if __name__ == "__main__":
    unittest.main()
