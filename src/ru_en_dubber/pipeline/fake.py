from __future__ import annotations

from pathlib import Path

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage


class FakeTranscriptionStage(PipelineStage):
    """Deterministic stand-in for WhisperX while the contract is stabilized."""

    @property
    def name(self) -> str:
        return "fake_transcription"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        source_path = job_dir / manifest.job.source_file
        if not source_path.is_file():
            raise FileNotFoundError(f"source video not found: {source_path}")

        manifest.segments = [
            Segment(
                id=1,
                start=0.0,
                end=4.8,
                ru="Короче говоря, после этого всё начало разваливаться.",
            ),
            Segment(
                id=2,
                start=5.2,
                end=9.6,
                ru="Но никто не ожидал такого результата.",
            ),
        ]
        manifest.metadata["transcription_provider"] = "fake"
        return manifest


class FakeTranslationStage(PipelineStage):
    """Deterministic stand-in for duration-aware RU-to-EN adaptation."""

    _translations = {
        "Короче говоря, после этого всё начало разваливаться.": (
            "After that, everything started falling apart."
        ),
        "Но никто не ожидал такого результата.": (
            "But nobody expected that result."
        ),
    }

    @property
    def name(self) -> str:
        return "fake_translation"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if not manifest.segments:
            raise ValueError("translation requires transcript segments")

        for segment in manifest.segments:
            segment.en = self._translations.get(segment.ru, f"[EN] {segment.ru}")
        manifest.metadata["translation_provider"] = "fake"
        return manifest

