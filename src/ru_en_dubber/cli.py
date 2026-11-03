from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

from ru_en_dubber.ingest import (
    FFmpegAudioExtractionStage,
    YouTubeDownloadStage,
    is_youtube_url,
)
from ru_en_dubber.editing import DialogueTimingStage, PremiereExportStage
from ru_en_dubber.avatar import MuseTalkAvatarStage
from ru_en_dubber.content import ContentAnalysisStage
from ru_en_dubber.models import JobStatus, Manifest
from ru_en_dubber.music import MusicBedStage
from ru_en_dubber.pipeline.runner import (
    MANIFEST_FILENAME,
    PipelineRunner,
    create_job,
    create_job_from_url,
)
from ru_en_dubber.pipeline.progress import ConsolePipelineProgress, PipelineProgress
from ru_en_dubber.pipeline.script import (
    NarrationExportStage,
    ScriptTimingStage,
    SubtitleExportStage,
    create_job_from_script,
)
from ru_en_dubber.preflight import PreflightError, PreflightRequirements, run_preflight
from ru_en_dubber.security import resolve_job_directory
from ru_en_dubber.separation import DemucsSeparationStage
from ru_en_dubber.transcription import WhisperXTranscriptionStage
from ru_en_dubber.translation import QwenTranslationStage
from ru_en_dubber.tts import ChatterboxTTSStage, SpeechPostprocessStage
from ru_en_dubber.visuals import AnimationPlanStage, VisualRebuildStage


def default_media_tool(name: str) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    bundled = project_root() / ".tools" / "ffmpeg" / "bin" / executable
    return str(bundled) if bundled.is_file() else name


def project_root() -> Path:
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file() and (checkout / "src/ru_en_dubber").is_dir():
        return checkout
    return Path.cwd().resolve()


def default_demucs_python() -> str:
    executable = "python.exe" if os.name == "nt" else "python"
    bundled = project_root() / ".venv" / ("Scripts" if os.name == "nt" else "bin") / executable
    return str(bundled) if bundled.is_file() else sys.executable


def default_whisperx_python() -> str:
    executable = "python.exe" if os.name == "nt" else "python"
    bundled = project_root() / ".venv-whisperx" / (
        "Scripts" if os.name == "nt" else "bin"
    ) / executable
    return str(bundled) if bundled.is_file() else sys.executable


def default_llama_server() -> str:
    executable = "llama-server.exe" if os.name == "nt" else "llama-server"
    bundled = project_root() / ".tools" / "llama.cpp" / executable
    return str(bundled) if bundled.is_file() else executable


def default_tts_python() -> str:
    executable = "python.exe" if os.name == "nt" else "python"
    bundled = project_root() / ".venv-chatterbox" / (
        "Scripts" if os.name == "nt" else "bin"
    ) / executable
    return str(bundled) if bundled.is_file() else sys.executable


def default_musetalk_python() -> str:
    executable = "python.exe" if os.name == "nt" else "python"
    bundled = project_root() / ".venv-musetalk" / (
        "Scripts" if os.name == "nt" else "bin"
    ) / executable
    return str(bundled) if bundled.is_file() else sys.executable


def default_musetalk_root() -> Path:
    return project_root() / ".tools" / "MuseTalk"


def default_voice_reference() -> Path:
    return project_root() / "voices" / "my_voice" / "reference.wav"


def default_editing_python() -> str:
    executable = "python.exe" if os.name == "nt" else "python"
    bundled = project_root() / ".venv" / (
        "Scripts" if os.name == "nt" else "bin"
    ) / executable
    return str(bundled) if bundled.is_file() else sys.executable


def default_translation_model() -> Path:
    return project_root() / ".models" / "qwen" / "Qwen3VL-4B-Instruct-Q4_K_M.gguf"


def default_vision_projector() -> Path:
    return project_root() / ".models" / "qwen" / "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"


def default_music_library() -> Path:
    return project_root() / "source" / "music"


