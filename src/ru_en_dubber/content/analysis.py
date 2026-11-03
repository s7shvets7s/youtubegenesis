"""One content-analysis stage for advertising, structure and hybrid visual decisions."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.prompts import get_prompt, render_prompt
from ru_en_dubber.security import MediaSecurityError, resolve_job_path
from ru_en_dubber.translation.qwen import QwenTranslationStage, TranslationExecutionError
from ru_en_dubber.visuals.models import INTENTS, fingerprint
from ru_en_dubber.visuals.transport import write_json
from .text_localization import (
    LOCALIZABLE_TEXT_ROLES,
    contains_cyrillic,
    normalize_text_regions,
    text_cleanup_status,
)
from .visual import SourceVisualAnalyzer


class ContentAnalysisError(RuntimeError):
    """Raised when the common content plan cannot be produced safely."""


ANIMATION_INTENTS = {"number", "map", "process", "abstract"}
CONTENT_ANALYSIS_VERSION = "content-analysis-v5-static-text-and-visual-integrations"
AUTHOR_CONFIDENCE_THRESHOLD = 0.90


def removed_before(value: float, ranges: list[dict[str, Any]]) -> float:
    return sum(max(0.0, min(value, float(item["end"])) - float(item["start"]))
               for item in ranges)


def subtract_ranges(start: float, end: float, ranges: list[dict[str, Any]]) -> list[tuple[float, float]]:
    pieces: list[tuple[float, float]] = []
    cursor = start
    for item in sorted(ranges, key=lambda value: float(value["start"])):
        cut_start, cut_end = float(item["start"]), float(item["end"])
        if cut_end <= cursor or cut_start >= end:
            continue
        if cut_start > cursor:
            pieces.append((cursor, min(cut_start, end)))
        cursor = max(cursor, cut_end)
        if cursor >= end:
            break
    if cursor < end:
        pieces.append((cursor, end))
    return [(left, right) for left, right in pieces if right - left >= 0.04]


def merge_ad_ranges(
    ranges: list[dict[str, Any]], *, maximum_gap_seconds: float = 1.0
) -> list[dict[str, Any]]:
    """Merge transcript and visual evidence into complete integration intervals."""

    ordered = sorted(
        (
            dict(item)
            for item in ranges
            if float(item.get("end") or 0.0) > float(item.get("start") or 0.0)
        ),
        key=lambda item: (float(item["start"]), float(item["end"])),
    )
    merged: list[dict[str, Any]] = []
    for item in ordered:
        item["start"] = float(item["start"])
        item["end"] = float(item["end"])
        if merged and item["start"] <= float(merged[-1]["end"]) + maximum_gap_seconds:
            current = merged[-1]
            current["start"] = min(float(current["start"]), item["start"])
            current["end"] = max(float(current["end"]), item["end"])
            current["confidence"] = max(
                float(current.get("confidence") or 0.0),
                float(item.get("confidence") or 0.0),
            )
            for key, chooser in (
                ("start_segment_id", min),
                ("end_segment_id", max),
            ):
                values = [
                    int(value)
                    for value in (current.get(key), item.get(key))
                    if value is not None
                ]
                if values:
                    current[key] = chooser(values)
            brands = {
                str(value).strip()
                for value in (current.get("brand"), item.get("brand"))
                if str(value or "").strip()
            }
            current["brand"] = ", ".join(sorted(brands))
            reasons = [
                str(value).strip()
                for value in (current.get("reason"), item.get("reason"))
                if str(value or "").strip()
            ]
            current["reason"] = " | ".join(dict.fromkeys(reasons))[:1000]
            continue
        merged.append(item)
    return merged


def visual_integration_ranges(
    shots: list[dict[str, Any]], confidence_threshold: float = 0.85
) -> list[dict[str, Any]]:
    """Convert visually confirmed QR/handle/CTA shots into removable ranges."""

    promotional = re.compile(
        r"qr\s*code|qr.?код|куар.?код|telegram|телеграм|t\.me/|"
        r"@[a-z0-9_.]{3,}|subscribe|подпис|follow|ссылк.{0,30}описани|"
        r"pinned\s+post|закрепл[её]нн.{0,20}пост|promo|промокод|"
        r"giveaway|розыгрыш|merch|мерч|donat|донат",
        flags=re.IGNORECASE,
    )
    candidates: list[dict[str, Any]] = []
    for shot in shots:
        regions = [
            item for item in (shot.get("text_regions") or []) if isinstance(item, dict)
        ]
        detected = " ".join(str(item.get("detected_text") or "") for item in regions)
        evidence = " ".join(
            [detected, str(shot.get("detected_text") or ""), str(shot.get("reason") or "")]
        )
        explicit_role = str(shot.get("text_role") or "") == "advertising_overlay"
        explicit_source = str(shot.get("source_type") or "") == "advertisement"
        if not (explicit_role or explicit_source or promotional.search(evidence)):
            continue
        confidence = max(
            [float(shot.get("confidence") or 0.0)]
            + [float(item.get("confidence") or 0.0) for item in regions]
        )
        if confidence < confidence_threshold:
            continue
        try:
            start, end = float(shot["start"]), float(shot["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        handles = re.findall(r"@[A-Za-z0-9_.]{3,}|(?:https?://|t\.me/)[^\s]+", evidence)
        candidates.append({
            "start": start,
            "end": end,
            "confidence": round(confidence, 3),
            "kind": "self_promo",
            "brand": handles[0] if handles else "",
            "reason": "visual integration: " + str(shot.get("reason") or evidence).strip()[:500],
            "source": "qwen_vl",
        })
    return merge_ad_ranges(candidates)


def action_for(mode: str, source_type: str, contains_author: bool, intent: str,
               contains_text: bool = False, *, text_role: str = "none",
               visual_confidence: float = 0.0, detected_text: str = "",
               translated_text: str = "", text_cleanup: str = "not_needed") -> str:
    if mode == "original":
        return "keep"
    if text_cleanup == "text_cleanup_review":
        return "keep"
    if (text_role in LOCALIZABLE_TEXT_ROLES
            and text_cleanup == "static_ready"
            and contains_cyrillic(detected_text)
            and translated_text.strip()
            and visual_confidence >= 0.75):
        return "localize_text"
    # A failed or inconclusive visual pass must never become an implicit
    # replacement. This also protects rebuild mode from destructive guesses.
    if source_type in {"unknown", "uncertain"}:
        return "keep"
    if mode == "rebuild":
        return "replace_animation" if intent in ANIMATION_INTENTS else "replace_media"
    if (contains_author and source_type == "author"
            and visual_confidence >= AUTHOR_CONFIDENCE_THRESHOLD):
        return "replace_media"
    if mode == "hybrid" and intent in ANIMATION_INTENTS:
        return "replace_animation"
    return "keep"


def validate_blocks(payload: object, segments: list[Segment]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) - {"content_blocks", "video_summary"}:
        raise ValueError("invalid content analysis object")
    items = payload.get("content_blocks")
    if not isinstance(items, list):
        raise ValueError("content analysis has no block list")
    by_id = {segment.id: segment for segment in segments}
    result: list[dict[str, Any]] = []
    allowed = {"start_id", "end_id", "topic", "visual_intent", "subject", "reason"}
    for raw in items:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("invalid content block")
        start_id, end_id = int(raw["start_id"]), int(raw["end_id"])
        if start_id not in by_id or end_id not in by_id or start_id > end_id:
            raise ValueError("content block references unknown segments")
        intent = str(raw["visual_intent"])
        if intent not in INTENTS:
            intent = "abstract"
        topic, subject = str(raw["topic"]).strip(), str(raw["subject"]).strip()
        if not topic or not subject or len(topic) > 300 or len(subject) > 500:
            raise ValueError("invalid content block text")
        covered = [segment for segment in segments if start_id <= segment.id <= end_id]
        if not covered:
            raise ValueError("empty content block")
        result.append({
            "start_id": start_id,
            "end_id": end_id,
            "start": covered[0].start,
            "end": covered[-1].end,
            "segment_ids": [segment.id for segment in covered],
            "topic": topic,
            "visual_intent": intent,
            "subject": subject,
            "reason": str(raw.get("reason") or "")[:1000],
        })
    result.sort(key=lambda item: (item["start"], item["end"]))
    covered_ids = [segment_id for item in result for segment_id in item["segment_ids"]]
    if covered_ids != [segment.id for segment in sorted(segments, key=lambda item: (item.start, item.id))]:
        raise ValueError("content blocks must cover each transcript segment exactly once")
    return result


def fallback_blocks(segments: list[Segment], target_seconds: float = 18.0) -> list[dict[str, Any]]:
    groups: list[list[Segment]] = []
    current: list[Segment] = []
    for segment in segments:
        if current and (segment.end - current[0].start > target_seconds or segment.start - current[-1].end > 4):
            groups.append(current)
            current = []
        current.append(segment)
    if current:
        groups.append(current)
    return [{
        "start_id": group[0].id, "end_id": group[-1].id,
        "start": group[0].start, "end": group[-1].end,
        "segment_ids": [segment.id for segment in group],
        "topic": "Unclassified content", "visual_intent": "original",
        "subject": " ".join((segment.en or segment.ru) for segment in group)[:500],
        "reason": "safe fallback after unavailable semantic analysis",
    } for group in groups]


def visual_for_interval(start: float, end: float, shots: list[dict[str, Any]]) -> dict[str, Any]:
    overlapping = []
    for shot in shots:
        overlap = max(0.0, min(end, float(shot["end"])) - max(start, float(shot["start"])))
        if overlap:
            overlapping.append((overlap, shot))
    if not overlapping:
        return {"source_type": "unknown", "contains_author": False, "contains_text": False,
                "text_role": "none", "detected_text": "", "translated_text": "",
                "text_regions": [], "text_cleanup_status": "not_needed",
                "video_type": "uncertain",
                "text_confidence": 0.0, "visual_confidence": 0.0,
                "requires_review": True, "source_shot_ids": []}
    author_overlap = sum(overlap for overlap, shot in overlapping if shot.get("contains_author"))
    total = sum(overlap for overlap, _ in overlapping)
    author = author_overlap / total >= 0.5
    text_overlap = sum(overlap for overlap, shot in overlapping if shot.get("contains_text"))
    contains_text = text_overlap / total >= 0.35
    confidence = max(float(shot.get("confidence") or 0.0) for _, shot in overlapping if shot.get("contains_author")) if author else max(float(shot.get("confidence") or 0.0) for _, shot in overlapping)
    person_unknown = any(shot.get("source_type") == "person_unknown" for _, shot in overlapping)
    primary = max(overlapping, key=lambda item: item[0])[1]
    source_type = str(primary.get("source_type") or ("text_graphic" if contains_text else "unknown"))
    return {"source_type": ("author" if author else source_type if source_type != "author" else
                            ("person_unknown" if person_unknown else "unknown")),
            "contains_author": author,
            "contains_text": contains_text,
            "text_role": str(primary.get("text_role") or "none"),
            "detected_text": str(primary.get("detected_text") or ""),
            "translated_text": str(primary.get("translated_text") or ""),
            "text_regions": list(primary.get("text_regions") or []),
            "text_cleanup_status": str(primary.get("text_cleanup_status") or "not_needed"),
            "video_type": str(primary.get("video_type") or "uncertain"),
            "text_confidence": round(max(float(shot.get("text_confidence") or 0.0)
                                         for _, shot in overlapping), 3),
            "visual_confidence": round(confidence, 3),
            "requires_review": any(bool(shot["requires_review"]) for _, shot in overlapping),
            "source_shot_ids": [int(shot["index"]) for _, shot in overlapping]}


def split_at_shots(start: float, end: float, shots: list[dict[str, Any]]) -> list[tuple[float, float]]:
    boundaries = {start, end}
    for shot in shots:
        shot_start, shot_end = float(shot["start"]), float(shot["end"])
        if start < shot_start < end:
            boundaries.add(shot_start)
        if start < shot_end < end:
            boundaries.add(shot_end)
    ordered = sorted(boundaries)
    return [(left, right) for left, right in zip(ordered, ordered[1:]) if right - left >= 0.04]


class ContentAnalysisStage(PipelineStage):
    def __init__(self, *, server: str, model: Path, python: str, source_mode: str,
                 vision_projector: Path | None = None,
                 ad_removal_mode: str = "off", ad_confidence_threshold: float = 0.85,
                 batch_size: int = 8, gpu_layers: int = 99, scene_threshold: float = 3.0,
                 author_policy: str = "confirmed_only") -> None:
        if source_mode not in {"original", "source-clean", "hybrid", "rebuild"}:
            raise ValueError("invalid source mode")
        if ad_removal_mode not in {"off", "mark", "remove"}:
            raise ValueError("invalid ad removal mode")
        if author_policy != "confirmed_only":
            raise ValueError("only confirmed_only author policy is supported")
        self.server, self.model, self.python = server, model, python
        self.vision_projector = vision_projector
        self.source_mode, self.ad_removal_mode = source_mode, ad_removal_mode
        self.ad_confidence_threshold, self.batch_size = ad_confidence_threshold, batch_size
        self.gpu_layers, self.scene_threshold = gpu_layers, scene_threshold
        self.author_policy = author_policy

    @property
    def name(self) -> str:
        return "content_analysis"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        if not manifest.segments or not all((segment.en or "").strip() for segment in manifest.segments):
            raise ContentAnalysisError("content analysis requires a complete English translation")
        source = resolve_job_path(job_dir, manifest.job.source_file, label="content-analysis source")
        analysis_dir = job_dir.resolve() / "content-analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        transcript = [{"id": s.id, "start": s.start, "end": s.end, "speaker": s.speaker,
                       "ru": s.ru, "en": s.en} for s in manifest.segments]
        projector_identity = None
        if self.vision_projector is not None:
            projector = self.vision_projector.expanduser().resolve()
            if projector.is_file():
                projector_identity = [projector.stat().st_size, projector.stat().st_mtime_ns]
        source_key = fingerprint([CONTENT_ANALYSIS_VERSION, self.source_mode, self.ad_removal_mode,
                                  self.ad_confidence_threshold, self.author_policy,
                                  projector_identity,
                                  source.stat().st_size, source.stat().st_mtime_ns, transcript])
        cached = analysis_dir / "content-analysis.json"
        if cached.is_file():
            try:
                payload = json.loads(cached.read_text(encoding="utf-8"))
                if source_key in {payload.get("fingerprint"), payload.get("output_fingerprint")} and (analysis_dir / "episode-plan.json").is_file():
                    if isinstance(payload.get("output_segments"), list):
                        manifest.segments = [Segment.from_dict(item) for item in payload["output_segments"]]
                    manifest.artifacts["content_analysis"] = cached.relative_to(job_dir).as_posix()
                    manifest.artifacts["episode_plan"] = (analysis_dir / "episode-plan.json").relative_to(job_dir).as_posix()
                    manifest.artifacts["ad_removal"] = "ad-removal.json"
                    manifest.metadata["content_analysis"] = payload["summary"]
                    manifest.metadata["ad_removal"] = payload["ad_analysis"]
                    return manifest
            except (OSError, ValueError, KeyError):
                pass
        self.report_progress(None, "Поиск монтажных границ…")
        detection = self._detect_shots(source, analysis_dir / "shot-detection.json")
        raw_shots = detection.get("scenes")
        if not isinstance(raw_shots, list) or not raw_shots:
            raise ContentAnalysisError("scene detector returned no shots")
        sampled_shots = SourceVisualAnalyzer().analyze(
            source, raw_shots, analysis_dir / "frames",
            progress=lambda done, total: self.report_progress(
                0.02 + 0.06 * done / max(1, total),
                f"Подготовлены кадры сцен: {done}/{total}",
            ),
        )

        runtime = QwenTranslationStage(server=self.server, model=self.model,
            vision_projector=self.vision_projector,
            batch_size=self.batch_size, gpu_layers=self.gpu_layers,
            context_size=32768,
            ad_removal_mode=self.ad_removal_mode,
            ad_confidence_threshold=self.ad_confidence_threshold,
            request_timeout=900.0)
        executable = runtime._resolve_executable(self.server)
        model = self.model.expanduser().resolve()
        if not model.is_file():
            raise ContentAnalysisError(f"content-analysis model not found: {model}")
        if self.vision_projector is None or not self.vision_projector.expanduser().is_file():
            raise ContentAnalysisError(
                "content analysis requires a Qwen-VL vision projector (--vision-projector)"
            )
        port = runtime._reserve_port()
        log_path = analysis_dir / "agent.log"
        process, log_file = runtime._start_server(executable, model, port, log_path)
        word_timings: dict[int, list[dict[str, Any]]] = {}
        semantic_error: str | None = None
        visual_error: str | None = None
        video_type = "uncertain"
        try:
            self.report_progress(None, "Загрузка модели Qwen-VL…")
            runtime._wait_until_ready(process, port, log_path)
            self.report_progress(0.10, "Единый анализ рекламы и структуры ролика…")
            if self.ad_removal_mode != "off":
                word_timings = runtime._load_word_timings(job_dir / str(manifest.artifacts.get("transcript") or "transcript.json"))
                ad_ranges = runtime._detect_ad_ranges(manifest.segments, port, word_timings=word_timings)
            else:
                ad_ranges = []
            try:
                blocks = self._analyze_structure(runtime, port, manifest.segments)
            except (TranslationExecutionError, OSError, ValueError, KeyError, TypeError) as error:
                semantic_error = type(error).__name__
                blocks = fallback_blocks(manifest.segments)
            try:
                self.report_progress(0.38, "Qwen-VL определяет тип ролика и содержимое сцен…")
                video_type = self._analyze_video_type(
                    runtime, port, sampled_shots, analysis_dir / "frames", manifest.segments
                )
                visual_shots = self._analyze_visual_shots(
                    runtime,
                    port,
                    sampled_shots,
                    analysis_dir / "frames",
                    manifest.segments,
                    video_type,
                )
            except (TranslationExecutionError, OSError, ValueError, KeyError, TypeError) as error:
                visual_error = type(error).__name__
                visual_shots = [self._safe_visual_fallback(item) for item in sampled_shots]
            if self.ad_removal_mode != "off":
                visual_ranges = visual_integration_ranges(
                    visual_shots, self.ad_confidence_threshold
                )
                ad_ranges = merge_ad_ranges([*ad_ranges, *visual_ranges])
            if self.ad_removal_mode == "remove" and ad_ranges:
                trimmed = runtime._remove_ad_speech(manifest.segments, ad_ranges, word_timings)
                if not trimmed:
                    raise ContentAnalysisError("ad removal would remove the complete transcript")
                missing = [segment for segment in trimmed if not segment.en]
                if missing:
                    translations = runtime._translate_segments(missing, port)
                    for segment in missing:
                        segment.en = translations[segment.id]
                manifest.segments = trimmed
        finally:
            runtime._stop_server(process)
            log_file.close()
        effective_cuts = ad_ranges if self.ad_removal_mode == "remove" else []
        episodes: list[dict[str, Any]] = []
        for block in blocks:
            for source_start, source_end in subtract_ranges(float(block["start"]), float(block["end"]), effective_cuts):
                for shot_start, shot_end in split_at_shots(source_start, source_end, visual_shots):
                    visual = visual_for_interval(shot_start, shot_end, visual_shots)
                    action = action_for(self.source_mode, visual["source_type"], visual["contains_author"],
                                        block["visual_intent"], visual["contains_text"],
                                        text_role=visual["text_role"],
                                        visual_confidence=visual["visual_confidence"],
                                        detected_text=visual["detected_text"],
                                        translated_text=visual["translated_text"],
                                        text_cleanup=visual["text_cleanup_status"])
                    episodes.append({
                        "index": len(episodes) + 1,
                        "source_start": round(shot_start, 6), "source_end": round(shot_end, 6),
                        "timeline_start": round(shot_start - removed_before(shot_start, effective_cuts), 6),
                        "timeline_end": round(shot_end - removed_before(shot_end, effective_cuts), 6),
                        "segment_ids": [sid for sid in block["segment_ids"] if any(s.id == sid and s.start < shot_end and s.end > shot_start for s in manifest.segments)],
                        "topic": block["topic"], "visual_intent": block["visual_intent"],
                        "subject": block["subject"], "action": action,
                        "author_policy": self.author_policy, **visual,
                    })
        # Transcript timestamps often leave silent title cards and outro graphics
        # uncovered. Analyze those source intervals too so hybrid mode cannot leak
        # an untranslated card (or a flash of the presenter) through an implicit gap.
        source_duration = max(float(shot["end"]) for shot in visual_shots)
        occupied = [{"start": item["source_start"], "end": item["source_end"]}
                    for item in episodes]
        allowed_ranges = subtract_ranges(0.0, source_duration, effective_cuts)
        for allowed_start, allowed_end in allowed_ranges:
            for gap_start, gap_end in subtract_ranges(allowed_start, allowed_end, occupied):
                for shot_start, shot_end in split_at_shots(gap_start, gap_end, visual_shots):
                    midpoint = (shot_start + shot_end) / 2
                    context = min(blocks, key=lambda item: (
                        0.0 if float(item["start"]) <= midpoint <= float(item["end"]) else
                        min(abs(midpoint - float(item["start"])), abs(midpoint - float(item["end"])))
                    ))
                    visual = visual_for_interval(shot_start, shot_end, visual_shots)
                    action = action_for(self.source_mode, visual["source_type"], visual["contains_author"],
                                        context["visual_intent"], visual["contains_text"],
                                        text_role=visual["text_role"],
                                        visual_confidence=visual["visual_confidence"],
                                        detected_text=visual["detected_text"],
                                        translated_text=visual["translated_text"],
                                        text_cleanup=visual["text_cleanup_status"])
                    segment_ids = [segment.id for segment in manifest.segments
                                   if segment.start < shot_end and segment.end > shot_start]
                    episodes.append({
                        "index": 0,
                        "source_start": round(shot_start, 6), "source_end": round(shot_end, 6),
                        "timeline_start": round(shot_start - removed_before(shot_start, effective_cuts), 6),
                        "timeline_end": round(shot_end - removed_before(shot_end, effective_cuts), 6),
                        "segment_ids": segment_ids,
                        "topic": context["topic"], "visual_intent": context["visual_intent"],
                        "subject": context["subject"], "action": action,
                        "author_policy": self.author_policy, "context_source": "visual_gap", **visual,
                    })
        episodes.sort(key=lambda item: (item["source_start"], item["source_end"]))
        for index, episode in enumerate(episodes, 1):
            episode["index"] = index
        ad_report = {"provider": "qwen", "mode": self.ad_removal_mode,
                     "confidence_threshold": self.ad_confidence_threshold, "ranges": ad_ranges}
        write_json(job_dir / "ad-removal.json", ad_report)
        episode_payload = {"version": 2, "timeline": "post-ad-removal", "source_mode": self.source_mode,
                           "author_policy": self.author_policy, "episodes": episodes}
        write_json(analysis_dir / "episode-plan.json", episode_payload)
        summary = {"source_mode": self.source_mode, "author_policy": self.author_policy,
                   "video_type": video_type,
                   "content_block_count": len(blocks), "episode_count": len(episodes),
                   "author_episode_count": sum(item["contains_author"] for item in episodes),
                   "text_episode_count": sum(item["contains_text"] for item in episodes),
                   "localized_text_count": sum(item["action"] == "localize_text" for item in episodes),
                   "static_text_ready_count": sum(
                       item.get("text_cleanup_status") == "static_ready"
                       for item in episodes
                   ),
                   "text_cleanup_review_count": sum(
                       item.get("text_cleanup_status") == "text_cleanup_review"
                       for item in episodes
                   ),
                   "replacement_count": sum(item["action"].startswith("replace") for item in episodes),
                   "review_count": sum(item["requires_review"] for item in episodes),
                   "semantic_fallback": semantic_error,
                   "visual_fallback": visual_error,
                   "visual_fallback_count": sum(
                       item.get("reason") == "qwen_vl_unavailable_keep_original"
                       for item in visual_shots
                   ),
                   "removed_ad_seconds": round(sum(float(r["end"]) - float(r["start"]) for r in effective_cuts), 3)}
        output_transcript = [{"id": s.id, "start": s.start, "end": s.end, "speaker": s.speaker,
                              "ru": s.ru, "en": s.en} for s in manifest.segments]
        output_fingerprint = fingerprint([CONTENT_ANALYSIS_VERSION, self.source_mode, self.ad_removal_mode,
                                          self.ad_confidence_threshold, self.author_policy,
                                          projector_identity,
                                          source.stat().st_size, source.stat().st_mtime_ns, output_transcript])
        write_json(cached, {"version": 1, "fingerprint": source_key,
                            "output_fingerprint": output_fingerprint, "transcript": transcript,
                            "output_segments": [segment.to_dict() for segment in manifest.segments],
                            "ad_analysis": ad_report, "content_blocks": blocks,
                            "source_shots": visual_shots, "summary": summary})
        manifest.artifacts.update(content_analysis=cached.relative_to(job_dir).as_posix(),
                                  episode_plan=(analysis_dir / "episode-plan.json").relative_to(job_dir).as_posix(),
                                  ad_removal="ad-removal.json")
        manifest.metadata["content_analysis"] = summary
        manifest.metadata["ad_removal"] = ad_report
        for downstream in ("animation_plan", "tts", "timing_alignment", "visual_rebuild", "avatar_overlay", "music_bed", "premiere_export"):
            if downstream in manifest.completed_stages:
                manifest.completed_stages.remove(downstream)
        for key in ("animation_plan", "tts_report", "tts_segments", "aligned_tts",
                    "avatar_video", "avatar_overlay_video", "avatar_base_video", "avatar_composite",
                    "music_report", "music_bed", "music_plan", "music_candidates", "music_credits",
                    "premiere_export", "premiere_xml"):
            manifest.artifacts.pop(key, None)
        manifest.metadata.pop("avatar", None)
        manifest.metadata.pop("music_bed", None)
        self.report_progress(0.99, f"Размечено эпизодов: {len(episodes)}; автор: {summary['author_episode_count']}")
        return manifest

    def _analyze_structure(self, runtime: QwenTranslationStage, port: int,
                           segments: list[Segment]) -> list[dict[str, Any]]:
        transcript = [{"id": s.id, "start": round(s.start, 3), "end": round(s.end, 3),
                       "text": s.en} for s in segments]
        prompt = render_prompt(
            "content_analysis.structure_user",
            required=("payload_json",),
            payload_json=json.dumps({"segments": transcript}, ensure_ascii=False),
        )
        content = runtime._chat_completion(port, prompt, temperature=0.0,
                                           max_tokens=min(8192, max(1200, len(segments) * 45)),
                                           system=get_prompt("content_analysis.structure_system"))
        payload = runtime._parse_json_payload(content, "content_blocks")
        return validate_blocks(payload, segments)

    @staticmethod
    def _image_part(path: Path) -> dict[str, Any]:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{encoded}"},
        }

    def _analyze_video_type(
        self,
        runtime: QwenTranslationStage,
        port: int,
        shots: list[dict[str, Any]],
        frames_dir: Path,
        segments: list[Segment],
    ) -> str:
        if not shots:
            return "uncertain"
        count = min(18, len(shots))
        indices = sorted({round(index * (len(shots) - 1) / max(1, count - 1))
                          for index in range(count)})
        transcript_excerpts = " | ".join(
            (segment.en or "") for segment in segments[::max(1, len(segments)//12)]
        )[:3000]
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": render_prompt(
                "content_analysis.video_type_user",
                required=("transcript_excerpts",),
                transcript_excerpts=transcript_excerpts,
            ),
        }]
        image_count = 0
        for index in indices:
            shot = shots[index]
            names = list(shot.get("frames") or ([shot.get("preview")] if shot.get("preview") else []))
            if not names:
                continue
            content.append({
                "type": "text",
                "text": render_prompt(
                    "content_analysis.representative_shot_label",
                    required=("shot_index",),
                    shot_index=shot["index"],
                ),
            })
            content.append(self._image_part(frames_dir / names[len(names) // 2]))
            image_count += 1
        if image_count == 0:
            return "uncertain"
        response = runtime._chat_completion_messages(
            port,
            [{"role": "user", "content": content}],
            temperature=0.0,
            max_tokens=300,
            system=get_prompt("content_analysis.video_type_system"),
        )
        payload = runtime._parse_json_payload(response, "video_type")
        video_type = str(payload.get("video_type") or "uncertain")
        confidence = float(payload.get("confidence") or 0.0)
        if video_type not in {"film_only", "presenter_only", "mixed", "uncertain"}:
            return "uncertain"
        return video_type if confidence >= 0.75 else "uncertain"

    def _analyze_visual_shots(
        self,
        runtime: QwenTranslationStage,
        port: int,
        shots: list[dict[str, Any]],
        frames_dir: Path,
        segments: list[Segment],
        video_type: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        batch_size = 4
        for offset in range(0, len(shots), batch_size):
            batch = shots[offset:offset + batch_size]
            self.report_progress(
                0.40 + 0.54 * offset / len(shots),
                f"Qwen-VL: обработано сцен {offset}/{len(shots)}; "
                f"анализ {offset + 1}–{offset + len(batch)}…",
            )
            content: list[dict[str, Any]] = [{
                "type": "text",
                "text": render_prompt(
                    "content_analysis.visual_shots_user",
                    required=("video_type",),
                    video_type=video_type,
                ),
            }]
            for shot in batch:
                start, end = float(shot["start"]), float(shot["end"])
                speech = " ".join(
                    f"RU: {segment.ru} EN: {segment.en or ''}"
                    for segment in segments
                    if segment.start < end and segment.end > start
                )[:1600]
                content.append({
                    "type": "text",
                    "text": render_prompt(
                        "content_analysis.visual_shot_context",
                        required=("shot_index", "start", "end", "speech"),
                        shot_index=shot["index"],
                        start=f"{start:.3f}",
                        end=f"{end:.3f}",
                        speech=speech or "[silent]",
                    ),
                })
                names = list(shot.get("frames") or ([shot.get("preview")] if shot.get("preview") else []))
                for name in names[:3]:
                    content.append(self._image_part(frames_dir / str(name)))
            try:
                response = runtime._chat_completion_messages(
                    port,
                    [{"role": "user", "content": content}],
                    temperature=0.0,
                    max_tokens=max(1200, len(batch) * 700),
                    system=get_prompt("content_analysis.visual_shots_system"),
                )
                payload = runtime._parse_json_payload(response, "shots")
                raw_items = payload.get("shots")
                if not isinstance(raw_items, list):
                    raise ValueError("visual response has no shots")
                by_index = {int(item["index"]): item for item in raw_items if isinstance(item, dict)}
                if set(by_index) != {int(item["index"]) for item in batch}:
                    raise ValueError("visual response does not cover the requested shots")
                batch_result: list[dict[str, Any]] = []
                for shot in batch:
                    names = list(shot.get("frames") or ([shot.get("preview")] if shot.get("preview") else []))
                    if not names:
                        batch_result.append(self._safe_visual_fallback(shot))
                        continue
                    batch_result.append(self._validated_visual_shot(
                        shot, by_index[int(shot["index"])], video_type
                    ))
                result.extend(batch_result)
            except (TranslationExecutionError, OSError, ValueError, KeyError, TypeError):
                result.extend(self._safe_visual_fallback(shot) for shot in batch)
            completed = offset + len(batch)
            review = sum(item.get("requires_review", False) for item in result)
            self.report_progress(
                0.40 + 0.54 * completed / len(shots),
                f"Qwen-VL: обработано сцен {completed}/{len(shots)}; "
                f"требуют проверки: {review}",
            )
        return result

    @staticmethod
    def _validated_visual_shot(
        shot: dict[str, Any], raw: dict[str, Any], video_type: str
    ) -> dict[str, Any]:
        source_type = str(raw.get("source_type") or "uncertain")
        allowed_sources = {"movie_scene", "author", "interview_guest", "stock_footage",
                           "screen_recording", "advertisement", "text_graphic", "uncertain"}
        if source_type not in allowed_sources:
            source_type = "uncertain"
        text_role = str(raw.get("text_role") or "none")
        allowed_roles = {"none", "subtitle", "diegetic_text", "editorial_overlay",
                         "title_card", "advertising_overlay", "diagram"}
        if text_role not in allowed_roles:
            text_role = "none"
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        contains_author = bool(raw.get("contains_author")) and source_type == "author"
        if video_type in {"film_only", "uncertain"}:
            contains_author = False
            if source_type == "author":
                source_type = "movie_scene" if video_type == "film_only" else "uncertain"
        text_regions = normalize_text_regions(raw.get("text_regions"))
        cleanup_status = text_cleanup_status(text_role, text_regions)
        russian_regions = [
            item for item in text_regions
            if contains_cyrillic(item.get("detected_text"))
        ]
        primary_text = next(
            (item for item in russian_regions if item.get("localizable") is True),
            russian_regions[0] if russian_regions else None,
        )
        detected_text = str(primary_text.get("detected_text") or "") if primary_text else ""
        translated_text = (
            str(primary_text.get("translated_text") or "")
            if primary_text and cleanup_status == "static_ready"
            else ""
        )
        text_confidence = max(
            (float(item.get("confidence") or 0.0) for item in russian_regions),
            default=confidence if text_role != "none" else 0.0,
        )
        return {
            **shot,
            "source_type": source_type,
            "contains_author": contains_author,
            "contains_text": text_role != "none" or bool(text_regions),
            "text_role": text_role,
            "detected_text": detected_text,
            "translated_text": translated_text,
            "text_regions": text_regions,
            "text_cleanup_status": cleanup_status,
            "video_type": video_type,
            "text_confidence": round(text_confidence, 3),
            "confidence": confidence,
            "requires_review": (
                confidence < AUTHOR_CONFIDENCE_THRESHOLD
                or source_type == "uncertain"
                or cleanup_status == "text_cleanup_review"
            ),
            "reason": str(raw.get("reason") or "")[:1000],
        }

    @staticmethod
    def _safe_visual_fallback(shot: dict[str, Any]) -> dict[str, Any]:
        return {
            **shot,
            "source_type": "uncertain",
            "contains_author": False,
            "contains_text": False,
            "text_role": "none",
            "detected_text": "",
            "translated_text": "",
            "text_regions": [],
            "text_cleanup_status": "not_needed",
            "video_type": "uncertain",
            "text_confidence": 0.0,
            "confidence": 0.0,
            "requires_review": True,
            "reason": "qwen_vl_unavailable_keep_original",
        }

    def _detect_shots(self, source: Path, output: Path) -> dict[str, Any]:
        executable = QwenTranslationStage._resolve_executable(self.python)
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = source_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        result = subprocess.run([executable, "-m", "ru_en_dubber.editing.detector",
            "--source", str(source), "--output", str(output), "--threshold", str(self.scene_threshold)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
            env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not output.is_file():
            raise ContentAnalysisError("shot detection failed: " + (result.stderr[-1000:] or "no output"))
        try:
            return json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ContentAnalysisError("shot detector returned invalid JSON") from error
