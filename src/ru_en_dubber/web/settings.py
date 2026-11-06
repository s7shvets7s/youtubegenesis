from __future__ import annotations

import argparse
import math
from pathlib import Path

from ru_en_dubber.cli import build_parser
from ru_en_dubber.ingest import is_youtube_url
from ru_en_dubber.security import validate_job_id

HIDDEN = {"help", "source", "script", "audio_only", "jobs_dir", "job_id", "no_progress", "source_url_env",
          "video_mode_env", "avatar_source_env", "resume_job", "translate_job", "tts_job", "premiere_export_job",
          "tts_direction_file"}
MODES = {"new": None, "script": None, "resume": "--resume-job", "translate": "--translate-job",
         "tts": "--tts-job", "export": "--premiere-export-job"}
BOUNDS = {"tts_exaggeration": (0, 2), "tts_cfg_weight": (0, 1), "ad_confidence": (0, 1),
          "speech_target_lufs": (-24, -14), "speech_true_peak_db": (-6, -1),
          "capcut_scene_seconds": (0, 30),
          "music_min_score": (0, 1), "music_target_lufs": (-40, -8), "music_crossfade_seconds": (0, 10),
          "avatar_size": (160, 720), "avatar_margin": (0, 300)}


def valid_id(value: str) -> str:
    try:
        return validate_job_id(value)
    except ValueError as error:
        raise ValueError(
            "Job ID: 1-100 Latin letters, digits, hyphens or underscores"
        ) from error


def actions() -> dict:
    return {a.dest: a for a in build_parser()._actions if a.dest not in HIDDEN}


def group(name: str) -> str:
    """Return a stable group key; the Web UI maps it to a localized name."""
    if name == "capcut_scene_seconds":
        return "speech"
    if name.startswith("speech_"):
        return "speech"
    if name in {"ffmpeg", "ffprobe", "editing_python"}:
        return "tools"
    if name in {"scene_threshold", "minimum_episode_seconds", "maximum_episode_seconds", "no_premiere_export"}:
        return "export"
    if name == "no_tts":
        return "speech"
    if name == "animation_mode":
        return "video"
    if name == "visual_media_type":
        return "video"
    for prefix, label in [("demucs", "separation"), ("whisperx", "transcription"),
                          ("translation", "translation"), ("tts", "speech"),
                          ("music", "music"), ("avatar", "avatar"), ("musetalk", "avatar"),
                          ("premiere", "export"), ("visual", "video"),
                          ("media", "video"), ("rebuild", "video")]:
        if name.startswith(prefix):
            return label
    if name in {"llama_server", "vision_projector", "ad_removal", "ad_confidence"}:
        return "translation"
    if name in {"diarize", "min_speakers", "max_speakers"}:
        return "transcription"
    return "general"


def schema() -> list[dict]:
    return [dict(name=name, flag=a.option_strings[0], group=group(name),
                 help=a.help, default=str(a.default) if isinstance(a.default, Path) else a.default,
                 choices=list(a.choices) if a.choices is not None and not isinstance(a.choices, range) else None,
                 minimum=a.choices.start if isinstance(a.choices, range) else BOUNDS.get(name, (None, None))[0],
                 maximum=a.choices.stop - 1 if isinstance(a.choices, range) else BOUNDS.get(name, (None, None))[1],
                 kind="boolean" if isinstance(a, argparse._StoreTrueAction) else
                      "integer" if a.type is int else "number" if a.type is float else "string",
                 multiple=a.nargs == "+") for name, a in actions().items()]


