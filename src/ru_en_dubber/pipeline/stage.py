from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ru_en_dubber.models import Manifest


class PipelineStage(ABC):
    """Contract implemented by every replaceable pipeline component."""

    _progress_callback: Callable[[float | None, str | None], None] | None = None

    def set_progress_callback(
        self,
        callback: Callable[[float | None, str | None], None] | None,
    ) -> None:
        self._progress_callback = callback

    def report_progress(
        self, fraction: float | None, message: str | None = None
    ) -> None:
        if self._progress_callback is not None:
            self._progress_callback(fraction, message)

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable name persisted in manifest.completed_stages."""

    @abstractmethod
    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        """Run the stage and return the updated manifest."""
