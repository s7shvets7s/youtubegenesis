from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from ru_en_dubber.models import Job, JobStatus, Manifest
from ru_en_dubber.pipeline.progress import NullPipelineProgress, PipelineProgress
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.security import resolve_job_directory


MANIFEST_FILENAME = "manifest.json"


def create_job(source: Path, jobs_dir: Path, job_id: str | None = None) -> tuple[Manifest, Path]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input video not found: {source}")

    resolved_job_id = job_id or uuid4().hex[:12]
    job_dir = resolve_job_directory(jobs_dir, resolved_job_id)
    job_dir.mkdir(parents=True, exist_ok=False)

    source_name = f"source{source.suffix.lower() or '.mp4'}"
    shutil.copy2(source, job_dir / source_name)
    manifest = Manifest(
        job=Job(id=resolved_job_id, source_file=source_name),
        artifacts={"source_video": source_name},
        metadata={"input": {"type": "file", "original_path": str(source)}},
    )
    manifest.save(job_dir / MANIFEST_FILENAME)
    return manifest, job_dir


def create_job_from_url(
    source_url: str, jobs_dir: Path, job_id: str | None = None
) -> tuple[Manifest, Path]:
    from ru_en_dubber.ingest.youtube import is_youtube_url

    if not is_youtube_url(source_url):
        raise ValueError("only YouTube video URLs are currently supported")

    resolved_job_id = job_id or uuid4().hex[:12]
    job_dir = resolve_job_directory(jobs_dir, resolved_job_id)
    job_dir.mkdir(parents=True, exist_ok=False)
    manifest = Manifest(
        job=Job(
            id=resolved_job_id,
            source_file="source.mp4",
            source_url=source_url,
        ),
        metadata={"input": {"type": "youtube", "url": source_url}},
    )
    manifest.save(job_dir / MANIFEST_FILENAME)
    return manifest, job_dir


class PipelineRunner:
    def __init__(
        self,
        stages: list[PipelineStage],
        *,
        progress: PipelineProgress | None = None,
    ) -> None:
        if not stages:
            raise ValueError("pipeline must contain at least one stage")
        names = [stage.name for stage in stages]
        if len(names) != len(set(names)):
            raise ValueError("pipeline stage names must be unique")
        self._stages = tuple(stages)
        self._progress = progress or NullPipelineProgress()

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        manifest.job.touch(status=JobStatus.RUNNING)
        manifest.save(job_dir / MANIFEST_FILENAME)
        self._progress.start_pipeline(manifest.job.id, len(self._stages))
        current_stage: PipelineStage | None = None

        try:
            for index, stage in enumerate(self._stages, start=1):
                current_stage = stage
                stage.set_progress_callback(self._progress.update_stage)
                self._progress.start_stage(stage.name, index, len(self._stages))
                manifest = stage.run(manifest, job_dir)
                manifest.mark_stage_completed(stage.name)
                manifest.job.touch()
                manifest.save(job_dir / MANIFEST_FILENAME)
                self._progress.complete_stage(stage.name)
                stage.set_progress_callback(None)
        except Exception as error:
            if current_stage is not None:
                self._progress.fail_stage(current_stage.name, error)
                current_stage.set_progress_callback(None)
            manifest.job.error = f"{type(error).__name__}: {error}"
            manifest.job.touch(status=JobStatus.FAILED)
            manifest.save(job_dir / MANIFEST_FILENAME)
            self._progress.finish_pipeline(False)
            raise

        manifest.job.error = None
        manifest.job.touch(status=JobStatus.COMPLETED)
        manifest.save(job_dir / MANIFEST_FILENAME)
        self._progress.finish_pipeline(True)
        return manifest