def load_local_secrets(path: Path | None = None) -> None:
    """Load supported local secrets without evaluating the .env file."""
    env_path = path or project_root() / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() not in {
            "PEXELS_API_KEY", "PIXABAY_API_KEY", "NARA_API_KEY", "JAMENDO_CLIENT_ID"
        }:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value and "\0" not in value:
            os.environ.setdefault(name.strip(), value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download or ingest a video, extract audio, separate vocals, "
            "transcribe with WhisperX, translate with Qwen, and synthesize "
            "English speech with Chatterbox."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="path to a source video or a YouTube URL",
    )
    parser.add_argument(
        "--script", type=Path,
        help="UTF-8 English narration script (.txt, .md or timed .srt)",
    )
    parser.add_argument(
        "--audio-only", action="store_true",
        help="for English scripts, export narration WAV and SRT without visuals or Premiere",
    )
    parser.add_argument(
        "--capcut-scene-seconds", type=int, default=0,
        help="fixed seconds per CapCut SRT cue (0 keeps source timecodes; 2-30 allowed)",
    )
    parser.add_argument(
        "--source-url-env",
        metavar="NAME",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path("jobs"),
        help="directory in which job artifacts are stored (default: jobs)",
    )
    parser.add_argument(
        "--minimum-free-gb",
        type=float,
        default=10.0,
        metavar="GIB",
        help="minimum free space required in the jobs directory (default: 10)",
    )
    parser.add_argument("--job-id", help="optional stable job identifier")
    parser.add_argument(
        "--video-mode",
        choices=("original", "source-clean", "hybrid", "rebuild"),
        default=None,
        help=(
            "clean the source, selectively replace visuals, or rebuild the picture; "
            "original preserves every source frame; new jobs default to hybrid"
        ),
    )
    parser.add_argument(
        "--video-mode-env",
        metavar="NAME",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--resume-job",
        metavar="JOB_ID",
        help="transcribe, translate, and voice an existing ingested job",
    )
    parser.add_argument(
        "--translate-job",
        metavar="JOB_ID",
        help="translate and voice an existing transcribed job",
    )
    parser.add_argument(
        "--tts-job",
        metavar="JOB_ID",
        help="voice an existing translated job without running WhisperX or Qwen",
    )
    parser.add_argument(
        "--premiere-export-job",
        metavar="JOB_ID",
        help="export an existing voiced job as an editable Premiere package",
    )
    parser.add_argument(
        "--ffmpeg",
        default=default_media_tool("ffmpeg"),
        help="FFmpeg executable name or full path",
    )
    parser.add_argument(
        "--ffprobe",
        default=default_media_tool("ffprobe"),
        help="FFprobe executable name or full path",
    )
    parser.add_argument(
        "--demucs-python",
        default=default_demucs_python(),
        help="Python executable containing the Demucs package",
    )
    parser.add_argument(
        "--demucs-model",
        default="htdemucs",
        help="Demucs model name (default: htdemucs)",
    )
    parser.add_argument(
        "--demucs-device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="separation device (default: auto)",
    )
    parser.add_argument(
        "--demucs-segment",
        type=int,
        choices=range(1, 8),
        default=7,
        metavar="SECONDS",
        help="chunk size from 1 to 7 seconds (default: 7)",
    )
    parser.add_argument(
        "--cookies-from-browser",
        choices=("brave", "chrome", "chromium", "edge", "firefox", "opera", "vivaldi"),
        help="use browser cookies for a YouTube video that requires sign-in",
    )
    parser.add_argument(
        "--whisperx-python",
        default=default_whisperx_python(),
        help="Python executable containing the WhisperX package",
    )
    parser.add_argument(
        "--whisperx-model",
        default="medium",
        help="Whisper model name (default: medium)",
    )
    parser.add_argument(
        "--whisperx-device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="transcription device (default: auto)",
    )
    parser.add_argument(
        "--whisperx-batch-size",
        type=int,
        choices=range(1, 33),
        default=4,
        metavar="N",
        help="WhisperX batch size from 1 to 32 (default: 4)",
    )
    parser.add_argument(
        "--diarize",
        action="store_true",
        help=(
            "identify multiple speakers with WhisperX/PyAnnote; requires access "
            "to the Hugging Face diarization model"
        ),
    )
    parser.add_argument(
        "--min-speakers",
        type=int,
        metavar="N",
        help="optional minimum speaker count for diarization",
    )
    parser.add_argument(
        "--max-speakers",
        type=int,
        metavar="N",
        help="optional maximum speaker count for diarization",
    )
    parser.add_argument(
        "--llama-server",
        default=default_llama_server(),
        help="llama-server executable name or full path",
    )
    parser.add_argument(
        "--translation-model",
        type=Path,
        default=default_translation_model(),
        help="path to the Qwen GGUF translation model",
    )
    parser.add_argument(
        "--vision-projector",
        type=Path,
        default=default_vision_projector(),
        help="path to the Qwen-VL GGUF vision projector",
    )
    parser.add_argument(
        "--translation-batch-size",
        type=int,
        choices=range(1, 21),
        default=8,
        metavar="N",
        help="number of transcript segments per Qwen request (default: 8)",
    )
    parser.add_argument(
        "--translation-gpu-layers",
        type=int,
        default=99,
        metavar="N",
        help="Qwen layers offloaded to the GPU; use 0 for CPU (default: 99)",
    )
    parser.add_argument(
        "--ad-removal",
        choices=("off", "mark", "remove"),
        default="remove",
        help=(
            "detect sponsor integrations: disable, only mark, or ripple-remove "
            "from the Premiere export (default: remove)"
        ),
    )
    parser.add_argument(
        "--ad-confidence",
        type=float,
        default=0.85,
        metavar="VALUE",
        help="minimum Qwen confidence for an advertising range (default: 0.85)",
    )
    parser.add_argument(
        "--tts-python",
        default=default_tts_python(),
        help="Python executable containing Chatterbox",
    )
    parser.add_argument(
        "--tts-provider", choices=("chatterbox", "cosyvoice3"), default="chatterbox",
        help="speech synthesis provider (default: chatterbox)",
    )
    parser.add_argument(
        "--tts-model-dir", type=Path,
        help="local CosyVoice 3 model directory",
    )
    parser.add_argument(
        "--tts-direction-file", type=Path,
        help="per-segment expressive delivery JSON",
    )
    parser.add_argument(
        "--tts-reference",
        type=Path,
        default=default_voice_reference(),
        help="voice-cloning reference WAV",
    )
    parser.add_argument(
        "--tts-device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="Chatterbox device (default: auto)",
    )
    parser.add_argument(
        "--tts-model",
        choices=("v2", "v3"),
        default="v3",
        help="Chatterbox multilingual model (default: v3)",
    )
    parser.add_argument(
        "--tts-exaggeration",
        type=float,
        default=0.5,
        metavar="VALUE",
        help="Chatterbox expressiveness from 0 to 2 (default: 0.5)",
    )
    parser.add_argument(
        "--tts-cfg-weight",
        type=float,
        default=0.5,
        metavar="VALUE",
        help="Chatterbox CFG weight from 0 to 1 (default: 0.5)",
    )
    parser.add_argument(
        "--speech-postprocess", choices=("speech", "off"), default="speech",
        help="generated speech mastering profile, enabled for all TTS providers (default: speech)",
    )
    parser.add_argument(
        "--speech-target-lufs", type=float, default=-18.0,
        help="processed voice loudness target, -24 to -14 LUFS (default: -18)",
    )
    parser.add_argument(
        "--speech-true-peak-db", type=float, default=-2.0,
        help="processed voice true-peak ceiling, -6 to -1 dBTP (default: -2)",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="stop after translation without generating speech",
    )
    parser.add_argument(
        "--editing-python",
        default=default_editing_python(),
        help="Python executable containing PySceneDetect",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=3.0,
        metavar="VALUE",
        help="PySceneDetect adaptive threshold (default: 3.0)",
    )
    parser.add_argument(
        "--minimum-episode-seconds",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="merge short shots to at least this duration (default: 2)",
    )
    parser.add_argument(
        "--maximum-episode-seconds",
        type=float,
        default=15.0,
        metavar="SECONDS",
        help="split long episodes near this duration (default: 15)",
    )
    parser.add_argument(
        "--premiere-codec",
        choices=("h264", "prores"),
        default="h264",
        help="codec for replaceable scene clips (default: h264)",
    )
    parser.add_argument(
        "--premiere-device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="video render device with automatic CPU fallback (default: auto)",
    )
    parser.add_argument(
        "--rebuild-beat-seconds",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="target duration of one stock-footage beat in rebuild mode (default: 10)",
    )
    parser.add_argument(
        "--rebuild-candidates",
        type=int,
        choices=range(1, 21),
        default=6,
        metavar="N",
        help="candidate count per provider query (default: 6)",
    )
    parser.add_argument("--media-providers", nargs="+", choices=("pexels", "pixabay", "wikimedia", "nara", "loc"),
                        default=["pexels", "pixabay", "wikimedia", "nara", "loc"], help="enabled visual media sources")
    parser.add_argument("--visual-planner", choices=("auto", "legacy"), default="auto",
                        help="local Qwen semantic planner, or legacy illustrative keyword search")
    parser.add_argument("--visual-media-type", choices=("image", "mixed", "video"), default=None,
                        help="use only still images, both images and videos, or only videos (default: mixed; existing jobs retain their choice)")
    parser.add_argument("--visual-requests", type=Path, help="JSON array overriding semantic requests, one per beat")
    parser.add_argument("--visual-fallback", choices=("card", "original"), default="original",
                        help="fallback policy; safety rules always preserve source footage on analysis failure")
    parser.add_argument("--animation-mode", choices=("off", "templates", "agent"), default="templates",
                        help="disable animation, use trusted templates, or let local Qwen fill strict template specs")
    parser.add_argument(
        "--avatar-mode",
        choices=("off", "musetalk"),
        default="off",
        help="add a local circular talking avatar after visual rebuild (default: off)",
    )
    parser.add_argument(
        "--avatar-source",
        type=Path,
        help="front-facing motion video used as the reusable MuseTalk avatar",
    )
    parser.add_argument("--avatar-source-env", metavar="NAME", help=argparse.SUPPRESS)
    parser.add_argument(
        "--musetalk-python",
        default=default_musetalk_python(),
        help="Python executable containing MuseTalk 1.5 dependencies",
    )
    parser.add_argument(
        "--musetalk-root",
        type=Path,
        default=default_musetalk_root(),
        help="local checkout containing MuseTalk scripts and models",

    )
    parser.add_argument("--avatar-size", type=int, default=360, metavar="PX",
                        help="circular avatar diameter from 160 to 720 pixels (default: 360)")
    parser.add_argument("--avatar-position", choices=("top-left", "top-right", "bottom-left", "bottom-right"),
                        default="bottom-right", help="avatar corner (default: bottom-right)")
    parser.add_argument("--avatar-margin", type=int, default=48, metavar="PX",
                        help="avatar distance from frame edges, 0 to 300 pixels (default: 48)")
    parser.add_argument("--musetalk-batch-size", type=int, choices=range(1, 9), default=2, metavar="N",
                        help="MuseTalk inference batch size; 2 is safe for 6 GB VRAM")
    parser.add_argument(
        "--music-mode", choices=("off", "local", "hybrid"), default="hybrid",
        help="disable AI music, use only the local library, or fall back to Jamendo",
    )
    parser.add_argument(
        "--music-library", type=Path, default=default_music_library(),
        help="project music library containing catalog.json",
    )
    parser.add_argument(
        "--music-min-score", type=float, default=0.72, metavar="VALUE",
        help="minimum local music relevance score (default: 0.72)",
    )
    parser.add_argument(
        "--music-target-lufs", type=float, default=-24.0, metavar="LUFS",
        help="integrated loudness for the separate AI music track (default: -24)",
    )
    parser.add_argument(
        "--music-crossfade-seconds", type=float, default=3.0, metavar="SECONDS",
        help="crossfade between playlist tracks (default: 3)",
    )
    parser.add_argument(
        "--music-request-budget", type=int, choices=range(1, 101), default=20, metavar="N",
        help="maximum Jamendo API calls per run (default: 20)",
    )
    parser.add_argument("--media-request-budget", type=int, choices=range(1, 10001), default=120, metavar="N",
                        help="maximum API calls per visual rebuild run")
    parser.add_argument(
        "--no-premiere-export",
        action="store_true",
        help="stop after TTS without creating a Premiere package",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable the interactive progress display (useful for logs and CI)",
    )
    return parser


