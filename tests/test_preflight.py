from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.preflight import PreflightError, PreflightRequirements, run_preflight


class PreflightTests(unittest.TestCase):
    def test_reports_all_missing_runtime_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements = PreflightRequirements(
                executables=(("FFmpeg", "missing-ffmpeg"),),
                files=(("translation model", root / "missing.gguf"),),
                work_directory=root / "jobs",
                minimum_free_bytes=0,
            )

            with patch("ru_en_dubber.preflight.shutil.which", return_value=None):
                with self.assertRaises(PreflightError) as raised:
                    run_preflight(requirements)

            message = str(raised.exception)
            self.assertIn("FFmpeg", message)
            self.assertIn("translation model", message)

    def test_accepts_explicit_executable_file_and_cleans_write_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tool.exe"
            model = root / "model.gguf"
            executable.write_bytes(b"tool")
            model.write_bytes(b"model")
            jobs = root / "jobs"

            run_preflight(
                PreflightRequirements(
                    executables=(("tool", str(executable)),),
                    files=(("model", model),),
                    work_directory=jobs,
                    minimum_free_bytes=0,
                )
            )

            self.assertTrue(jobs.is_dir())
            self.assertEqual(list(jobs.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
