from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ru_en_dubber.models.job import Job
from ru_en_dubber.models.segment import Segment


@dataclass(slots=True)
class Manifest:
    """Versioned source of truth for a dubbing job."""

    job: Job
    segments: list[Segment] = field(default_factory=list)
    completed_stages: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"unsupported manifest schema: {self.schema_version}")
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("segment ids must be unique")
        if len(self.completed_stages) != len(set(self.completed_stages)):
            raise ValueError("completed stage names must be unique")

    def mark_stage_completed(self, stage_name: str) -> None:
        if stage_name not in self.completed_stages:
            self.completed_stages.append(stage_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job": self.job.to_dict(),
            "segments": [segment.to_dict() for segment in self.segments],
            "completed_stages": list(self.completed_stages),
            "artifacts": dict(self.artifacts),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            job=Job.from_dict(data["job"]),
            segments=[Segment.from_dict(item) for item in data.get("segments", [])],
            completed_stages=list(data.get("completed_stages", [])),
            artifacts=dict(data.get("artifacts", {})),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def load(cls, path: Path) -> Manifest:
        with path.open("r", encoding="utf-8") as file:
            return cls.from_dict(json.load(file))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(
                self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)

