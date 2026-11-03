from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class Job:
    """Portable job metadata stored inside the manifest.

    Artifact paths are relative to the job directory so a job can be moved to
    another machine without rewriting its manifest.
    """

    id: str
    source_file: str = "source.mp4"
    source_url: str | None = None
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.id or self.id.isspace():
            raise ValueError("job id must not be empty")
        if not self.source_file:
            raise ValueError("source_file must not be empty")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("job timestamps must be timezone-aware")

    def touch(self, *, status: JobStatus | None = None) -> None:
        if status is not None:
            self.status = status
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_file": self.source_file,
            "source_url": self.source_url,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        return cls(
            id=str(data["id"]),
            source_file=str(data.get("source_file", "source.mp4")),
            source_url=data.get("source_url"),
            status=JobStatus(data.get("status", JobStatus.PENDING.value)),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            error=data.get("error"),
        )