def main(argv: list[str] | None = None, *, progress: PipelineProgress | None = None) -> int:
    load_local_secrets()
    args = build_parser().parse_args(argv)
    if args.source_url_env is not None:
        if args.source is not None:
            raise SystemExit("source cannot be combined with --source-url-env")
        value = os.environ.get(args.source_url_env, "")
        if not value or len(value) > 4096 or "\0" in value:
            raise SystemExit("source URL environment variable is empty or invalid")
        args.source = value
    if args.script is not None and args.source is not None:
        raise SystemExit("--script cannot be combined with a video source")
    if args.script is not None and args.no_tts:
        raise SystemExit("--script requires TTS")
    if args.capcut_scene_seconds and (
        args.script is None or args.script.suffix.lower() != ".srt"
        or not args.audio_only or not 2 <= args.capcut_scene_seconds <= 30
    ):
        raise SystemExit("--capcut-scene-seconds requires --script .srt --audio-only and 2-30")
    if args.video_mode_env is not None:
        if args.video_mode is not None:
            raise SystemExit("--video-mode cannot be combined with --video-mode-env")
        choice = os.environ.get(args.video_mode_env, "1").strip()
        modes = {
            "1": "source-clean",
            "2": "hybrid",
            "3": "rebuild",
            "4": "original",
            "original": "original",
            "source-clean": "source-clean",
            "hybrid": "hybrid",
            "rebuild": "rebuild",
        }
        if choice not in modes:
            raise SystemExit(
                "video mode must be 1 (source-clean), 2 (hybrid), "
                "3 (rebuild), or 4 (original)"
            )
        args.video_mode = modes[choice]
    if args.avatar_source_env is not None:
        if args.avatar_source is not None:
            raise SystemExit("--avatar-source cannot be combined with --avatar-source-env")
        value = os.environ.get(args.avatar_source_env, "")
        if not value or len(value) > 32767 or "\0" in value:
            raise SystemExit("avatar source environment variable is empty or invalid")
        args.avatar_source = Path(value)
    if (args.min_speakers is not None or args.max_speakers is not None) and not args.diarize:
        raise SystemExit("--min-speakers/--max-speakers require --diarize")
    if (
        args.min_speakers is not None
        and args.max_speakers is not None
        and args.min_speakers > args.max_speakers
    ):
        raise SystemExit("--min-speakers must not exceed --max-speakers")
    if not 0.0 <= args.ad_confidence <= 1.0:
        raise SystemExit("--ad-confidence must be between 0 and 1")
    if not math.isfinite(args.minimum_free_gb) or args.minimum_free_gb < 0:
        raise SystemExit("--minimum-free-gb must be a non-negative finite number")
    if args.avatar_mode == "musetalk" and args.avatar_source is None:
        raise SystemExit("--avatar-source is required with --avatar-mode musetalk")
    if not 160 <= args.avatar_size <= 720:
        raise SystemExit("--avatar-size must be between 160 and 720")
    if not 0 <= args.avatar_margin <= 300:
        raise SystemExit("--avatar-margin must be between 0 and 300")
    stages = []
    if not math.isfinite(args.speech_target_lufs) or not -24 <= args.speech_target_lufs <= -14:
        raise SystemExit("--speech-target-lufs must be between -24 and -14")
    if not math.isfinite(args.speech_true_peak_db) or not -6 <= args.speech_true_peak_db <= -1:
        raise SystemExit("--speech-true-peak-db must be between -6 and -1")
    existing_options = [
        args.resume_job,
        args.translate_job,
        args.tts_job,
        args.premiere_export_job,
    ]
    if sum(value is not None for value in existing_options) > 1:
        raise SystemExit(
            "existing-job options are mutually exclusive"
        )
    if args.tts_job is not None and args.no_tts:
        raise SystemExit("--tts-job cannot be combined with --no-tts")
    if args.premiere_export_job is not None and (
        args.no_tts or args.no_premiere_export
    ):
        raise SystemExit(
            "--premiere-export-job cannot be combined with a disabled output stage"
        )
    existing_job_id = (
        args.resume_job
        or args.translate_job
        or args.tts_job
        or args.premiere_export_job
    )
    if existing_job_id is not None:
        if args.source is not None or args.script is not None:
            raise SystemExit("source or script cannot be combined with an existing-job option")
        if args.job_id is not None:
            raise SystemExit("--job-id cannot be combined with an existing-job option")
        if args.cookies_from_browser is not None:
            raise SystemExit(
                "--cookies-from-browser cannot be used with an existing-job option"
            )
        try:
            job_dir = resolve_job_directory(args.jobs_dir, existing_job_id)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        manifest_path = job_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise SystemExit(f"job manifest not found: {manifest_path}")
        manifest = Manifest.load(manifest_path)
    elif args.script is not None:
        try:
            manifest, job_dir = create_job_from_script(
                args.script, args.jobs_dir, args.job_id,
                capcut_scene_seconds=args.capcut_scene_seconds,
            )
        except (ValueError, OSError) as error:
            raise SystemExit(str(error)) from error
    elif args.source is None:
        raise SystemExit(
            "source is required unless an existing-job option is used"
        )
    elif is_youtube_url(args.source):
        manifest, job_dir = create_job_from_url(args.source, args.jobs_dir, args.job_id)
        stages.append(
            YouTubeDownloadStage(
                python=args.demucs_python,
                ffmpeg=args.ffmpeg,
                cookies_from_browser=args.cookies_from_browser,
            )
        )
    else:
        if args.cookies_from_browser is not None:
            raise SystemExit("--cookies-from-browser can only be used with a YouTube URL")
        manifest, job_dir = create_job(Path(args.source), args.jobs_dir, args.job_id)

    script_mode = manifest.metadata.get("input", {}).get("type") == "script"
    if args.audio_only and not script_mode:
        raise SystemExit("--audio-only requires an English script job")
    if args.audio_only and args.premiere_export_job is not None:
        raise SystemExit("--audio-only cannot be combined with --premiere-export-job")
    audio_only = script_mode and (
        args.audio_only
        or (
            manifest.metadata.get("output_mode") == "audio"
            and args.premiere_export_job is None
        )
    )
    if audio_only and args.no_premiere_export:
        raise SystemExit("--audio-only cannot be combined with --no-premiere-export")
    if audio_only and args.avatar_mode != "off":
        raise SystemExit("--audio-only cannot be combined with an avatar")
    if script_mode and args.video_mode not in {None, "rebuild"}:
        raise SystemExit("script jobs require --video-mode rebuild")
    if script_mode and args.no_tts:
        raise SystemExit("script jobs require TTS")
    saved_video_mode = str(manifest.metadata.get("video_mode") or "").strip()
    supported_video_modes = {"original", "source-clean", "hybrid", "rebuild"}
    requested_video_mode = args.video_mode
    video_mode = "rebuild" if script_mode else requested_video_mode or (
        saved_video_mode if saved_video_mode in supported_video_modes else "hybrid"
    )
    manifest.metadata["video_mode"] = video_mode
    manifest.metadata["output_mode"] = "audio" if audio_only else "video"
    saved_media_type = manifest.metadata.get("visual_media_type")
    visual_media_type = args.visual_media_type or (
        saved_media_type if saved_media_type in {"image", "mixed", "video"} else "mixed"
    )
    manifest.metadata["visual_media_type"] = visual_media_type
    try:
        _run_preflight(
            args, video_mode, script_mode=script_mode, audio_only=audio_only,
            timed_script=bool(manifest.metadata.get("script", {}).get("timed_cues")),
        )
    except SystemExit as error:
        manifest.job.error = str(error)
        manifest.job.touch(status=JobStatus.FAILED)
        manifest.save(job_dir / MANIFEST_FILENAME)
        raise
    if video_mode in {"original", "source-clean", "hybrid"}:
        manifest.artifacts["source_video"] = manifest.job.source_file

    if existing_job_id is None and not script_mode:
        stages.extend(
            [
                FFmpegAudioExtractionStage(
                    ffmpeg=args.ffmpeg,
                    ffprobe=args.ffprobe,
                ),
                DemucsSeparationStage(
                    python=args.demucs_python,
                    model=args.demucs_model,
                    device=args.demucs_device,
                    segment_seconds=args.demucs_segment,
                    model_cache=project_root() / ".models",
                ),
            ]
        )

    if not script_mode and (
        args.translate_job is None
        and args.tts_job is None
        and args.premiere_export_job is None
    ):
        ffmpeg_path = Path(args.ffmpeg)
        stages.append(
            WhisperXTranscriptionStage(
                python=args.whisperx_python,
                model=args.whisperx_model,
                device=args.whisperx_device,
                batch_size=args.whisperx_batch_size,
                diarize=args.diarize,
                min_speakers=args.min_speakers,
                max_speakers=args.max_speakers,
                model_cache=project_root() / ".models",
                ffmpeg_bin=(
                    ffmpeg_path.resolve().parent if ffmpeg_path.is_file() else None
                ),
            )
        )
    if not script_mode and args.tts_job is None and args.premiere_export_job is None:
        stages.append(
            QwenTranslationStage(
                server=args.llama_server,
                model=args.translation_model,
                batch_size=args.translation_batch_size,
                gpu_layers=args.translation_gpu_layers,
                ad_removal_mode=args.ad_removal if video_mode == "original" else "off",
            )
        )
    if not script_mode and args.premiere_export_job is None and video_mode != "original":
        stages.append(
            ContentAnalysisStage(
                server=args.llama_server,
                model=args.translation_model,
                vision_projector=args.vision_projector,
                python=args.editing_python,
                source_mode=video_mode,
                ad_removal_mode=args.ad_removal,
                ad_confidence_threshold=args.ad_confidence,
                batch_size=args.translation_batch_size,
                gpu_layers=args.translation_gpu_layers,
                scene_threshold=args.scene_threshold,
            )
        )
        stages.append(
            AnimationPlanStage(
                mode=args.animation_mode,
                server=args.llama_server,
                model=args.translation_model,
                gpu_layers=args.translation_gpu_layers,
            )
        )
    if not args.no_tts and args.premiere_export_job is None:
        stages.append(
            ChatterboxTTSStage(
                python=args.tts_python,
                reference=args.tts_reference,
                model=args.tts_model,
                device=args.tts_device,
                exaggeration=args.tts_exaggeration,
                cfg_weight=args.tts_cfg_weight,
                model_cache=project_root() / ".models",
                provider=args.tts_provider,
                model_dir=args.tts_model_dir,
                direction_file=args.tts_direction_file,
            )
        )
    if not args.no_tts:
        stages.append(SpeechPostprocessStage(
            ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, profile=args.speech_postprocess,
            target_lufs=args.speech_target_lufs, true_peak_db=args.speech_true_peak_db,
        ))
    if not args.no_tts and not args.no_premiere_export:
        if script_mode:
            stages.append(ScriptTimingStage(ffmpeg=args.ffmpeg))
        else:
            stages.append(
                DialogueTimingStage(
                    ffmpeg=args.ffmpeg,
                    ffprobe=args.ffprobe,
                )
            )
        if audio_only:
            stages.append(NarrationExportStage())
            stages.append(SubtitleExportStage())
        else:
            if script_mode:
                stages.append(SubtitleExportStage())
            if (
                video_mode != "original"
                and (
                    args.premiere_export_job is None
                    or video_mode == "rebuild"
                    or "episode_plan" in manifest.artifacts
                )
            ):
                stages.append(
                    VisualRebuildStage(
                        ffmpeg=args.ffmpeg,
                        ffprobe=args.ffprobe,
                        target_beat_seconds=args.rebuild_beat_seconds,
                        candidates_per_query=args.rebuild_candidates,
                        provider_names=tuple(args.media_providers),
                        planner_mode=args.visual_planner,
                        planner_server=args.llama_server,
                        planner_model=args.translation_model,
                        planner_gpu_layers=args.translation_gpu_layers,
                        requests_file=args.visual_requests,
                        fallback=args.visual_fallback,
                        request_budget=args.media_request_budget,
                        media_type_mode=visual_media_type,
                    )
                )
            if args.avatar_mode == "musetalk":
                stages.append(
                    MuseTalkAvatarStage(
                        python=args.musetalk_python,
                        musetalk_root=args.musetalk_root,
                        avatar_source=args.avatar_source,
                        ffmpeg=args.ffmpeg,
                        ffprobe=args.ffprobe,
                        size=args.avatar_size,
                        position=args.avatar_position,
                        margin=args.avatar_margin,
                        batch_size=args.musetalk_batch_size,
                    )
                )
            stages.append(
                MusicBedStage(
                    mode=args.music_mode,
                    library=args.music_library,
                    server=args.llama_server,
                    model=args.translation_model,
                    ffmpeg=args.ffmpeg,
                    ffprobe=args.ffprobe,
                    jamendo_client_id=os.environ.get("JAMENDO_CLIENT_ID", ""),
                    min_score=args.music_min_score,
                    target_lufs=args.music_target_lufs,
                    crossfade_seconds=args.music_crossfade_seconds,
                    request_budget=args.music_request_budget,
                    gpu_layers=args.translation_gpu_layers,
                )
            )
            stages.append(
                PremiereExportStage(
                    python=args.editing_python,
                    ffmpeg=args.ffmpeg,
                    ffprobe=args.ffprobe,
                    threshold=args.scene_threshold,
                    minimum_episode_seconds=args.minimum_episode_seconds,
                    maximum_episode_seconds=args.maximum_episode_seconds,
                    codec=args.premiere_codec,
                    device=args.premiere_device,
                )
            )
    runner = PipelineRunner(
        stages,
        progress=progress if progress is not None else (None if args.no_progress else ConsolePipelineProgress()),
    )
    runner.run(manifest, job_dir)
    print(job_dir / "manifest.json")
    return 0


