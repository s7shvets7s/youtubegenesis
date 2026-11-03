from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ru_en_dubber.avatar import MuseTalkAvatarError, MuseTalkAvatarStage
from ru_en_dubber.models import Job, Manifest, Segment


class MuseTalkAvatarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.ffmpeg = root / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
        cls.ffprobe = root / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe"

    def test_requires_complete_local_musetalk_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mp4"
            video.write_bytes(b"video")
            avatar = root / "avatar.mp4"
            avatar.write_bytes(b"avatar")
            # This checks model validation, not a locally installed media runtime.
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            ffmpeg.write_bytes(b"synthetic executable")
            ffprobe.write_bytes(b"synthetic executable")
            manifest = Manifest(job=Job(id="job", source_file="source.mp4"))
            stage = MuseTalkAvatarStage(
                python=str(Path(__file__)), musetalk_root=root / "missing",
                avatar_source=avatar, ffmpeg=str(ffmpeg), ffprobe=str(ffprobe),
            )
            with self.assertRaisesRegex(MuseTalkAvatarError, "MuseTalk 1.5 is incomplete"):
                stage.run(manifest, root)

    def test_musetalk_timeout_is_duration_aware_and_actionable(self) -> None:
        runner = Mock(
            side_effect=subprocess.TimeoutExpired(["python", "inference"], 3600)
        )
        stage = MuseTalkAvatarStage(
            python=sys.executable,
            musetalk_root=Path("MuseTalk"),
            avatar_source=Path("avatar.mp4"),
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            inference_timeout=3600,
            run_command=runner,
        )
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "musetalk.log"
            with log_path.open("w", encoding="utf-8") as log:
                with self.assertRaisesRegex(
                    MuseTalkAvatarError, "timed out.*musetalk.log"
                ):
                    stage._run_inference(
                        ["python", "inference"],
                        cwd=Path(directory),
                        environment={},
                        log=log,
                        log_path=log_path,
                        duration=400.0,
                    )
        self.assertEqual(runner.call_args.kwargs["timeout"], 8000.0)

    def test_generates_fp16_avatar_composite_and_reuses_cache(self) -> None:
        if not self.ffmpeg.is_file() or not self.ffprobe.is_file():
            self.skipTest("bundled FFmpeg is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            base = job / "base.mp4"
            avatar = job / "motion-source.mp4"
            speech = job / "line.wav"
            self._video(base, "#253047")
            self._video(avatar, "#bd6b45")
            subprocess.run(
                [str(self.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=0.8",
                 "-c:a", "pcm_s16le", str(speech)], check=True, timeout=30,
            )
            musetalk = job / "MuseTalk"
            for relative in (
                "scripts/inference.py", "models/musetalkV15/unet.pth",
                "models/musetalkV15/musetalk.json", "models/whisper/config.json",
                "models/whisper/pytorch_model.bin",
                "models/whisper/preprocessor_config.json",
                "models/sd-vae/config.json",
                "models/sd-vae/diffusion_pytorch_model.bin",
                "models/dwpose/dw-ll_ucoco_384.pth",
                "models/face-parse-bisent/79999_iter.pth",
                "models/face-parse-bisent/resnet18-5c106cde.pth",
                "musetalk/utils/face_detection/detection/sfd/s3fd.pth",
            ):
                path = musetalk / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            calls: list[list[str]] = []
            environments: list[dict[str, str]] = []

            def fake_run(command, **kwargs):
                calls.append(command)
                environments.append(kwargs["env"])
                # Exercise real redirected Python output, including the exact
                # character that crashed MuseTalk on Windows.
                subprocess.run(
                    [sys.executable, "-c", "print('\\u300c\\u0442\\u0435\\u0441\\u0442\\u300d')"],
                    env=kwargs["env"], stdout=kwargs["stdout"],
                    stderr=kwargs["stderr"], check=True,
                )
                result_dir = Path(command[command.index("--result_dir") + 1]) / "v15"
                result_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(job / "avatar" / "avatar-input.mp4", result_dir / "synced-avatar.mp4")
                return subprocess.CompletedProcess(command, 0)

            manifest = Manifest(
                job=Job(id="job", source_file="base.mp4"),
                segments=[Segment(
                    id=1, start=0.1, end=0.9, ru="тест", en="test",
                    tts_file="line.wav", tts_duration=0.8, dub_file="line.wav",
                    dub_start=0.1, dub_duration=0.8, dub_speed=1.0,
                )],
                artifacts={"source_video": "base.mp4"},
            )
            stage = MuseTalkAvatarStage(
                python=str(Path(__file__).resolve()), musetalk_root=musetalk,
                avatar_source=avatar, ffmpeg=str(self.ffmpeg), ffprobe=str(self.ffprobe),
                size=160, margin=10, run_command=fake_run,
            )
            result = stage.run(manifest, job)
            self.assertEqual(len(calls), 1)
            self.assertIn("--use_float16", calls[0])
            self.assertEqual(environments[0]["PYTHONIOENCODING"], "utf-8")
            self.assertEqual(environments[0]["PYTHONUTF8"], "1")
            self.assertIn("「тест」", (job / "avatar/musetalk.log").read_text(encoding="utf-8"))
            self.assertEqual(
                environments[0]["TORCH_HOME"], str((musetalk / ".cache" / "torch").resolve())
            )
            self.assertEqual(
                environments[0]["HF_HOME"], str((musetalk / ".cache" / "huggingface").resolve())
            )
            self.assertTrue((job / result.artifacts["avatar_composite"]).is_file())
            self.assertTrue((job / result.artifacts["avatar_overlay_video"]).is_file())
            self.assertEqual(result.artifacts["source_video"], "base.mp4")
            self.assertEqual(result.metadata["avatar"]["precision"], "fp16")

            stage.run(result, job)
            self.assertEqual(len(calls), 1)

            def failed_run(command, **kwargs):
                kwargs["stdout"].write("Error occurred during processing: simulated failure\n")
                return subprocess.CompletedProcess(command, 0)

            (job / "avatar/avatar.json").unlink()
            (job / "avatar/musetalk-results/v15/synced-avatar.mp4").unlink()
            stage._run_command = failed_run
            with self.assertRaisesRegex(MuseTalkAvatarError, "simulated failure"):
                stage.run(result, job)

    def _video(self, path: Path, color: str) -> None:
        subprocess.run(
            [str(self.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d=1.2",
             "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            check=True, timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
