from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_public_release.py"
spec = importlib.util.spec_from_file_location("public_audit", SCRIPT)
assert spec and spec.loader
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PublicReleaseAuditTests(unittest.TestCase):
    def test_private_paths_and_safe_config_template(self) -> None:
        for name in ["channels/example/video.json", "voices/reference.wav", ".env",
                     ".env.production", ".agents/skills/example.md", "backup.bundle",
                     "tmp-notes/README.md", "source/music/track.mp3"]:
            with self.subTest(name=name):
                self.assertIsNotNone(audit.path_problem(name))
        for name in ["src/ru_en_dubber/cli.py", "config/.env.example", "README.md"]:
            self.assertIsNone(audit.path_problem(name))

    def test_detects_private_content_without_returning_it(self) -> None:
        secret = b"hf_" + b"A" * 30
        self.assertEqual(audit.content_problems(secret), ["provider token"])
        local_path = b"C:" + b"\\Users\\" + b"Example\\file.txt"
        self.assertEqual(audit.content_problems(local_path), ["machine user path"])
        self.assertEqual(audit.content_problems(b"private title", (b"private title",)),
                         ["local private literal"])
        self.assertEqual(audit.content_problems(b"synthetic public narration"), [])

    def test_history_finds_deleted_content_and_nonbranch_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def git(*args: str) -> str:
                return subprocess.check_output(["git", "-C", str(root), *args], text=True)
            git("init", "--quiet")
            git("config", "user.name", "Example Contributor")
            git("config", "user.email", "contributor@example.invalid")
            (root / "private.txt").write_text("sensitive literal", encoding="utf-8")
            git("add", ".")
            git("commit", "--quiet", "-m", "First")
            old = git("rev-parse", "HEAD").strip()
            git("update-ref", "refs/local/old", old)
            (root / "private.txt").unlink()
            (root / "README.md").write_text("Public", encoding="utf-8")
            git("add", "-A")
            tree = git("write-tree").strip()
            clean = git("commit-tree", tree, "-m", "Clean root").strip()
            git("update-ref", "HEAD", clean)
            count, findings = audit.audit(root, history=False, private_values=(b"sensitive literal",))
            self.assertGreater(count, 0)
            self.assertEqual(findings, [])
            _, findings = audit.audit(root, history=True, private_values=(b"sensitive literal",))
            self.assertTrue(any("local private literal" in finding for finding in findings))
            self.assertFalse(any("sensitive literal" in finding for finding in findings))


if __name__ == "__main__":
    unittest.main()
