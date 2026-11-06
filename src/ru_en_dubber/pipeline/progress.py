from __future__ import annotations

import sys
from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule


STAGE_LABELS = {
    "content_analysis": "Content and scene analysis",
    "animation_plan": "Animation planning",
    "tts": "Speech synthesis",
    "speech_postprocess": "Voice processing and loudness",
    "timing_alignment": "Line synchronization",
    "script_timing": "Timeline from ready narration",
    "narration_export": "Assembling the master audio",
    "subtitle_export": "SRT subtitle export",
    "visual_rebuild": "Building the visual track",
    "avatar_overlay": "Talking avatar",
    "music_bed": "Background music",
    "premiere_export": "Premiere editing package",
    "youtube_download": "YouTube download",
    "audio_extraction": "Audio extraction",
    "source_separation": "Voice and background separation",
    "transcription": "Russian speech recognition",
    "translation": "Translation and adaptation RU → EN",
    "fake_transcription": "Test transcription",
    "fake_translation": "Test translation",
}


class PipelineProgress(Protocol):
    def start_pipeline(self, job_id: str, stage_count: int) -> None: ...

    def start_stage(self, name: str, index: int, stage_count: int) -> None: ...

    def update_stage(self, fraction: float | None, message: str | None = None) -> None: ...

    def complete_stage(self, name: str) -> None: ...

    def fail_stage(self, name: str, error: Exception) -> None: ...

    def finish_pipeline(self, success: bool) -> None: ...


class NullPipelineProgress:
    def start_pipeline(self, job_id: str, stage_count: int) -> None:
        pass

    def start_stage(self, name: str, index: int, stage_count: int) -> None:
        pass

    def update_stage(self, fraction: float | None, message: str | None = None) -> None:
        pass

    def complete_stage(self, name: str) -> None:
        pass

    def fail_stage(self, name: str, error: Exception) -> None:
        pass

    def finish_pipeline(self, success: bool) -> None:
        pass


class ConsolePipelineProgress:
    """Rich live display for the whole pipeline and its current stage."""

    def __init__(self, *, console: Console | None = None) -> None:
        if console is None:
            self._enable_utf8_console()
        self._console = console or Console()
        self._progress: Progress | None = None
        self._overall_task: TaskID | None = None
        self._stage_task: TaskID | None = None
        self._stage_index = 0
        self._stage_count = 0
        self._completed_stages = 0

    def start_pipeline(self, job_id: str, stage_count: int) -> None:
        self._stage_count = stage_count
        self._completed_stages = 0
        self._console.print(Rule("[bold cyan]RU → EN Dubber[/]", style="cyan"))
        self._console.print(f"[dim]Job:[/] [bold]{job_id}[/]\n")
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("{task.description}", justify="left"),
            BarColumn(
                bar_width=None,
                style="grey35",
                complete_style="cyan",
                finished_style="green",
                pulse_style="bright_cyan",
            ),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[status]}[/]", justify="left"),
            TimeElapsedColumn(),
            console=self._console,
            refresh_per_second=10,
            expand=True,
        )
        self._progress.start()
        self._overall_task = self._progress.add_task(
            "[bold]Whole pipeline[/]",
            total=stage_count,
            status=f"0 of {stage_count}",
        )

    def start_stage(self, name: str, index: int, stage_count: int) -> None:
        progress = self._require_progress()
        self._stage_index = index
        self._stage_count = stage_count
        label = STAGE_LABELS.get(name, name.replace("_", " "))
        self._stage_task = progress.add_task(
            f"[cyan]  {index}/{stage_count}[/] {label}",
            total=None,
            completed=0,
            status="Preparing…",
        )

    def update_stage(self, fraction: float | None, message: str | None = None) -> None:
        if self._progress is None or self._stage_task is None:
            return
        fields: dict[str, object] = {}
        if fraction is None:
            fields["total"] = None
            fields["completed"] = 0
        else:
            fields["total"] = 100
            fields["completed"] = max(0.0, min(1.0, fraction)) * 100
        if message is not None:
            fields["status"] = message
        self._progress.update(self._stage_task, refresh=True, **fields)
        if self._overall_task is not None and fraction is not None:
            self._progress.update(
                self._overall_task,
                completed=self._completed_stages + max(0.0, min(1.0, fraction)),
                refresh=True,
            )

    def complete_stage(self, name: str) -> None:
        if self._progress is None or self._stage_task is None:
            return
        label = STAGE_LABELS.get(name, name.replace("_", " "))
        self._progress.update(
            self._stage_task,
            total=100,
            completed=100,
            description=f"[green]✓ {self._stage_index}/{self._stage_count}[/] {label}",
            status="Done",
            refresh=True,
        )
        if self._overall_task is not None:
            self._completed_stages += 1
            self._progress.update(
                self._overall_task,
                completed=self._completed_stages,
                status=f"{self._completed_stages} of {self._stage_count}",
                refresh=True,
            )

    def fail_stage(self, name: str, error: Exception) -> None:
        if self._progress is None or self._stage_task is None:
            return
        label = STAGE_LABELS.get(name, name.replace("_", " "))
        self._progress.update(
            self._stage_task,
            description=f"[red]✗ {self._stage_index}/{self._stage_count}[/] {label}",
            status=f"Error: {type(error).__name__}",
            refresh=True,
        )

    def finish_pipeline(self, success: bool) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
        if success:
            self._console.print("\n[bold green]✓ Pipeline finished successfully[/]")
        else:
            self._console.print("\n[bold red]✗ Pipeline stopped due to an error[/]")

    def _require_progress(self) -> Progress:
        if self._progress is None:
            raise RuntimeError("progress display has not been started")
        return self._progress

    @staticmethod
    def _enable_utf8_console() -> None:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is None:
                continue
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
