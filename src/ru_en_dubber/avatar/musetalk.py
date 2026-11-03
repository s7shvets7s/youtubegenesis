from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_path


AVATAR_VERSION = "musetalk-v15-fp16-circle-layer-v2"


class MuseTalkAvatarError(RuntimeError):
    """Raised when the local avatar cannot be generated safely."""


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


class MuseTalkAvatarStage(PipelineStage):
    """Lip-sync a reusable motion plate and composite it as a circular presenter."""

    def __init__(
        self,
        *,
        python: str,
        musetalk_root: Path,
        avatar_source: Path,
        ffmpeg: str,
        ffprobe: str,
        size: int = 360,
        position: str = "bottom-right",
        margin: int = 48,
        gpu_id: int = 0,
        batch_size: int = 2,
        inference_timeout: float = 3600.0,
        ffmpeg_timeout: float = 7200.0,
        probe_timeout: float = 60.0,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if not 160 <= size <= 720:
            raise ValueError("avatar size must be between 160 and 720 pixels")
        if position not in {"top-left", "top-right", "bottom-left", "bottom-right"}:
            raise ValueError("invalid avatar position")
        if not 0 <= margin <= 300:
            raise ValueError("avatar margin must be between 0 and 300 pixels")
        if not 0 <= gpu_id <= 31:
            raise ValueError("avatar GPU id must be between 0 and 31")
        if not 1 <= batch_size <= 8:
            raise ValueError("avatar batch size must be between 1 and 8")
        if any(
            not math.isfinite(value) or value <= 0
            for value in (inference_timeout, ffmpeg_timeout, probe_timeout)
        ):
            raise ValueError("avatar process timeouts must be finite and positive")
        self._python = python
        self._root = Path(musetalk_root)
        self._avatar_source = Path(avatar_source)
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._size = size
        self._position = position
        self._margin = margin
        self._gpu_id = gpu_id
        self._batch_size = batch_size
        self._inference_timeout = float(inference_timeout)
        self._ffmpeg_timeout = float(ffmpeg_timeout)
        self._probe_timeout = float(probe_timeout)
        self._run_command = run_command

    @property
    def name(self) -> str:
        return "avatar_overlay"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        job_dir = job_dir.resolve()
        source_relative = manifest.artifacts.get("source_video") or manifest.job.source_file
        if (
            source_relative == manifest.artifacts.get("avatar_composite")
            and manifest.artifacts.get("avatar_base_video")
        ):
            source_relative = manifest.artifacts["avatar_base_video"]
        base_video = resolve_job_path(job_dir, source_relative, label="avatar base video")
        if not base_video.is_file():
            raise MuseTalkAvatarError(f"avatar base video not found: {base_video}")
        avatar_source = self._avatar_source.expanduser().resolve()
        if not avatar_source.is_file():
            raise MuseTalkAvatarError(f"avatar motion video not found: {avatar_source}")

        python = self._resolve_executable(self._python, "MuseTalk Python")
        ffmpeg = self._resolve_executable(self._ffmpeg, "FFmpeg")
        ffprobe = self._resolve_executable(self._ffprobe, "FFprobe")
        root = self._root.expanduser().resolve()
        inference = root / "scripts" / "inference.py"
        model_dir = root / "models"
        required = (
            inference,
            model_dir / "musetalkV15" / "unet.pth",
            model_dir / "musetalkV15" / "musetalk.json",
            model_dir / "whisper" / "config.json",
            model_dir / "whisper" / "pytorch_model.bin",
            model_dir / "whisper" / "preprocessor_config.json",
            model_dir / "sd-vae" / "config.json",
            model_dir / "sd-vae" / "diffusion_pytorch_model.bin",
            model_dir / "dwpose" / "dw-ll_ucoco_384.pth",
            model_dir / "face-parse-bisent" / "79999_iter.pth",
            model_dir / "face-parse-bisent" / "resnet18-5c106cde.pth",
            root / "musetalk" / "utils" / "face_detection" / "detection" / "sfd" / "s3fd.pth",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise MuseTalkAvatarError(
                "MuseTalk 1.5 is incomplete; missing: " + ", ".join(missing)
            )

        media = self._probe(ffprobe, base_video)
        duration = float(media.get("format", {}).get("duration") or 0.0)
        stream = next(
            (item for item in media.get("streams", []) if item.get("codec_type") == "video"),
            None,
        )
        if not math.isfinite(duration) or duration <= 0 or stream is None:
            raise MuseTalkAvatarError("avatar base video has invalid media metadata")
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        if width <= 0 or height <= 0:
            raise MuseTalkAvatarError("avatar base video has invalid dimensions")

        dialogue = []
        for segment in manifest.segments:
            relative = segment.dub_file or segment.tts_file
            start = segment.dub_start if segment.dub_start is not None else segment.start
            item_duration = (
                segment.dub_duration
                if segment.dub_duration is not None
                else segment.tts_duration
            )
            if not relative or not item_duration:
                raise MuseTalkAvatarError(
                    f"avatar requires aligned speech for segment {segment.id}"
                )
            path = resolve_job_path(job_dir, relative, label=f"avatar speech {segment.id}")
            if not path.is_file():
                raise MuseTalkAvatarError(f"avatar speech file not found: {path}")
            dialogue.append((segment.id, path, float(start), float(item_duration)))
        if not dialogue:
            raise MuseTalkAvatarError("avatar requires at least one speech segment")

        identity = _fingerprint(
            [
                AVATAR_VERSION,
                base_video.stat().st_size,
                base_video.stat().st_mtime_ns,
                avatar_source.stat().st_size,
                avatar_source.stat().st_mtime_ns,
                [(sid, start, length, path.stat().st_size, path.stat().st_mtime_ns)
                 for sid, path, start, length in dialogue],
                self._size,
                self._position,
                self._margin,
                self._gpu_id,
                self._batch_size,
            ]
        )
        directory = job_dir / "avatar"
        report_path = directory / "avatar.json"
        composite = directory / "avatar-composite.mp4"
        synced = directory / "synced-avatar.mp4"
        overlay = directory / "avatar-overlay.mov"
        if (report_path.is_file() and composite.is_file() and synced.is_file()
                and overlay.is_file()):
            try:
                saved = json.loads(report_path.read_text(encoding="utf-8"))
                if saved.get("fingerprint") == identity:
                    return self._apply_artifacts(
                        manifest, job_dir, saved, base_video, composite, synced, overlay
                    )
            except (OSError, ValueError, KeyError, TypeError):
                pass

        directory.mkdir(parents=True, exist_ok=True)
        speech = directory / "avatar-speech.wav"
        motion = directory / "avatar-input.mp4"
        config = directory / "musetalk.yaml"
        result_dir = directory / "musetalk-results"
        result_dir.mkdir(parents=True, exist_ok=True)

        self.report_progress(0.03, "Сборка цельной речи для аватара…")
        self._mix_dialogue(ffmpeg, dialogue, duration, speech)
        self.report_progress(0.10, "Подготовка фронтального motion-video…")
        self._prepare_motion(ffmpeg, avatar_source, motion)
        config.write_text(
            "avatar:\n"
            f"  video_path: {json.dumps(str(motion), ensure_ascii=False)}\n"
            f"  audio_path: {json.dumps(str(speech), ensure_ascii=False)}\n"
            '  result_name: "synced-avatar.mp4"\n',
            encoding="utf-8",
        )

        self.report_progress(0.15, "MuseTalk 1.5 FP16: синхронизация губ…")
        command = [
            python,
            "-m",
            "scripts.inference",
            "--inference_config",
            str(config),
            "--result_dir",
            str(result_dir),
            "--unet_model_path",
            str(model_dir / "musetalkV15" / "unet.pth"),
            "--unet_config",
            str(model_dir / "musetalkV15" / "musetalk.json"),
            "--whisper_dir",
            str(model_dir / "whisper"),
            "--version",
            "v15",
            "--fps",
            "25",
            "--gpu_id",
            str(self._gpu_id),
            "--batch_size",
            str(self._batch_size),
            "--use_float16",
            "--saved_coord",
            "--ffmpeg_path",
            str(Path(ffmpeg).resolve().parent),
        ]
        coordinate_cache = directory / "avatar-input.pkl"
        if coordinate_cache.is_file():
            command.append("--use_saved_coord")
        log_path = directory / "musetalk.log"
        cache_root = root / ".cache"
        torch_home = cache_root / "torch"
        huggingface_home = cache_root / "huggingface"
        torch_home.mkdir(parents=True, exist_ok=True)
        huggingface_home.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        # Redirected stdout uses the Windows locale unless the child explicitly
        # selects UTF-8; the parent's log file encoding does not configure it.
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        environment["TORCH_HOME"] = str(torch_home)
        environment["HF_HOME"] = str(huggingface_home)
        with log_path.open("w", encoding="utf-8") as log:
            result = self._run_inference(
                command,
                cwd=root,
                environment=environment,
                log=log,
                log_path=log_path,
                duration=duration,
            )
        produced = result_dir / "v15" / "synced-avatar.mp4"
        if result.returncode != 0 or not produced.is_file() or produced.stat().st_size == 0:
            with log_path.open("rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 4000))
                details = log.read().decode("utf-8", errors="replace").strip()
            raise MuseTalkAvatarError(
                f"MuseTalk did not produce a video (exit code {result.returncode}); "
                f"inspect {log_path}\n{details}"
            )
        shutil.copy2(produced, synced)
        generated_duration = float(self._probe(ffprobe, synced).get("format", {}).get("duration") or 0)
        if generated_duration < duration - 0.75:
            raise MuseTalkAvatarError(
                f"MuseTalk output is too short ({generated_duration:.2f}s for {duration:.2f}s)"
            )

        self.report_progress(0.88, "Подготовка отдельного слоя аватара…")
        self._render_overlay(ffmpeg, synced, overlay, duration)
        self.report_progress(0.93, "Наложение круглого аватара на превью…")
        self._composite(ffmpeg, base_video, synced, composite, width, height, duration)
        report = {
            "version": 1,
            "fingerprint": identity,
            "provider": "musetalk",
            "model": "1.5",
            "precision": "fp16",
            "avatar_source": str(avatar_source),
            "base_video": base_video.relative_to(job_dir).as_posix(),
            "synced_video": synced.relative_to(job_dir).as_posix(),
            "overlay_video": overlay.relative_to(job_dir).as_posix(),
            "composite_video": composite.relative_to(job_dir).as_posix(),
            "position": self._position,
            "size": self._size,
            "margin": self._margin,
            "duration": duration,
            "speech_segment_count": len(dialogue),
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.report_progress(0.99, "Локальный AI-аватар готов")
        return self._apply_artifacts(
            manifest, job_dir, report, base_video, composite, synced, overlay
        )

    def _run_inference(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        log: object,
        log_path: Path,
        duration: float,
    ) -> subprocess.CompletedProcess[str]:
        timeout = max(self._inference_timeout, duration * 20.0)
        try:
            return self._run_command(
                command,
                cwd=cwd,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise MuseTalkAvatarError(
                f"MuseTalk timed out after {timeout:.0f}s; inspect {log_path}. "
                "Increase inference_timeout for unusually long videos."
            ) from error

    def _apply_artifacts(
        self,
        manifest: Manifest,
        job_dir: Path,
        report: dict,
        base_video: Path,
        composite: Path,
        synced: Path,
        overlay: Path,
    ) -> Manifest:
        manifest.artifacts["avatar_base_video"] = base_video.relative_to(job_dir).as_posix()
        manifest.artifacts["avatar_video"] = synced.relative_to(job_dir).as_posix()
        manifest.artifacts["avatar_overlay_video"] = overlay.relative_to(job_dir).as_posix()
        manifest.artifacts["avatar_composite"] = composite.relative_to(job_dir).as_posix()
        # Keep the source picture independent. Premiere imports the transparent
        # avatar on its own video track; avatar_composite remains a preview/final.
        manifest.artifacts["source_video"] = base_video.relative_to(job_dir).as_posix()
        manifest.metadata["avatar"] = report
        for stage in ("music_bed", "premiere_export"):
            if stage in manifest.completed_stages:
                manifest.completed_stages.remove(stage)
        for artifact in ("music_report", "music_bed", "music_plan", "music_candidates", "music_credits", "premiere_export", "premiere_xml"):
            manifest.artifacts.pop(artifact, None)
        manifest.metadata.pop("music_bed", None)
        manifest.metadata.pop("premiere_export", None)
        return manifest

    @staticmethod
    def _resolve_executable(value: str, label: str) -> str:
        path = Path(value).expanduser()
        if path.is_file():
            return str(path.resolve())
        found = shutil.which(value)
        if found:
            return found
        raise MuseTalkAvatarError(f"{label} executable not found: {value}")

    def _mix_dialogue(
        self,
        ffmpeg: str,
        dialogue: list[tuple[int, Path, float, float]],
        duration: float,
        output: Path,
    ) -> None:
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                   "-t", f"{duration:.6f}", "-i", "anullsrc=r=16000:cl=mono"]
        filters = []
        labels = ["[0:a]"]
        for index, (_, path, start, length) in enumerate(dialogue, start=1):
            command.extend(["-i", str(path)])
            fade_out = max(0.0, length - 0.03)
            delay = max(0, round(start * 1000))
            filters.append(
                f"[{index}:a]aresample=16000,aformat=sample_fmts=fltp:channel_layouts=mono,"
                f"atrim=0:{length:.6f},afade=t=in:st=0:d=0.03,"
                f"afade=t=out:st={fade_out:.6f}:d=0.03,adelay={delay}[a{index}]"
            )
            labels.append(f"[a{index}]")
        filters.append(
            "".join(labels)
            + f"amix=inputs={len(labels)}:duration=first:normalize=0,atrim=0:{duration:.6f}[mix]"
        )
        command.extend(["-filter_complex", ";".join(filters), "-map", "[mix]", "-c:a",
                        "pcm_s16le", "-ar", "16000", "-ac", "1", str(output)])
        self._checked(
            command,
            "failed to build avatar speech track",
            timeout=max(self._ffmpeg_timeout, duration * 12.0),
        )

    def _prepare_motion(self, ffmpeg: str, source: Path, output: Path) -> None:
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-an", "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset",
            "veryfast", "-crf", "18", str(output),
        ]
        self._checked(
            command,
            "failed to normalize avatar motion video",
            timeout=self._ffmpeg_timeout,
        )

    def _render_overlay(
        self, ffmpeg: str, avatar: Path, output: Path, duration: float
    ) -> None:
        radius = self._size / 2.0
        mask = (
            "crop='min(iw,ih)':'min(iw,ih)',"
            f"scale={self._size}:{self._size},format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(lte((X-{radius})*(X-{radius})+(Y-{radius})*(Y-{radius}),"
            f"{radius}*{radius}),255,0)'"
        )
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(avatar), "-an", "-vf", mask, "-t", f"{duration:.6f}",
            "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
            str(output),
        ]
        self._checked(
            command,
            "failed to render transparent avatar layer",
            timeout=max(self._ffmpeg_timeout, duration * 12.0),
        )

    def _composite(
        self,
        ffmpeg: str,
        base: Path,
        avatar: Path,
        output: Path,
        width: int,
        height: int,
        duration: float,
    ) -> None:
        size = min(self._size, width - 2 * self._margin, height - 2 * self._margin)
        if size < 80:
            raise MuseTalkAvatarError("avatar does not fit inside the output frame")
        x = str(self._margin) if self._position.endswith("left") else f"W-w-{self._margin}"
        y = str(self._margin) if self._position.startswith("top") else f"H-h-{self._margin}"
        radius = size / 2.0
        mask = (
            f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={size}:{size},format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(lte((X-{radius})*(X-{radius})+(Y-{radius})*(Y-{radius}),"
            f"{radius}*{radius}),255,0)'[avatar];"
            f"[0:v][avatar]overlay=x={x}:y={y}:shortest=1:format=auto[v]"
        )
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(base),
            "-i", str(avatar), "-filter_complex", mask, "-map", "[v]", "-map", "0:a?",
            "-t", f"{duration:.6f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(output),
        ]
        self._checked(
            command,
            "failed to composite circular avatar",
            timeout=max(self._ffmpeg_timeout, duration * 12.0),
        )

    def _probe(self, ffprobe: str, path: Path) -> dict:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._probe_timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise MuseTalkAvatarError(
                f"FFprobe timed out after {self._probe_timeout:.0f}s for {path}"
            ) from error
        if result.returncode != 0:
            raise MuseTalkAvatarError(f"FFprobe failed for {path}")
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise MuseTalkAvatarError("FFprobe returned invalid JSON") from error

    @staticmethod
    def _checked(command: list[str], message: str, *, timeout: float) -> None:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise MuseTalkAvatarError(
                f"{message}: timed out after {timeout:.0f}s"
            ) from error
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()[-2000:]
            raise MuseTalkAvatarError(f"{message}: {details}")