def build_argv(
    payload: dict,
    root: Path,
    jobs: Path,
    job_id: str,
    *,
    script_path: Path | None = None,
    script_root: Path | None = None,
) -> list[str]:
    if not isinstance(payload, dict):
        raise ValueError("A settings object is expected")
    mode = payload.get("mode", "new")
    if mode not in MODES:
        raise ValueError("Unknown launch mode")
    output_mode = payload.get("output_mode", "video")
    if (
        not isinstance(output_mode, str)
        or output_mode not in {"video", "audio", "capcut"}
        or (mode != "script" and output_mode != "video")
    ):
        raise ValueError("Output mode is only available for an English script")
    options = payload.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    known = actions()
    if set(options) - known.keys():
        raise ValueError("Unknown settings: " + ", ".join(set(options) - known.keys()))
    argv = ["--jobs-dir", str(jobs), "--no-progress"]
    if mode == "script":
        if script_path is None or script_path.suffix.lower() not in {".txt", ".md", ".srt"}:
            raise ValueError("Provide the English script text")
        if output_mode == "capcut" and script_path.suffix.lower() != ".srt":
            raise ValueError("CapCut requires a timed .srt script")
        seconds = options.get("capcut_scene_seconds", 0)
        if output_mode != "capcut" and seconds not in (None, "", 0, "0"):
            raise ValueError("Scene length is only available in CapCut mode")
        resolved_script = script_path.resolve()
        allowed_root = (script_root or jobs).resolve()
        if not resolved_script.is_relative_to(allowed_root) or not resolved_script.is_file():
            raise ValueError("The script must be inside the video folder")
        argv += ["--job-id", valid_id(job_id), "--script", str(resolved_script)]
    elif mode == "new":
        source = payload.get("source", "")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Provide a source file or a YouTube link")
        if not is_youtube_url(source):
            source_path = (root / source).resolve()
            if not source_path.is_relative_to(root) or not source_path.is_file():
                raise ValueError("The source file must be inside the project folder")
            source = str(source_path)
        argv += ["--job-id", valid_id(job_id)]
    else:
        existing = valid_id(payload.get("job_id", ""))
        directory = (jobs / existing).resolve()
        if not directory.is_relative_to(jobs) or not (directory / "manifest.json").is_file():
            raise ValueError("Job not found")
        argv += [MODES[mode], existing]
    for name, value in options.items():
        a = known[name]
        if value is None or value == "":
            continue
        if isinstance(a, argparse._StoreTrueAction):
            if not isinstance(value, bool):
                raise ValueError(f"{name}: a toggle is expected")
            if value:
                argv.append(a.option_strings[0])
            continue
        values = value if a.nargs == "+" else [value]
        if not isinstance(values, list) or not values:
            raise ValueError(f"{name}: select at least one value")
        converted = []
        for item in values:
            if isinstance(item, (dict, list, bool)):
                raise ValueError(f"{name}: invalid type")
            if a.type is int and isinstance(item, float) and not item.is_integer():
                raise ValueError(f"{name}: an integer is required")
            try:
                result = (a.type or str)(item)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"{name}: invalid value") from None
            if isinstance(result, (int, float)) and not math.isfinite(result):
                raise ValueError(f"{name}: a finite number is required")
            if a.choices is not None and result not in a.choices:
                raise ValueError(f"{name}: value is not in the allowed list")
            if name in BOUNDS and not BOUNDS[name][0] <= result <= BOUNDS[name][1]:
                raise ValueError(f"{name}: allowed range {BOUNDS[name]}")
            if "\0" in str(result) or len(str(result)) > 32767:
                raise ValueError(f"{name}: invalid value")
            converted.append(str(result))
        if a.nargs == "+":
            argv += [a.option_strings[0], *converted]
        else:
            argv.append(a.option_strings[0] + "=" + converted[0])
    if mode == "new":
        argv += ["--", source]
    if mode == "script":
        if "video_mode" in options and options["video_mode"] not in {None, "", "rebuild"}:
            raise ValueError("Only a full visual rebuild is available for a script")
        if options.get("no_tts"):
            raise ValueError("A script requires voice synthesis")
        if output_mode in {"audio", "capcut"}:
            argv.append("--audio-only")
    args = build_parser().parse_args(argv)
    if args.capcut_scene_seconds == 1:
        raise ValueError("Scene length: 0 or 2-30 seconds")
    for name in ("scene_threshold", "minimum_episode_seconds", "maximum_episode_seconds", "rebuild_beat_seconds"):
        if getattr(args, name) <= 0:
            raise ValueError(f"{name}: value must be positive")
    if args.maximum_episode_seconds < args.minimum_episode_seconds:
        raise ValueError("Maximum episode length is less than the minimum")
    if (args.min_speakers is not None or args.max_speakers is not None) and not args.diarize:
        raise ValueError("Enable speaker diarization before setting the speaker counts")
    if args.min_speakers is not None and args.max_speakers is not None and args.min_speakers > args.max_speakers:
        raise ValueError("Minimum speakers exceeds the maximum")
    if args.avatar_mode == "musetalk" and args.avatar_source is None:
        raise ValueError("Provide a video for the avatar")
    if mode == "tts" and args.no_tts or mode == "export" and (args.no_tts or args.no_premiere_export):
        raise ValueError("The selected stage is disabled in the settings")
    return argv
