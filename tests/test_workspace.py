from __future__ import annotations

import tempfile
import unittest
import hashlib
from pathlib import Path

from ru_en_dubber.models import Job, Manifest
from ru_en_dubber.workspace import WorkspaceCatalog, slugify


class WorkspaceCatalogTests(unittest.TestCase):
    def test_readable_folders_keep_stable_short_ids(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = WorkspaceCatalog(Path(folder))
            channel = workspace.create_channel("История городов")
            video = workspace.create_video(
                channel["id"], "Почему исчез старый Нью-Йорк?"
            )

            self.assertRegex(channel["id"], r"^c[a-f0-9]{8}$")
            self.assertRegex(video["id"], r"^v[a-f0-9]{8}$")
            self.assertIn("istoriya-gorodov--", channel["folder_name"])
            self.assertIn("pochemu-ischez-staryy-nyu-york--", video["folder_name"])
            self.assertEqual(
                workspace.channel_details(channel["id"])["videos"][0]["working_title"],
                "Почему исчез старый Нью-Йорк?",
            )

    def test_each_channel_owns_its_videos_research_and_playbooks(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = WorkspaceCatalog(Path(folder))
            first = workspace.create_channel("First")
            second = workspace.create_channel("Second")
            video = workspace.create_video(first["id"], "Episode")
            first_root = workspace.channel_directory(first["id"])

            self.assertTrue((first_root / "research" / "competitors").is_dir())
            self.assertTrue((first_root / "playbooks").is_dir())
            self.assertEqual(len(workspace.list_videos(first["id"])["videos"]), 1)
            self.assertEqual(len(workspace.list_videos(second["id"])["videos"]), 0)
            self.assertTrue(
                workspace.video_directory(first["id"], video["id"]).is_relative_to(
                    first_root
                )
            )

    def test_run_history_is_read_from_video_runs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = WorkspaceCatalog(Path(folder))
            channel = workspace.create_channel("History")
            video = workspace.create_video(channel["id"], "City")
            run_id = "run-20260101-120000-abcd"
            run = workspace.runs_directory(channel["id"], video["id"]) / run_id
            run.mkdir()
            Manifest(job=Job(id=run_id)).save(run / "manifest.json")

            details = workspace.video_details(channel["id"], video["id"])

            self.assertEqual(details["run_count"], 1)
            self.assertEqual(details["runs"][0]["id"], run_id)

    def test_script_must_be_saved_and_reviewed_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = WorkspaceCatalog(Path(folder))
            channel = workspace.create_channel("History")
            video = workspace.create_video(channel["id"], "City")
            self.assertEqual(video["script_status"], "planning")
            self.assertFalse(video["has_script"])

            draft = workspace.save_script(
                channel["id"], video["id"], "First draft text.", status="drafting"
            )
            self.assertEqual(draft["script_status"], "drafting")
            with self.assertRaises(ValueError):
                workspace.read_ready_script(channel["id"], video["id"])

            ready = workspace.set_script_status(channel["id"], video["id"], "ready")
            self.assertEqual(ready["script_status"], "ready")
            self.assertEqual(ready["script_word_count"], 3)
            self.assertEqual(
                ready["script_sha256"], hashlib.sha256(b"First draft text.").hexdigest()
            )
            self.assertEqual(
                workspace.read_ready_script(channel["id"], video["id"]),
                b"First draft text.",
            )
            workspace.script_path(channel["id"], video["id"]).write_text(
                "Changed after review.", encoding="utf-8"
            )
            changed = workspace.video_details(channel["id"], video["id"])
            self.assertFalse(changed["script_is_current"])
            with self.assertRaisesRegex(ValueError, "изменился"):
                workspace.read_ready_script(channel["id"], video["id"])

    def test_slugify_is_portable_and_bounded(self) -> None:
        self.assertEqual(slugify("  Москва: 1900 / 2000  ", fallback="video"), "moskva-1900-2000")
        self.assertEqual(slugify("東京", fallback="video"), "video")
        self.assertLessEqual(len(slugify("x" * 100, fallback="video")), 48)


if __name__ == "__main__":
    unittest.main()
