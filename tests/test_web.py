from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console
from ru_en_dubber.content.analysis import ContentAnalysisStage
from ru_en_dubber.models import Job, Manifest
from ru_en_dubber.pipeline.fake import FakeTranscriptionStage, FakeTranslationStage
from ru_en_dubber.pipeline.progress import ConsolePipelineProgress
from ru_en_dubber.pipeline.runner import PipelineRunner
from ru_en_dubber.web.catalog import details, media_path
from ru_en_dubber.web.progress import FileProgress
from ru_en_dubber.web.server import Service, handler
from ru_en_dubber.web.settings import build_argv, schema, valid_id


class ProgressTests(unittest.TestCase):
    def test_windows_read_lock_retries_atomic_progress_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = FileProgress(Path(folder) / "progress.json")
            original_replace = Path.replace
            sharing_error = PermissionError("snapshot is open")
            sharing_error.winerror = 5

            def replace_with_transient_lock(temporary, target):
                if replace.call_count == 1:
                    raise sharing_error
                self.assertEqual(json.loads(target.read_text("utf-8"))["status"], "starting")
                original_replace(temporary, target)

            with (patch.object(Path, "replace", autospec=True,
                               side_effect=replace_with_transient_lock) as replace,
                  patch("ru_en_dubber.web.progress.time.sleep") as sleep):
                progress.start_pipeline("test", 2)

            sleep.assert_called_once_with(0.02)
            self.assertEqual(json.loads(progress.path.read_text("utf-8"))["status"], "running")

    def test_real_pipeline_snapshots_finish_only_after_all_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source.mp4").write_bytes(b"video")
            progress = FileProgress(root / "progress.json")
            runner = PipelineRunner([FakeTranscriptionStage(), FakeTranslationStage()], progress=progress)
            runner.run(Manifest(job=Job(id="test")), root)
            state = json.loads(progress.path.read_text(encoding="utf-8"))
            self.assertEqual(state["overall"], 1)
            self.assertEqual(state["status"], "completed")
            self.assertEqual(len(state["stages"]), 2)

    def test_partial_progress_unknown_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = FileProgress(Path(folder) / "progress.json")
            progress.start_pipeline("test", 2)
            progress.start_stage("content_analysis", 1, 2)
            progress.update_stage(.5, "4/8")
            self.assertEqual(progress.state["overall"], .25)
            progress.update_stage(None, "Загрузка")
            self.assertIsNone(progress.current["fraction"])
            self.assertEqual(progress.state["overall"], .25)
            progress.fail_stage("content_analysis", RuntimeError("failed"))
            progress.finish_pipeline(False)
            self.assertEqual(progress.state["status"], "failed")
            self.assertLess(progress.state["overall"], 1)

    def test_console_completion_does_not_double_count_partial_stage(self):
        progress = ConsolePipelineProgress(console=Console(file=io.StringIO()))
        progress.start_pipeline("test", 2)
        try:
            progress.start_stage("content_analysis", 1, 2)
            progress.update_stage(.5)
            self.assertEqual(progress._progress.tasks[0].completed, .5)
            progress.complete_stage("content_analysis")
            self.assertEqual(progress._progress.tasks[0].completed, 1)
        finally:
            progress.finish_pipeline(True)

    def test_analysis_reports_batches_including_fallback_and_partial_last_batch(self):
        stage = ContentAnalysisStage(server="unused", model=Path("unused"), python="unused", source_mode="hybrid")
        runtime = Mock()
        runtime._chat_completion_messages.side_effect = ValueError("invalid model JSON")
        events = []
        stage.set_progress_callback(lambda fraction, message: events.append((fraction, message)))
        shots = [dict(index=i+1, start=float(i), end=float(i+1), frames=[]) for i in range(9)]
        result = stage._analyze_visual_shots(runtime, 1234, shots, Path("."), [], "uncertain")
        self.assertEqual(len(result), 9)
        self.assertEqual(runtime._chat_completion_messages.call_count, 3)
        self.assertEqual(len(events), 6)
        self.assertEqual([f for f, _ in events], sorted(f for f, _ in events))
        self.assertAlmostEqual(events[-1][0], .94)
        self.assertIn("9/9", events[-1][1])
        self.assertIn("need review: 9", events[-1][1])

    def test_invalid_later_shot_does_not_duplicate_earlier_results(self):
        stage = ContentAnalysisStage(server="unused", model=Path("unused"), python="unused", source_mode="hybrid")
        runtime = Mock()
        runtime._parse_json_payload.return_value = {"shots": [{"index": 1}, {"index": 2}]}
        shots = [dict(index=i+1, start=float(i), end=float(i+1), frames=["test.jpg"]) for i in range(2)]
        with patch.object(stage, "_image_part", return_value={}), patch.object(stage, "_validated_visual_shot", side_effect=[shots[0], ValueError("invalid confidence")]):
            result = stage._analyze_visual_shots(runtime, 1234, shots, Path("."), [], "uncertain")
        self.assertEqual([shot["index"] for shot in result], [1, 2])
        self.assertTrue(all(shot["requires_review"] for shot in result))


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.service = Service(self.root)
        self.channel = self.service.workspace.create_channel("История городов")
        self.video = self.service.workspace.create_video(
            self.channel["id"], "Почему исчез старый Нью-Йорк"
        )
        self.run_id = "run-20260101-120000-abcd"
        self.job = self.service.workspace.runs_directory(
            self.channel["id"], self.video["id"]
        ) / self.run_id
        self.job.mkdir()
        (self.job / "source.mp4").write_bytes(b"0123456789")
        Manifest(job=Job(id=self.run_id)).save(self.job / "manifest.json")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.service))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_srt_is_available_as_downloadable_run_asset(self):
        (self.job / "narration.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello.\n", encoding="utf-8"
        )
        (self.job / "script.srt").write_text("source cues", encoding="utf-8")
        manifest = Manifest.load(self.job / "manifest.json")
        manifest.artifacts["subtitles_srt"] = "narration.srt"
        manifest.save(self.job / "manifest.json")
        listing = details(
            self.service.workspace, self.channel["id"], self.video["id"], self.run_id
        )["media"]
        self.assertEqual(
            next(item for item in listing if item["path"] == "narration.srt")["kind"],
            "subtitle",
        )
        self.assertFalse(any(item["path"] == "script.srt" for item in listing))
        self.assertEqual(
            media_path(
                self.service.workspace, self.channel["id"], self.video["id"],
                self.run_id, "narration.srt",
            ),
            self.job / "narration.srt",
        )

    def test_catalog_and_range_media(self):
        status, _, body = self.request("/api/v1/channels")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["channels"][0]["display_name"], "История городов")
        base = (
            f"/api/v1/channels/{self.channel['id']}/videos/{self.video['id']}"
            f"/runs/{self.run_id}/media/source.mp4"
        )
        status, headers, body = self.request(base, headers={"Range": "bytes=2-5"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"2345")
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(self.request(base, headers={"Range": "bytes=15-20"})[0], 416)
        self.assertEqual(self.request(base, headers={"Range": "bytes=-3"})[2], b"789")

    def test_channel_can_be_created_and_opened_by_api(self):
        headers = {
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
            "X-CSRF-Token": self.service.token,
            "Content-Type": "application/json",
        }
        status, _, body = self.request(
            "/api/v1/channels",
            "POST",
            json.dumps({"display_name": "Science Stories"}),
            headers,
        )
        self.assertEqual(status, 201)
        channel = json.loads(body)
        status, _, body = self.request(f"/api/v1/channels/{channel['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["display_name"], "Science Stories")

    def test_host_csrf_and_traversal(self):
        self.assertEqual(self.request("/api/v1/bootstrap", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/v1/runs", "POST", "{}")[0], 403)
        base = (
            f"/api/v1/channels/{self.channel['id']}/videos/{self.video['id']}"
            f"/runs/{self.run_id}/media/"
        )
        self.assertEqual(self.request(base + "../manifest.json")[0], 404)
        self.assertEqual(self.request(base + "%2e%2e/%2e%2e/secret.mp4")[0], 404)

    def test_settings_validate_and_roundtrip_cli(self):
        source = self.root / "input.mp4"
        source.write_bytes(b"video")
        from ru_en_dubber.cli import build_parser
        jobs = self.service.workspace.runs_directory(self.channel["id"], self.video["id"])
        argv = build_argv(dict(source="input.mp4", options={"tts_exaggeration": .7, "media_providers": ["pexels"], "no_tts": True}), self.root, jobs, "new")
        args = build_parser().parse_args(argv)
        self.assertEqual(args.tts_exaggeration, .7)
        self.assertEqual(args.media_providers, ["pexels"])
        self.assertTrue(args.no_tts)
        self.assertGreater(len(schema()), 50)
        for payload in [{"source":"../outside.mp4"}, {"source":"input.mp4", "options":{"unknown":1}}, {"source":"input.mp4", "options":{"tts_exaggeration":float('nan')}}]:
            with self.assertRaises(ValueError):
                build_argv(payload, self.root, jobs, "new")
        for value in ["../escape", "C:\\outside", "", "a/b"]:
            with self.assertRaises(ValueError): valid_id(value)

    def test_script_argv_requires_job_local_text_and_forces_rebuild(self):
        video_root = self.service.workspace.video_directory(
            self.channel["id"], self.video["id"]
        )
        jobs = video_root / "runs"
        path = video_root / "script.md"
        path.write_text("English narration.", encoding="utf-8")
        argv = build_argv({"mode": "script", "options": {
            "video_mode": "rebuild", "visual_media_type": "image"
        }}, self.root, jobs, "story", script_path=path, script_root=video_root)
        from ru_en_dubber.cli import build_parser
        args = build_parser().parse_args(argv)
        self.assertEqual(args.script, path.resolve())
        self.assertEqual(args.visual_media_type, "image")
        audio_argv = build_argv(
            {"mode": "script", "output_mode": "audio"}, self.root, jobs, "audio-story",
            script_path=path, script_root=video_root,
        )
        self.assertTrue(build_parser().parse_args(audio_argv).audio_only)
        timed = video_root / "CAPCUT_BEATS.srt"
        timed.write_text(
            "1\n00:00:00,000 --> 00:00:10,000\nAn English beat.\n", encoding="utf-8"
        )
        capcut_argv = build_argv(
            {"mode": "script", "output_mode": "capcut",
             "options": {"capcut_scene_seconds": 8}}, self.root, jobs,
            "capcut-story", script_path=timed, script_root=video_root,
        )
        self.assertTrue(build_parser().parse_args(capcut_argv).audio_only)
        self.assertEqual(build_parser().parse_args(capcut_argv).capcut_scene_seconds, 8)
        with self.assertRaisesRegex(ValueError, "0 or 2"):
            build_argv(
                {"mode": "script", "output_mode": "capcut",
                 "options": {"capcut_scene_seconds": 1}}, self.root, jobs,
                "too-short", script_path=timed, script_root=video_root,
            )
        with self.assertRaisesRegex(ValueError, "CapCut"):
            build_argv(
                {"mode": "script", "output_mode": "capcut"}, self.root, jobs,
                "wrong-format", script_path=path, script_root=video_root,
            )
        with self.assertRaises(ValueError):
            build_argv({"mode": "script", "output_mode": "invalid"},
                       self.root, jobs, "story", script_path=path, script_root=video_root)
        with self.assertRaises(ValueError):
            build_argv({"mode": "script", "options": {"video_mode": "hybrid"}},
                       self.root, jobs, "story", script_path=path, script_root=video_root)
        outside = self.root / "outside.txt"
        outside.write_text("text", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_argv({"mode": "script"}, self.root, jobs, "story",
                       script_path=outside, script_root=video_root)

    def test_script_import_stores_text_outside_request_json(self):
        process = Mock()
        process.pid = 12345
        process.poll.return_value = None
        with patch("ru_en_dubber.web.server.subprocess.Popen", return_value=process):
            result = self.service.launch({
                "mode": "script_import", "channel_id": self.channel["id"],
                "video_title": "Narrated city history", "script_text": "A private draft.",
                "output_mode": "audio",
                "options": {"visual_media_type": "video"}
            })
        directory = self.service.runs / result["id"]
        script = self.service.workspace.script_path(result["channel_id"], result["video_id"])
        self.assertEqual(script.read_text(encoding="utf-8"), "A private draft.")
        self.assertEqual(
            (directory / "script-snapshot.md").read_text(encoding="utf-8"),
            "A private draft.",
        )
        self.assertNotIn("A private draft", (directory / "request.json").read_text(encoding="utf-8"))
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        self.assertIn("--audio-only", request["argv"])
        for stream in self.service.logs.values(): stream.close()
        self.service.logs.clear()

    def test_prepared_script_launch_reuses_video_and_takes_snapshot(self):
        self.service.workspace.save_script(
            self.channel["id"], self.video["id"], "Reviewed narration.", status="ready"
        )
        process = Mock()
        process.pid = 12345
        process.poll.return_value = None
        before = self.service.workspace.list_videos(self.channel["id"])["videos"]
        with patch("ru_en_dubber.web.server.subprocess.Popen", return_value=process):
            result = self.service.launch({
                "mode": "script",
                "channel_id": self.channel["id"],
                "video_id": self.video["id"],
                "options": {"visual_media_type": "image"},
            })
        after = self.service.workspace.list_videos(self.channel["id"])["videos"]
        operation = self.service.runs / result["id"]
        request = json.loads((operation / "request.json").read_text(encoding="utf-8"))

        self.assertEqual(result["video_id"], self.video["id"])
        self.assertEqual([item["id"] for item in after], [item["id"] for item in before])
        self.assertEqual(
            (operation / "script-snapshot.md").read_text(encoding="utf-8"),
            "Reviewed narration.",
        )
        self.assertIn("--script", request["argv"])
        for stream in self.service.logs.values():
            stream.close()
        self.service.logs.clear()

    def test_prepared_capcut_launch_snapshots_timed_beat_sheet(self):
        self.service.workspace.save_script(
            self.channel["id"], self.video["id"], "Reviewed narration.", status="ready"
        )
        video_root = self.service.workspace.video_directory(
            self.channel["id"], self.video["id"]
        )
        beats = "1\n00:00:00,000 --> 00:00:10,000\nA timed beat.\n"
        (video_root / "CAPCUT_BEATS.srt").write_text(beats, encoding="utf-8")
        direction = {"version": 1, "segments": [
            {"id": 1, "text": "A timed beat.", "style": "curious", "intensity": 0.5}
        ]}
        (video_root / "CAPCUT_VOICE_DIRECTION.json").write_text(
            json.dumps(direction), encoding="utf-8"
        )
        process = Mock()
        process.pid = 12345
        process.poll.return_value = None
        with patch("ru_en_dubber.web.server.subprocess.Popen", return_value=process):
            result = self.service.launch({
                "mode": "script", "channel_id": self.channel["id"],
                "video_id": self.video["id"], "output_mode": "capcut",
                "options": {"capcut_scene_seconds": 8, "tts_provider": "cosyvoice3"},
            })
        operation = self.service.runs / result["id"]
        self.assertEqual(
            (operation / "script-snapshot.srt").read_text(encoding="utf-8"), beats
        )
        request = json.loads((operation / "request.json").read_text(encoding="utf-8"))
        self.assertIn("--audio-only", request["argv"])
        self.assertIn("--tts-direction-file", request["argv"])
        self.assertEqual(json.loads((operation / "direction-snapshot.json").read_text()), direction)
        for stream in self.service.logs.values():
            stream.close()
        self.service.logs.clear()

    def test_prepared_draft_cannot_be_launched(self):
        self.service.workspace.save_script(
            self.channel["id"], self.video["id"], "Unreviewed narration.",
            status="drafting",
        )

        with self.assertRaisesRegex(ValueError, "final review"):
            self.service.launch({
                "mode": "script",
                "channel_id": self.channel["id"],
                "video_id": self.video["id"],
            })

    def test_start_uses_argument_list_and_single_worker(self):
        (self.root / "input.mp4").write_bytes(b"video")
        process = Mock()
        process.pid = 12345
        process.poll.return_value = None
        with patch("ru_en_dubber.web.server.subprocess.Popen", return_value=process) as launch:
            result = self.service.launch({
                "channel_id": self.channel["id"], "video_title": "New test video",
                "source":"input.mp4"
            })
            self.assertIsInstance(launch.call_args.args[0], list)
            with self.assertRaises(ValueError): self.service.launch({
                "channel_id": self.channel["id"], "video_title": "Second video",
                "source":"input.mp4"
            })
        for stream in self.service.logs.values(): stream.close()
        self.service.logs.clear()
        self.assertTrue((self.service.runs / result["id"] / "request.json").exists())

    def test_invalid_new_launch_does_not_leave_empty_video(self):
        before = self.service.workspace.list_videos(self.channel["id"])["videos"]

        with self.assertRaises(ValueError):
            self.service.launch({
                "channel_id": self.channel["id"],
                "video_title": "Invalid source",
                "source": "missing-input.mp4",
            })

        after = self.service.workspace.list_videos(self.channel["id"])["videos"]
        self.assertEqual([item["id"] for item in after], [item["id"] for item in before])

    def test_restarted_server_detects_dead_worker(self):
        directory = self.service.runs / "old"
        directory.mkdir()
        record = dict(channel_id=self.channel["id"], channel_name="История городов",
                      video_id=self.video["id"], video_title="Нью-Йорк", run_id=self.run_id,
                      mode="new", created_at=1)
        (directory / "request.json").write_text(json.dumps(record))
        (directory / "progress.json").write_text(json.dumps(dict(status="running", overall=.3)))
        (directory / "worker.pid").write_text("12345")
        with patch("ru_en_dubber.web.server.process_alive", return_value=False):
            result = self.service.snapshots()
        self.assertEqual(result[0]["progress"]["status"], "failed")

    def test_snapshots_releases_completed_worker_handles(self) -> None:
        directory = self.service.runs / "finished"
        directory.mkdir()
        (directory / "request.json").write_text(
            json.dumps(dict(channel_id=self.channel["id"], channel_name="История городов",
                            video_id=self.video["id"], video_title="Нью-Йорк",
                            run_id=self.run_id, mode="new", created_at=1)),
            encoding="utf-8",
        )
        (directory / "progress.json").write_text(
            json.dumps(dict(status="completed", overall=1)),
            encoding="utf-8",
        )
        process = Mock(returncode=0)
        process.poll.return_value = 0
        stream = Mock()
        self.service.processes["finished"] = process
        self.service.logs["finished"] = stream

        self.service.snapshots()

        stream.close.assert_called_once_with()
        self.assertNotIn("finished", self.service.processes)
        self.assertNotIn("finished", self.service.logs)

    def test_service_close_terminates_and_reaps_worker(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("worker", 10), 0]
        stream = Mock()
        self.service.processes["run"] = process
        self.service.logs["run"] = stream

        self.service.close()

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        stream.close.assert_called_once_with()
        self.assertEqual(self.service.processes, {})
        self.assertEqual(self.service.logs, {})

    def test_catalog_exposes_editor_capabilities_honestly(self):
        result = details(
            self.service.workspace, self.channel["id"], self.video["id"], self.run_id
        )
        self.assertFalse(result["capabilities"]["agent_edit"])
        self.assertEqual(result["media"][0]["name"], "source.mp4")

    def test_http_launch_worker_and_failure_are_visible_without_gpu(self):
        (self.root / "input.mp4").write_bytes(b"test input")
        headers = {"Origin": f"http://127.0.0.1:{self.server.server_port}",
                   "X-CSRF-Token": self.service.token, "Content-Type": "application/json"}
        status, _, body = self.request("/api/v1/runs", "POST", json.dumps({
            "channel_id": self.channel["id"], "video_title": "Broken run",
            "source": "input.mp4", "options": {"ffmpeg": "missing-web-test-ffmpeg-xyz"}
        }), headers)
        self.assertEqual(status, 201)
        run = json.loads(body)
        process = self.service.processes[run["id"]]
        self.assertNotEqual(process.wait(timeout=20), 0)
        result = self.service.snapshots()[0]
        self.assertEqual(result["progress"]["status"], "failed")
        run_path = self.service.workspace.runs_directory(
            run["channel_id"], run["video_id"]
        ) / run["run_id"] / "manifest.json"
        self.assertEqual(Manifest.load(run_path).job.status.value, "failed")
        self.assertIn("error", result["progress"])


if __name__ == "__main__":
    unittest.main()
