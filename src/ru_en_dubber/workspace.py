from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


_ENTITY_ID = re.compile(r"^[cv][a-f0-9]{8}$")
_RUN_ID = re.compile(r"^run-[0-9]{8}-[0-9]{6}-[a-f0-9]{4}$")
SCRIPT_STATUSES = {"planning", "drafting", "review", "ready"}
MAX_SCRIPT_BYTES = 128_000
_CYRILLIC = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def slugify(value: str, *, fallback: str, maximum: int = 48) -> str:
    """Return a short, portable ASCII slug for a user-facing name."""
    lowered = value.strip().lower().translate(_CYRILLIC)
    normalized = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    slug = slug[:maximum].rstrip("-")
    return slug or fallback


def validate_entity_id(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _ENTITY_ID.fullmatch(value) is None:
        raise ValueError(f"Некорректный ID {label}")
    return value


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("Некорректный ID запуска")
    return value


def _name(value: Any, *, label: str, maximum: int = 160) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}: ожидается текст")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or "\0" in cleaned:
        raise ValueError(f"{label}: укажите от 1 до {maximum} символов")
    return cleaned


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Ожидается JSON-объект: {path.name}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class WorkspaceCatalog:
    """Filesystem catalog for managed channels, videos, and pipeline runs."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self.channels = (self.root / "channels").resolve()
        self.runtime = (self.root / ".runtime" / "runs").resolve()
        if not self.channels.is_relative_to(self.root) or not self.runtime.is_relative_to(
            self.root
        ):
            raise ValueError("Каталоги workspace должны находиться внутри проекта")
        self.channels.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_run_id() -> str:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        return f"run-{stamp}-{uuid4().hex[:4]}"

    def create_channel(self, display_name: str) -> dict[str, Any]:
        display_name = _name(display_name, label="Название канала")
        channel_id = f"c{uuid4().hex[:8]}"
        slug = slugify(display_name, fallback="channel")
        directory = self.channels / f"{slug}--{channel_id}"
        directory.mkdir()
        for relative in (
            "brand",
            "research/competitors",
            "playbooks",
            "videos",
        ):
            (directory / relative).mkdir(parents=True)
        now = utc_now()
        _write_json(
            directory / "channel.json",
            {
                "schema_version": 1,
                "id": channel_id,
                "display_name": display_name,
                "folder_slug": slug,
                "created_at": now,
                "updated_at": now,
            },
        )
        return self.channel_summary(directory)

    def list_channels(self) -> dict[str, Any]:
        channels: list[dict[str, Any]] = []
        warnings: list[str] = []
        for metadata in self.channels.glob("*/channel.json"):
            try:
                directory = metadata.parent.resolve()
                if not directory.is_relative_to(self.channels):
                    continue
                channels.append(self.channel_summary(directory))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                warnings.append(f"Не удалось прочитать канал {metadata.parent.name}")
        channels.sort(key=lambda item: item["updated_at"], reverse=True)
        return {"channels": channels, "warnings": warnings}

    def channel_directory(self, channel_id: str) -> Path:
        channel_id = validate_entity_id(channel_id, label="канала")
        return self._entity_directory(
            self.channels, channel_id, metadata_name="channel.json", field="id"
        )

    def channel_summary(self, directory: Path) -> dict[str, Any]:
        data = _read_json(directory / "channel.json")
        channel_id = validate_entity_id(str(data["id"]), label="канала")
        videos = self._video_metadata_paths(directory)
        competitors_root = directory / "research" / "competitors"
        playbooks_root = directory / "playbooks"
        competitors = [
            path
            for path in competitors_root.iterdir()
            if path.is_dir()
        ] if competitors_root.is_dir() else []
        playbooks = [
            path for path in playbooks_root.iterdir() if path.is_dir()
        ] if playbooks_root.is_dir() else []
        return {
            "id": channel_id,
            "display_name": _name(data["display_name"], label="Название канала"),
            "folder_name": directory.name,
            "path": str(directory),
            "created_at": str(data["created_at"]),
            "updated_at": str(data.get("updated_at", data["created_at"])),
            "video_count": len(videos),
            "competitor_count": len(competitors),
            "playbook_count": len(playbooks),
        }

    def channel_details(self, channel_id: str) -> dict[str, Any]:
        directory = self.channel_directory(channel_id)
        result = self.channel_summary(directory)
        result["videos"] = self.list_videos(channel_id)["videos"]
        result["competitors"] = self._named_directories(
            directory / "research" / "competitors"
        )
        result["playbooks"] = self._named_directories(directory / "playbooks")
        return result

    def create_video(self, channel_id: str, working_title: str) -> dict[str, Any]:
        channel = self.channel_directory(channel_id)
        working_title = _name(working_title, label="Название ролика")
        video_id = f"v{uuid4().hex[:8]}"
        slug = slugify(working_title, fallback="video")
        date_prefix = datetime.now().astimezone().strftime("%Y-%m-%d")
        directory = channel / "videos" / f"{date_prefix}-{slug}--{video_id}"
        directory.mkdir()
        (directory / "runs").mkdir()
        (directory / "review-prompts").mkdir()
        now = utc_now()
        _write_json(
            directory / "video.json",
            {
                "schema_version": 2,
                "id": video_id,
                "channel_id": channel_id,
                "working_title": working_title,
                "publish_title": None,
                "folder_slug": slug,
                "script_file": "script.md",
                "script_status": "planning",
                "created_at": now,
                "updated_at": now,
            },
        )
        self._touch_channel(channel)
        return self.video_summary(directory)

    def list_videos(self, channel_id: str) -> dict[str, Any]:
        channel = self.channel_directory(channel_id)
        videos: list[dict[str, Any]] = []
        warnings: list[str] = []
        for metadata in self._video_metadata_paths(channel):
            try:
                videos.append(self.video_summary(metadata.parent))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                warnings.append(f"Не удалось прочитать ролик {metadata.parent.name}")
        videos.sort(key=lambda item: item["updated_at"], reverse=True)
        return {"videos": videos, "warnings": warnings}

    def video_directory(self, channel_id: str, video_id: str) -> Path:
        channel = self.channel_directory(channel_id)
        video_id = validate_entity_id(video_id, label="ролика")
        return self._entity_directory(
            channel / "videos", video_id, metadata_name="video.json", field="id"
        )

    def video_summary(self, directory: Path) -> dict[str, Any]:
        data = _read_json(directory / "video.json")
        video_id = validate_entity_id(str(data["id"]), label="ролика")
        channel_id = validate_entity_id(str(data["channel_id"]), label="канала")
        runs = self._run_directories(directory)
        latest = self._latest_run_summary(runs)
        script = self._script_summary(directory, data)
        return {
            "id": video_id,
            "channel_id": channel_id,
            "working_title": _name(data["working_title"], label="Название ролика"),
            "publish_title": data.get("publish_title"),
            "folder_name": directory.name,
            "path": str(directory),
            "created_at": str(data["created_at"]),
            "updated_at": str(data.get("updated_at", data["created_at"])),
            "run_count": len(runs),
            "latest_run": latest,
            "status": latest["status"] if latest else "draft",
            **script,
        }

    def video_details(self, channel_id: str, video_id: str) -> dict[str, Any]:
        directory = self.video_directory(channel_id, video_id)
        result = self.video_summary(directory)
        result["runs"] = [self._run_summary(path) for path in self._run_directories(directory)]
        return result

    def runs_directory(self, channel_id: str, video_id: str) -> Path:
        return self.video_directory(channel_id, video_id) / "runs"

    def run_directory(self, channel_id: str, video_id: str, run_id: str) -> Path:
        root = self.runs_directory(channel_id, video_id).resolve()
        directory = (root / validate_run_id(run_id)).resolve()
        if not directory.is_relative_to(root) or not directory.is_dir():
            raise FileNotFoundError("Запуск не найден")
        return directory

    def script_path(self, channel_id: str, video_id: str) -> Path:
        return self.video_directory(channel_id, video_id) / "script.md"

    def save_script(
        self,
        channel_id: str,
        video_id: str,
        text: str,
        *,
        status: str,
    ) -> dict[str, Any]:
        if status not in SCRIPT_STATUSES - {"planning"}:
            raise ValueError("Некорректный статус сценария")
        if not isinstance(text, str) or not text.strip() or "\0" in text:
            raise ValueError("Сценарий не может быть пустым")
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_SCRIPT_BYTES:
            raise ValueError("Сценарий слишком большой: максимум 128 КБ")
        directory = self.video_directory(channel_id, video_id)
        path = directory / "script.md"
        temporary = path.with_suffix(".md.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(path)
        self._set_script_metadata(directory, status=status, content=encoded)
        return self.video_summary(directory)

    def set_script_status(
        self, channel_id: str, video_id: str, status: str
    ) -> dict[str, Any]:
        if status not in SCRIPT_STATUSES:
            raise ValueError("Некорректный статус сценария")
        directory = self.video_directory(channel_id, video_id)
        path = directory / "script.md"
        if path.is_file() and path.stat().st_size > MAX_SCRIPT_BYTES:
            raise ValueError("Сценарий слишком большой: максимум 128 КБ")
        content = path.read_bytes() if path.is_file() else b""
        if status in {"drafting", "review", "ready"} and not content.strip():
            raise ValueError("Нельзя пометить пустой сценарий как готовый")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Файл script.md должен быть в UTF-8") from error
        self._set_script_metadata(directory, status=status, content=content)
        return self.video_summary(directory)

    def read_ready_script(self, channel_id: str, video_id: str) -> bytes:
        directory = self.video_directory(channel_id, video_id)
        data = _read_json(directory / "video.json")
        if data.get("script_status") != "ready":
            raise ValueError("Сценарий ещё не прошёл итоговую проверку")
        path = directory / "script.md"
        if not path.is_file():
            raise ValueError("В папке ролика нет файла script.md")
        if path.stat().st_size <= 0 or path.stat().st_size > MAX_SCRIPT_BYTES:
            raise ValueError("Файл script.md пуст или превышает 128 КБ")
        content = path.read_bytes()
        if not content.strip():
            raise ValueError("Файл script.md пуст или превышает 128 КБ")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Файл script.md должен быть в UTF-8") from error
        expected_hash = data.get("script_sha256")
        if not isinstance(expected_hash, str) or not hmac.compare_digest(
            hashlib.sha256(content).hexdigest(), expected_hash
        ):
            raise ValueError(
                "Сценарий изменился после итоговой проверки; "
                "пометьте его как ready повторно"
            )
        return content

    def touch_video(self, channel_id: str, video_id: str) -> None:
        video = self.video_directory(channel_id, video_id)
        data = _read_json(video / "video.json")
        data["updated_at"] = utc_now()
        _write_json(video / "video.json", data)
        self._touch_channel(self.channel_directory(channel_id))

    @staticmethod
    def _entity_directory(
        parent: Path, entity_id: str, *, metadata_name: str, field: str
    ) -> Path:
        matches: list[Path] = []
        for metadata in parent.glob(f"*/{metadata_name}"):
            try:
                data = _read_json(metadata)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if data.get(field) == entity_id:
                matches.append(metadata.parent.resolve())
        if len(matches) != 1:
            raise FileNotFoundError("Объект workspace не найден")
        directory = matches[0]
        if not directory.is_relative_to(parent.resolve()):
            raise FileNotFoundError("Объект workspace не найден")
        return directory

    @staticmethod
    def _video_metadata_paths(channel: Path) -> list[Path]:
        root = channel / "videos"
        return list(root.glob("*/video.json")) if root.is_dir() else []

    @staticmethod
    def _run_directories(video: Path) -> list[Path]:
        root = video / "runs"
        if not root.is_dir():
            return []
        paths = [path.parent for path in root.glob("*/manifest.json")]
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)

    @staticmethod
    def _run_summary(directory: Path) -> dict[str, Any]:
        from ru_en_dubber.models import Manifest

        manifest = Manifest.load(directory / "manifest.json")
        return {
            "id": directory.name,
            "status": manifest.job.status.value,
            "created_at": manifest.job.created_at.isoformat(),
            "updated_at": manifest.job.updated_at.isoformat(),
            "has_export": "premiere_export" in manifest.artifacts,
            "segments": len(manifest.segments),
            "error": manifest.job.error,
        }

    @classmethod
    def _latest_run_summary(cls, runs: list[Path]) -> dict[str, Any] | None:
        if not runs:
            return None
        try:
            return cls._run_summary(runs[0])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {
                "id": runs[0].name,
                "status": "failed",
                "created_at": datetime.fromtimestamp(
                    runs[0].stat().st_mtime, tz=UTC
                ).isoformat(),
                "updated_at": datetime.fromtimestamp(
                    runs[0].stat().st_mtime, tz=UTC
                ).isoformat(),
                "has_export": False,
                "segments": 0,
                "error": "Манифест запуска повреждён",
            }

    @staticmethod
    def _named_directories(root: Path) -> list[dict[str, str]]:
        if not root.is_dir():
            return []
        return [
            {"folder_name": path.name, "path": str(path)}
            for path in sorted(root.iterdir(), key=lambda item: item.name.lower())
            if path.is_dir()
        ]

    @staticmethod
    def _touch_channel(channel: Path) -> None:
        data = _read_json(channel / "channel.json")
        data["updated_at"] = utc_now()
        _write_json(channel / "channel.json", data)

    @staticmethod
    def _script_summary(directory: Path, data: dict[str, Any]) -> dict[str, Any]:
        status = str(data.get("script_status", "planning"))
        if status not in SCRIPT_STATUSES:
            raise ValueError("Некорректный статус сценария в video.json")
        script_file = data.get("script_file", "script.md")
        if script_file != "script.md":
            raise ValueError("Некорректный путь сценария в video.json")
        path = directory / script_file
        has_script = path.is_file() and path.stat().st_size > 0
        safe_size = has_script and path.stat().st_size <= MAX_SCRIPT_BYTES
        current_hash = (
            hashlib.sha256(path.read_bytes()).hexdigest() if safe_size else None
        )
        saved_hash = data.get("script_sha256")
        script_is_current = (
            has_script
            and isinstance(saved_hash, str)
            and hmac.compare_digest(current_hash or "", saved_hash)
        )
        return {
            "has_script": has_script,
            "script_status": status,
            "script_file": script_file,
            "script_updated_at": data.get("script_updated_at"),
            "script_sha256": data.get("script_sha256"),
            "script_word_count": int(data.get("script_word_count") or 0),
            "script_is_current": script_is_current,
        }

    def _set_script_metadata(
        self, directory: Path, *, status: str, content: bytes
    ) -> None:
        data = _read_json(directory / "video.json")
        now = utc_now()
        data.update(
            {
                "script_file": "script.md",
                "script_status": status,
                "script_updated_at": now,
                "script_sha256": hashlib.sha256(content).hexdigest() if content else None,
                "script_word_count": len(content.decode("utf-8").split()) if content else 0,
                "updated_at": now,
            }
        )
        _write_json(directory / "video.json", data)
        self._touch_channel(directory.parent.parent)
