from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.cli import _run_preflight, build_parser, load_local_secrets, main
from ru_en_dubber.pipeline.script import create_job_from_script
from ru_en_dubber.preflight import PreflightError


class LocalSecretsTests(unittest.TestCase):
    def test_installed_package_uses_current_workspace_instead_of_site_packages(self) -> None:
        from ru_en_dubber.cli import project_root

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            installed = root / "environment/Lib/site-packages/ru_en_dubber/cli.py"
            with patch("ru_en_dubber.cli.__file__", str(installed)), patch(
                "ru_en_dubber.cli.Path.cwd", return_value=root
            ):
                self.assertEqual(project_root(), root)

    def test_english_script_skips_video_ingest_and_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "episode.txt"
            script.write_text("An opening line. A second line about the topic.", encoding="utf-8")
            jobs = Path(directory) / "jobs"
            with patch("ru_en_dubber.cli.load_local_secrets"), patch(
                "ru_en_dubber.cli._run_preflight"
            ), patch("ru_en_dubber.cli.PipelineRunner") as runner:
                main(["--script", str(script), "--jobs-dir", str(jobs),
                      "--job-id", "english-script", "--no-progress"])
            stages = [stage.name for stage in runner.call_args.args[0]]
            self.assertEqual(stages, ["tts", "speech_postprocess", "script_timing", "subtitle_export", "visual_rebuild",
                                      "music_bed", "premiere_export"])
            manifest = (jobs / "english-script" / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"type": "script"', manifest)
            self.assertIn('"video_mode": "rebuild"', manifest)

    def test_script_rejects_video_source_and_disabled_tts(self) -> None:
        with self.assertRaisesRegex(SystemExit, "cannot be combined"):
            main(["input.mp4", "--script", "episode.txt"])
        with self.assertRaisesRegex(SystemExit, "requires TTS"):
            main(["--script", "episode.txt", "--no-tts"])

    def test_audio_only_script_skips_visual_and_premiere_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "episode.txt"
            script.write_text("An English narration.", encoding="utf-8")
            jobs = root / "jobs"
            with patch("ru_en_dubber.cli.load_local_secrets"), patch(
                "ru_en_dubber.cli._run_preflight"
            ) as preflight, patch("ru_en_dubber.cli.PipelineRunner") as runner:
                main(["--script", str(script), "--jobs-dir", str(jobs),
                      "--job-id", "audio-only", "--audio-only", "--no-progress"])
            self.assertEqual(
                [stage.name for stage in runner.call_args.args[0]],
                ["tts", "speech_postprocess", "script_timing", "narration_export", "subtitle_export"],
            )
            self.assertTrue(preflight.call_args.kwargs["audio_only"])
            self.assertEqual(
                runner.return_value.run.call_args.args[0].metadata["output_mode"],
                "audio",
            )

    def test_script_routes_expressive_options_to_tts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "episode.txt"
            script.write_text("An English narration.", encoding="utf-8")
            model = root / "cosyvoice-model"
            directions = root / "VOICE_DIRECTION.json"
            with patch("ru_en_dubber.cli.load_local_secrets"), patch(
                "ru_en_dubber.cli._run_preflight"
            ), patch("ru_en_dubber.cli.PipelineRunner") as runner:
                main([
                    "--script", str(script), "--jobs-dir", str(root / "jobs"),
                    "--job-id", "expressive", "--audio-only", "--no-progress",
                    "--tts-provider", "cosyvoice3", "--tts-model-dir", str(model),
                    "--tts-direction-file", str(directions),
                ])
            tts = runner.call_args.args[0][0]
            self.assertEqual(tts.name, "tts")
            self.assertEqual(tts._provider, "cosyvoice3")
            self.assertEqual(tts._model_dir, model)
            self.assertEqual(tts._direction_file, directions)

    def test_audio_only_preflight_includes_speech_processing_tools(self) -> None:
        args = build_parser().parse_args(["--script", "episode.txt", "--audio-only"])
        with patch("ru_en_dubber.cli.run_preflight") as check:
            _run_preflight(args, "rebuild", script_mode=True, audio_only=True)
        requirements = check.call_args.args[0]
        self.assertEqual(
            [label for label, _ in requirements.executables],
            ["FFmpeg", "FFprobe", "Chatterbox Python"],
        )
        self.assertEqual(
            [label for label, _ in requirements.files], ["TTS voice reference"]
        )

    def test_explicit_speech_bypass_does_not_require_processing_binaries_for_plain_audio(self) -> None:
        args = build_parser().parse_args([
            "--script", "episode.txt", "--audio-only", "--speech-postprocess", "off",
        ])
        with patch("ru_en_dubber.cli.run_preflight") as check:
            _run_preflight(args, "rebuild", script_mode=True, audio_only=True)
        self.assertEqual([label for label, _ in check.call_args.args[0].executables],
                         ["Chatterbox Python"])

    def test_timed_audio_preflight_also_needs_ffmpeg(self) -> None:
        args = build_parser().parse_args(["--script", "beats.srt", "--audio-only"])
        with patch("ru_en_dubber.cli.run_preflight") as check:
            _run_preflight(
                args, "rebuild", script_mode=True, audio_only=True, timed_script=True
            )
        self.assertEqual(
            [label for label, _ in check.call_args.args[0].executables],
            ["FFmpeg", "FFprobe", "Chatterbox Python"],
        )

    def test_reexport_keeps_saved_visual_media_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "episode.txt"
            script.write_text("A short English script.", encoding="utf-8")
            jobs = root / "jobs"
            manifest, job_dir = create_job_from_script(script, jobs, "saved-mode")
            manifest.metadata["visual_media_type"] = "image"
            manifest.save(job_dir / "manifest.json")
            with patch("ru_en_dubber.cli.load_local_secrets"), patch(
                "ru_en_dubber.cli._run_preflight"
            ), patch("ru_en_dubber.cli.PipelineRunner") as runner:
                main(["--premiere-export-job", "saved-mode", "--jobs-dir", str(jobs),
                      "--no-progress"])
            rebuild = next(stage for stage in runner.call_args.args[0]
                           if stage.name == "visual_rebuild")
            self.assertEqual(rebuild._media_type_mode, "image")

    def test_preflight_failure_is_recorded_before_pipeline_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"video")
            with patch("ru_en_dubber.cli.load_local_secrets"):
                with patch(
                    "ru_en_dubber.cli.run_preflight",
                    side_effect=PreflightError("FFmpeg is missing"),
                ):
                    with patch(
                        "ru_en_dubber.cli.PipelineRunner",
                        side_effect=AssertionError("pipeline must not start"),
                    ):
                        with self.assertRaisesRegex(SystemExit, "Preflight failed"):
                            main(
                                [
                                    str(source),
                                    "--job-id",
                                    "preflight-failure",
                                    "--jobs-dir",
                                    str(Path(directory) / "jobs"),
                                ]
                            )
            manifest = Path(directory) / "jobs" / "preflight-failure" / "manifest.json"
            self.assertTrue(manifest.is_file())
            self.assertIn('"status": "failed"', manifest.read_text(encoding="utf-8"))

    def test_existing_job_id_cannot_escape_jobs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            jobs.mkdir()
            with patch("ru_en_dubber.cli.load_local_secrets"):
                with self.assertRaisesRegex(SystemExit, "job id"):
                    main(
                        [
                            "--resume-job",
                            "../outside",
                            "--jobs-dir",
                            str(jobs),
                        ]
                    )

    def test_local_avatar_requires_motion_video(self) -> None:
        with self.assertRaisesRegex(SystemExit, "--avatar-source is required"):
            from ru_en_dubber.cli import main
            main(["--avatar-mode", "musetalk"])
        args = build_parser().parse_args([
            "--avatar-mode", "musetalk", "--avatar-source", "presenter.mp4",
            "--avatar-size", "320", "--avatar-position", "top-left",
        ])
        self.assertEqual(args.avatar_mode, "musetalk")
        self.assertEqual(args.avatar_size, 320)
        self.assertEqual(args.avatar_position, "top-left")

    def test_optional_provider_keys_and_explicit_legacy_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('PIXABAY_API_KEY="pix-test"\nNARA_API_KEY=nara-test\nUNRELATED=skip\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                load_local_secrets(path)
                self.assertEqual(os.environ["PIXABAY_API_KEY"], "pix-test")
                self.assertEqual(os.environ["NARA_API_KEY"], "nara-test")
                self.assertNotIn("PEXELS_API_KEY", os.environ)
                self.assertNotIn("UNRELATED", os.environ)
        args = build_parser().parse_args(["--video-mode", "rebuild", "--visual-planner", "legacy",
                                          "--media-providers", "pexels", "pixabay"])
        self.assertEqual(args.media_providers, ["pexels", "pixabay"])
        self.assertEqual(args.visual_planner, "legacy")

    def test_music_defaults_to_local_first_hybrid_mode(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.music_mode, "hybrid")
        self.assertEqual(args.music_min_score, 0.72)
        self.assertEqual(args.music_library.name, "music")

    def test_loads_only_supported_key_without_overwriting_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "PEXELS_API_KEY=file-key\nUNRELATED=value\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"PEXELS_API_KEY": "environment-key"}, clear=True):
                load_local_secrets(env_file)
                self.assertEqual(os.environ["PEXELS_API_KEY"], "environment-key")
                self.assertNotIn("UNRELATED", os.environ)


if __name__ == "__main__":
    unittest.main()
