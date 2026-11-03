from __future__ import annotations

import json
import math
import time
from pathlib import Path

from ru_en_dubber.pipeline.progress import STAGE_LABELS


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class FileProgress:
    """Atomic snapshots: real stage work, never elapsed-time simulated percentages."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = dict(status="starting", stages=[], overall=0.0, started_at=time.time())
        self.completed = 0
        self.count = 0
        self.current: dict | None = None
        self._save()

    def _save(self) -> None:
        self.state["updated_at"] = time.time()
        write_json(self.path, self.state)

    def start_pipeline(self, job_id: str, stage_count: int) -> None:
        self.count = stage_count
        self.state.update(job_id=job_id, status="running", stage_count=stage_count)
        self._save()

    def start_stage(self, name: str, index: int, stage_count: int) -> None:
        self.current = dict(name=name, label=STAGE_LABELS.get(name, name), index=index,
                            fraction=None, status="running", message="Подготовка…", started_at=time.time())
        self.state["stages"].append(self.current)
        self._save()

    def update_stage(self, fraction: float | None, message: str | None = None) -> None:
        if self.current is None:
            return
        if fraction is not None and math.isfinite(fraction):
            # Some stages switch substeps and report a smaller local percentage.
            self.current["fraction"] = max(self.current.get("fraction") or 0, min(1.0, fraction))
        elif fraction is None:
            self.current["fraction"] = None
        if message is not None:
            self.current["message"] = message
        measured = (self.completed + (self.current["fraction"] or 0)) / max(1, self.count)
        self.state["overall"] = max(self.state["overall"], min(0.999, measured))
        self._save()

    def complete_stage(self, name: str) -> None:
        if self.current is not None:
            self.current.update(fraction=1.0, status="completed", finished_at=time.time(), message="Готово")
        self.completed += 1
        self.state["overall"] = min(0.999, self.completed / max(1, self.count))
        self._save()

    def fail_stage(self, name: str, error: Exception) -> None:
        if self.current is not None:
            self.current.update(status="failed", message=str(error), finished_at=time.time())
        self.state["error"] = f"{type(error).__name__}: {error}"
        self._save()

    def finish_pipeline(self, success: bool) -> None:
        self.state.update(status="completed" if success else "failed", finished_at=time.time())
        if success:
            self.state["overall"] = 1.0
        self._save()
