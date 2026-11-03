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
            "ID работы: 1–100 латинских букв, цифр, дефисов или подчёркиваний"
        ) from error


def actions() -> dict:
    return {a.dest: a for a in build_parser()._actions if a.dest not in HIDDEN}


def group(name: str) -> str:
    if name == "capcut_scene_seconds":
        return "Озвучка"
    if name.startswith("speech_"):
        return "Озвучка"
    if name in {"ffmpeg", "ffprobe", "editing_python"}:
        return "Инструменты и окружение"
    if name in {"scene_threshold", "minimum_episode_seconds", "maximum_episode_seconds", "no_premiere_export"}:
        return "Монтаж и экспорт"
    if name == "no_tts":
        return "Озвучка"
    if name == "animation_mode":
        return "Видеоряд"
    if name == "visual_media_type":
        return "Видеоряд"
    for prefix, label in [("demucs", "Разделение звука"), ("whisperx", "Распознавание"),
                          ("translation", "Перевод и анализ"), ("tts", "Озвучка"),
                          ("music", "Музыка"), ("avatar", "Аватар"), ("musetalk", "Аватар"),
                          ("premiere", "Монтаж и экспорт"), ("visual", "Видеоряд"),
                          ("media", "Видеоряд"), ("rebuild", "Видеоряд")]:
        if name.startswith(prefix):
            return label
    if name in {"llama_server", "vision_projector", "ad_removal", "ad_confidence"}:
        return "Перевод и анализ"
    if name in {"diarize", "min_speakers", "max_speakers"}:
        return "Распознавание"
    return "Общие"


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
        raise ValueError("Ожидается объект настроек")
    mode = payload.get("mode", "new")
    if mode not in MODES:
        raise ValueError("Неизвестный режим запуска")
    output_mode = payload.get("output_mode", "video")
    if (
        not isinstance(output_mode, str)
        or output_mode not in {"video", "audio", "capcut"}
        or (mode != "script" and output_mode != "video")
    ):
        raise ValueError("Режим результата доступен только для английского сценария")
    options = payload.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options должен быть объектом")
    known = actions()
    if set(options) - known.keys():
        raise ValueError("Неизвестные настройки: " + ", ".join(set(options) - known.keys()))
    argv = ["--jobs-dir", str(jobs), "--no-progress"]
    if mode == "script":
        if script_path is None or script_path.suffix.lower() not in {".txt", ".md", ".srt"}:
            raise ValueError("Укажите текст английского сценария")
        if output_mode == "capcut" and script_path.suffix.lower() != ".srt":
            raise ValueError("Для CapCut требуется сценарий с таймкодами .srt")
        seconds = options.get("capcut_scene_seconds", 0)
        if output_mode != "capcut" and seconds not in (None, "", 0, "0"):
            raise ValueError("Длительность сцены доступна только в режиме CapCut")
        resolved_script = script_path.resolve()
        allowed_root = (script_root or jobs).resolve()
        if not resolved_script.is_relative_to(allowed_root) or not resolved_script.is_file():
            raise ValueError("Сценарий должен находиться внутри папки ролика")
        argv += ["--job-id", valid_id(job_id), "--script", str(resolved_script)]
    elif mode == "new":
        source = payload.get("source", "")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Укажите исходный файл или ссылку YouTube")
        if not is_youtube_url(source):
            source_path = (root / source).resolve()
            if not source_path.is_relative_to(root) or not source_path.is_file():
                raise ValueError("Исходный файл должен находиться внутри рабочего проекта")
            source = str(source_path)
        argv += ["--job-id", valid_id(job_id)]
    else:
        existing = valid_id(payload.get("job_id", ""))
        directory = (jobs / existing).resolve()
        if not directory.is_relative_to(jobs) or not (directory / "manifest.json").is_file():
            raise ValueError("Работа не найдена")
        argv += [MODES[mode], existing]
    for name, value in options.items():
        a = known[name]
        if value is None or value == "":
            continue
        if isinstance(a, argparse._StoreTrueAction):
            if not isinstance(value, bool):
                raise ValueError(f"{name}: ожидается переключатель")
            if value:
                argv.append(a.option_strings[0])
            continue
        values = value if a.nargs == "+" else [value]
        if not isinstance(values, list) or not values:
            raise ValueError(f"{name}: выберите хотя бы одно значение")
        converted = []
        for item in values:
            if isinstance(item, (dict, list, bool)):
                raise ValueError(f"{name}: неверный тип")
            if a.type is int and isinstance(item, float) and not item.is_integer():
                raise ValueError(f"{name}: требуется целое число")
            try:
                result = (a.type or str)(item)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"{name}: неверное значение") from None
            if isinstance(result, (int, float)) and not math.isfinite(result):
                raise ValueError(f"{name}: требуется конечное число")
            if a.choices is not None and result not in a.choices:
                raise ValueError(f"{name}: значение не входит в список")
            if name in BOUNDS and not BOUNDS[name][0] <= result <= BOUNDS[name][1]:
                raise ValueError(f"{name}: допустимый диапазон {BOUNDS[name]}")
            if "\0" in str(result) or len(str(result)) > 32767:
                raise ValueError(f"{name}: неверное значение")
            converted.append(str(result))
        if a.nargs == "+":
            argv += [a.option_strings[0], *converted]
        else:
            argv.append(a.option_strings[0] + "=" + converted[0])
    if mode == "new":
        argv += ["--", source]
    if mode == "script":
        if "video_mode" in options and options["video_mode"] not in {None, "", "rebuild"}:
            raise ValueError("Для сценария доступна только полная пересборка видеоряда")
        if options.get("no_tts"):
            raise ValueError("Для сценария требуется озвучка")
        if output_mode in {"audio", "capcut"}:
            argv.append("--audio-only")
    args = build_parser().parse_args(argv)
    if args.capcut_scene_seconds == 1:
        raise ValueError("Длительность сцены: 0 или 2–30 секунд")
    for name in ("scene_threshold", "minimum_episode_seconds", "maximum_episode_seconds", "rebuild_beat_seconds"):
        if getattr(args, name) <= 0:
            raise ValueError(f"{name}: значение должно быть положительным")
    if args.maximum_episode_seconds < args.minimum_episode_seconds:
        raise ValueError("Максимальная длительность эпизода меньше минимальной")
    if (args.min_speakers is not None or args.max_speakers is not None) and not args.diarize:
        raise ValueError("Включите различение говорящих перед настройкой их количества")
    if args.min_speakers is not None and args.max_speakers is not None and args.min_speakers > args.max_speakers:
        raise ValueError("Минимум говорящих превышает максимум")
    if args.avatar_mode == "musetalk" and args.avatar_source is None:
        raise ValueError("Укажите видео для аватара")
    if mode == "tts" and args.no_tts or mode == "export" and (args.no_tts or args.no_premiere_export):
        raise ValueError("Выбранный этап отключён в настройках")
    return argv