def _run_preflight(
    args: argparse.Namespace, video_mode: str, *, script_mode: bool = False,
    audio_only: bool = False, timed_script: bool = False,
) -> None:
    executables: dict[str, str] = {}
    files: dict[str, Path] = {}

    def executable(label: str, command: str) -> None:
        executables.setdefault(label, command)

    def file(label: str, path: Path) -> None:
        files.setdefault(label, path)

    is_new_job = not any(
        (args.resume_job, args.translate_job, args.tts_job, args.premiere_export_job)
    )
    creates_output = not args.no_tts and not args.no_premiere_export
    runs_transcription = not script_mode and (
        args.translate_job is None
        and args.tts_job is None
        and args.premiere_export_job is None
    )
    runs_translation = not script_mode and args.tts_job is None and args.premiere_export_job is None
    runs_content_analysis = not script_mode and args.premiere_export_job is None and video_mode != "original"

    if (is_new_job and not script_mode) or (creates_output and not audio_only) or timed_script:
        executable("FFmpeg", args.ffmpeg)
        if not audio_only:
            executable("FFprobe", args.ffprobe)
    if not args.no_tts and args.speech_postprocess != "off":
        executable("FFmpeg", args.ffmpeg)
        executable("FFprobe", args.ffprobe)
    if is_new_job and not script_mode:
        executable("Demucs Python", args.demucs_python)
    if runs_transcription:
        executable("WhisperX Python", args.whisperx_python)
    if runs_translation or runs_content_analysis or (
        creates_output and not audio_only
        and (video_mode != "original" or args.music_mode != "off")
    ):
        executable("llama-server", args.llama_server)
        file("Qwen translation model", args.translation_model)
    if runs_content_analysis:
        executable("editing Python", args.editing_python)
        file("Qwen vision projector", args.vision_projector)
    if not args.no_tts and args.premiere_export_job is None:
        executable(
            "Chatterbox Python" if args.tts_provider == "chatterbox" else "CosyVoice Python",
            args.tts_python,
        )
        file("TTS voice reference", args.tts_reference)
        if args.tts_provider == "cosyvoice3":
            if args.tts_model_dir is None:
                raise SystemExit("--tts-model-dir is required for CosyVoice 3")
            file("CosyVoice 3 model config", args.tts_model_dir / "cosyvoice3.yaml")
        if args.tts_direction_file is not None:
            file("TTS direction file", args.tts_direction_file)
    if creates_output and not audio_only:
        executable("editing Python", args.editing_python)
    if args.avatar_mode == "musetalk" and creates_output:
        executable("MuseTalk Python", args.musetalk_python)
        file("MuseTalk avatar source", args.avatar_source)
        file("MuseTalk inference script", args.musetalk_root / "scripts" / "inference.py")

    requirements = PreflightRequirements(
        executables=tuple(executables.items()),
        files=tuple(files.items()),
        work_directory=args.jobs_dir,
        minimum_free_bytes=int(args.minimum_free_gb * 1024**3),
    )
    try:
        run_preflight(requirements)
    except PreflightError as error:
        raise SystemExit(f"Preflight failed: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main())
