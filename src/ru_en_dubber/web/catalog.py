from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from ru_en_dubber.models import Manifest
from ru_en_dubber.workspace import WorkspaceCatalog

MEDIA = {".mp4", ".webm", ".mov", ".wav", ".mp3", ".ogg", ".m4a", ".srt"}


def summary(
    directory: Path,
    *,
    channel_id: str,
    video_id: str,
    video_title: str,
) -> dict:
    manifest = Manifest.load(directory / "manifest.json")
    input_info = manifest.metadata.get("input", {})
    is_script = input_info.get("type") == "script"
    return {
        "id": directory.name,
        "channel_id": channel_id,
        "video_id": video_id,
        "video_title": video_title,
        "status": manifest.job.status.value,
        "created_at": manifest.job.created_at.isoformat(),
        "updated_at": manifest.job.updated_at.isoformat(),
        "source": (
            "Английский сценарий"
            if is_script
            else (
                manifest.job.source_url
                or input_info.get("original_path", manifest.job.source_file)
            )
        ),
        "input_type": "script" if is_script else input_info.get("type", "file"),
        "segments": len(manifest.segments),
        "stages": manifest.completed_stages,
        "video_mode": manifest.metadata.get("video_mode", "original"),
        "output_mode": manifest.metadata.get("output_mode", "video"),
        "error": manifest.job.error,
        "visual_media_type": (
            manifest.metadata.get("visual_media_type")
            or manifest.metadata.get("visual_rebuild", {}).get("media_type_mode", "mixed")
        ),
        "no_safe_match_count": manifest.metadata.get("visual_rebuild", {}).get(
            "no_safe_match_count", 0
        ),
        "has_export": "premiere_export" in manifest.artifacts,
    }


def details(
    workspace: WorkspaceCatalog,
    channel_id: str,
    video_id: str,
    run_id: str,
) -> dict:
    directory = workspace.run_directory(channel_id, video_id, run_id)
    video = workspace.video_summary(workspace.video_directory(channel_id, video_id))
    manifest = Manifest.load(directory / "manifest.json")
    prefix = (
        f"/api/v1/channels/{quote(channel_id)}/videos/{quote(video_id)}"
        f"/runs/{quote(run_id)}/media/"
    )
    media = []
    for path in directory.rglob("*"):
        if (
            path.suffix.lower() not in MEDIA
            or not path.is_file()
            or not path.resolve().is_relative_to(directory)
        ):
            continue
        relative = path.relative_to(directory).as_posix()
        if path.suffix.lower() == ".srt" and relative != manifest.artifacts.get(
            "subtitles_srt"
        ):
            continue
        media.append(
            {
                "path": relative,
                "name": path.name,
                "bytes": path.stat().st_size,
                "kind": (
                    "audio"
                    if path.suffix.lower() in {".wav", ".mp3", ".ogg", ".m4a"}
                    else "subtitle" if path.suffix.lower() == ".srt" else "video"
                ),
                "url": prefix + quote(relative, safe="/"),
            }
        )
    episodes = _artifact_items(
        directory, manifest.artifacts.get("episode_plan"), "episodes"
    )
    visual_beats = _artifact_items(
        directory, manifest.artifacts.get("visual_plan"), "beats"
    )
    return {
        **summary(
            directory,
            channel_id=channel_id,
            video_id=video_id,
            video_title=video["working_title"],
        ),
        "schema_version": 2,
        "media": sorted(media, key=lambda item: item["path"]),
        "episodes": episodes,
        "visual_beats": visual_beats,
        "segments": [segment.to_dict() for segment in manifest.segments],
        "capabilities": {
            "preview": True,
            "episode_selection": True,
            "agent_edit": False,
            "timeline_edit": False,
        },
    }


def media_path(
    workspace: WorkspaceCatalog,
    channel_id: str,
    video_id: str,
    run_id: str,
    filename: str,
) -> Path:
    directory = workspace.run_directory(channel_id, video_id, run_id)
    target = (directory / filename).resolve()
    if (
        not target.is_relative_to(directory)
        or target.suffix.lower() not in MEDIA
        or not target.is_file()
    ):
        raise FileNotFoundError("Медиафайл не найден")
    return target


def _artifact_items(directory: Path, relative: str | None, field: str) -> list:
    if not relative:
        return []
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get(field, [])
    return value if isinstance(value, list) else []
