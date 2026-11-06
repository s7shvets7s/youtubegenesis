from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ru_en_dubber.models import Manifest, Segment
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.prompts import get_prompt, render_prompt


class TranslationError(RuntimeError):
    """Base error raised by the local translation adapter."""


class TranslationServerNotFoundError(TranslationError):
    """Raised when llama-server or the configured model is unavailable."""


class TranslationExecutionError(TranslationError):
    """Raised when the model server returns an unusable translation."""


class QwenTranslationStage(PipelineStage):
    """Context-aware RU to EN dubbing adaptation through local llama-server."""

    def __init__(
        self,
        *,
        server: str,
        model: Path,
        vision_projector: Path | None = None,
        model_name: str = "Qwen3-VL-4B-Instruct-GGUF",
        quantization: str = "Q4_K_M",
        batch_size: int = 8,
        context_size: int = 8192,
        gpu_layers: int = 99,
        ad_removal_mode: str = "off",
        ad_confidence_threshold: float = 0.85,
        startup_timeout: float = 180.0,
        request_timeout: float = 180.0,
    ) -> None:
        if not model_name.strip():
            raise ValueError("translation model name must not be empty")
        if not quantization.strip():
            raise ValueError("translation quantization must not be empty")
        if not 1 <= batch_size <= 20:
            raise ValueError("translation batch size must be between 1 and 20")
        if not 2048 <= context_size <= 32768:
            raise ValueError("translation context size must be between 2048 and 32768")
        if gpu_layers < 0:
            raise ValueError("translation GPU layers must not be negative")
        if ad_removal_mode not in {"off", "mark", "remove"}:
            raise ValueError("ad removal mode must be off, mark or remove")
        if not 0.0 <= ad_confidence_threshold <= 1.0:
            raise ValueError("ad confidence threshold must be between 0 and 1")
        if startup_timeout <= 0 or request_timeout <= 0:
            raise ValueError("translation timeouts must be positive")

        self._server = server
        self._model = model
        self._vision_projector = vision_projector
        self._model_name = model_name
        self._quantization = quantization
        self._batch_size = batch_size
        self._context_size = context_size
        self._gpu_layers = gpu_layers
        self._ad_removal_mode = ad_removal_mode
        self._ad_confidence_threshold = ad_confidence_threshold
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._failure_log_path: Path | None = None

    @property
    def name(self) -> str:
        return "translation"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        self.report_progress(0.02, "Checking the Qwen model…")
        if not manifest.segments:
            raise TranslationExecutionError("translation requires transcript segments")

        server = self._resolve_executable(self._server)
        model = self._model.expanduser().resolve()
        if not model.is_file():
            raise TranslationServerNotFoundError(f"translation model not found: {model}")

        port = self._reserve_port()
        log_path = job_dir / "translation-server.log"
        self._failure_log_path = job_dir / "translation-failures.jsonl"
        self.report_progress(None, "Loading Qwen into VRAM…")
        process, log_file = self._start_server(server, model, port, log_path)
        try:
            self._wait_until_ready(process, port, log_path)
            ad_ranges: list[dict[str, Any]] = []
            if self._ad_removal_mode != "off":
                self.report_progress(0.08, "Finding ad integrations…")
                word_timings = self._load_word_timings(
                    job_dir / str(manifest.artifacts.get("transcript") or "transcript.json")
                )
                ad_ranges = self._detect_ad_ranges(
                    manifest.segments, port, word_timings=word_timings
                )
                if self._ad_removal_mode == "remove" and ad_ranges:
                    manifest.segments = self._remove_ad_speech(
                        manifest.segments,
                        ad_ranges,
                        word_timings,
                    )
                    if not manifest.segments:
                        raise TranslationExecutionError(
                            "ad removal would remove the complete transcript"
                        )
            self.report_progress(0.10, "Model loaded, starting translation…")
            translations = self._translate_segments(manifest.segments, port)
        finally:
            self._stop_server(process)
            log_file.close()

        self.report_progress(0.96, "Checking translation completeness…")
        missing = [segment.id for segment in manifest.segments if segment.id not in translations]
        if missing:
            raise TranslationExecutionError(
                f"translation is missing segment IDs: {missing}"
            )

        limits: dict[int, int] = {}
        over_budget: list[int] = []
        for segment in manifest.segments:
            translation = translations[segment.id].strip()
            if not translation:
                raise TranslationExecutionError(
                    f"translation is empty for segment {segment.id}"
                )
            segment.en = translation
            segment.tts_file = None
            segment.tts_duration = None
            segment.dub_file = None
            segment.dub_start = None
            segment.dub_duration = None
            segment.dub_speed = None
            maximum_words = self._word_budget(segment)
            limits[segment.id] = maximum_words
            if self._word_count(translation) > maximum_words + 2:
                over_budget.append(segment.id)

        translation_path = job_dir / "translation.json"
        payload = {
            "model": self._model_name,
            "quantization": self._quantization,
            "source_language": "ru",
            "target_language": "en",
            "segments": [
                {
                    "id": segment.id,
                    "start": segment.start,
                    "end": segment.end,
                    "target_seconds": round(segment.duration, 3),
                    "maximum_words": limits[segment.id],
                    "ru": segment.ru,
                    "en": segment.en,
                }
                for segment in manifest.segments
            ],
        }
        temporary_path = translation_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(translation_path)

        manifest.completed_stages = [
            stage
            for stage in manifest.completed_stages
            if stage
            not in {"fake_translation", "content_analysis", "animation_plan", "tts", "timing_alignment", "visual_rebuild", "avatar_overlay", "music_bed", "premiere_export"}
        ]
        manifest.metadata.pop("translation_provider", None)
        manifest.metadata.pop("tts", None)
        manifest.metadata.pop("timing_alignment", None)
        manifest.metadata.pop("premiere_export", None)
        manifest.metadata.pop("music_bed", None)
        manifest.metadata.pop("content_analysis", None)
        manifest.metadata.pop("animation_plan", None)
        manifest.metadata.pop("visual_rebuild", None)
        manifest.metadata.pop("avatar", None)
        manifest.artifacts.pop("tts_report", None)
        manifest.artifacts.pop("tts_segments", None)
        manifest.artifacts.pop("aligned_tts", None)
        manifest.artifacts.pop("premiere_export", None)
        manifest.artifacts.pop("premiere_xml", None)
        manifest.artifacts.pop("music_report", None)
        manifest.artifacts.pop("music_bed", None)
        manifest.artifacts.pop("music_plan", None)
        manifest.artifacts.pop("music_candidates", None)
        manifest.artifacts.pop("music_credits", None)
        manifest.artifacts.pop("content_analysis", None)
        manifest.artifacts.pop("episode_plan", None)
        manifest.artifacts.pop("animation_plan", None)
        manifest.artifacts.pop("visual_plan", None)
        manifest.artifacts.pop("rebuild_video", None)
        manifest.artifacts.pop("avatar_video", None)
        manifest.artifacts.pop("avatar_overlay_video", None)
        manifest.artifacts.pop("avatar_base_video", None)
        manifest.artifacts.pop("avatar_composite", None)
        manifest.artifacts["translation"] = translation_path.name
        manifest.artifacts["translation_log"] = log_path.name
        if self._failure_log_path.is_file():
            manifest.artifacts["translation_failures"] = self._failure_log_path.name
        if self._ad_removal_mode != "off":
            ad_report_path = job_dir / "ad-removal.json"
            ad_report = {
                "provider": "qwen",
                "mode": self._ad_removal_mode,
                "confidence_threshold": self._ad_confidence_threshold,
                "ranges": ad_ranges,
            }
            temporary_ad_path = ad_report_path.with_suffix(".json.tmp")
            temporary_ad_path.write_text(
                json.dumps(ad_report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_ad_path.replace(ad_report_path)
            manifest.artifacts["ad_removal"] = ad_report_path.name
            manifest.metadata["ad_removal"] = ad_report
        else:
            manifest.artifacts.pop("ad_removal", None)
            manifest.metadata.pop("ad_removal", None)
        manifest.metadata["translation"] = {
            "provider": "qwen",
            "runtime": "llama.cpp",
            "model": self._model_name,
            "quantization": self._quantization,
            "source_language": "ru",
            "target_language": "en",
            "batch_size": self._batch_size,
            "context_size": self._context_size,
            "gpu_layers": self._gpu_layers,
            "segment_count": len(manifest.segments),
            "over_budget_segment_ids": over_budget,
        }
        self.report_progress(
            0.99,
            f"Translated {len(manifest.segments)} segments, to review: {len(over_budget)}",
        )
        return manifest

    def _detect_ad_ranges(
        self,
        segments: list[Segment],
        port: int,
        *,
        word_timings: dict[int, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        window_size = max(12, self._batch_size * 2)
        overlap = min(4, window_size // 3)
        offset = 0
        while offset < len(segments):
            window = segments[offset : offset + window_size]
            prompt = self._ad_detection_prompt(window)
            content: str | None = None
            for attempt in range(3):
                try:
                    content = self._chat_completion(
                        port,
                        prompt,
                        temperature=0.0,
                        max_tokens=max(384, len(window) * 48),
                    )
                    candidates.extend(
                        self._parse_ad_response(
                            content,
                            window,
                            word_timings=word_timings,
                        )
                    )
                    break
                except (TranslationExecutionError, HTTPError, URLError, TimeoutError) as error:
                    self._record_failure(window, attempt + 1, error, content)
            offset += max(1, window_size - overlap)

        accepted = [
            item
            for item in candidates
            if float(item["confidence"]) >= self._ad_confidence_threshold
            and str(item.get("kind") or "paid_sponsor")
            in {"paid_sponsor", "affiliate", "self_promo", "giveaway"}
        ]
        accepted.sort(key=lambda item: (float(item["start"]), float(item["end"])))
        merged: list[dict[str, Any]] = []
        for item in accepted:
            if merged and float(item["start"]) <= float(merged[-1]["end"]) + 1.0:
                current = merged[-1]
                current["end"] = max(float(current["end"]), float(item["end"]))
                current["end_segment_id"] = max(
                    int(current["end_segment_id"]), int(item["end_segment_id"])
                )
                current["confidence"] = max(
                    float(current["confidence"]), float(item["confidence"])
                )
                brands = {str(current.get("brand") or ""), str(item.get("brand") or "")}
                current["brand"] = ", ".join(sorted(value for value in brands if value))
                continue
            merged.append(dict(item))
        return merged

    @staticmethod
    def _ad_detection_prompt(segments: list[Segment]) -> str:
        payload = {
            "segments": [
                {
                    "id": segment.id,
                    "start": round(segment.start, 3),
                    "end": round(segment.end, 3),
                    "text": segment.ru,
                }
                for segment in segments
            ]
        }
        return render_prompt(
            "translation.ad_detection_user",
            required=("payload_json",),
            payload_json=json.dumps(payload, ensure_ascii=False),
        )

    @classmethod
    def _parse_ad_response(
        cls,
        content: str,
        window: list[Segment],
        *,
        word_timings: dict[int, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        payload = cls._parse_json_payload(content, "ad_ranges")
        items = payload.get("ad_ranges")
        if not isinstance(items, list):
            raise TranslationExecutionError("model JSON has no ad_ranges list")
        by_id = {segment.id: segment for segment in window}
        ranges: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise TranslationExecutionError("model returned an invalid ad range")
            try:
                start_id = int(item["start_id"])
                end_id = int(item["end_id"])
                confidence = float(item["confidence"])
            except (KeyError, TypeError, ValueError) as error:
                raise TranslationExecutionError("model returned an invalid ad range") from error
            if start_id not in by_id or end_id not in by_id or start_id > end_id:
                raise TranslationExecutionError("ad range refers to unknown segment IDs")
            if not 0.0 <= confidence <= 1.0:
                raise TranslationExecutionError("ad confidence must be between 0 and 1")
            start_id = cls._refine_commercial_start(start_id, end_id, window)
            brand = str(item.get("brand") or "").strip()
            start_id = cls._expand_commercial_start(
                start_id, window, brand=brand
            )
            start_quote = str(item.get("start_quote") or "").strip()
            end_quote = str(item.get("end_quote") or "").strip()
            start = by_id[start_id].start
            end = by_id[end_id].end
            if word_timings:
                start = cls._quote_boundary(
                    word_timings.get(start_id, []), start_quote, use_end=False
                ) or start
                end = cls._quote_boundary(
                    word_timings.get(end_id, []), end_quote, use_end=True
                ) or end
                transition = cls._transition_boundary(word_timings.get(end_id, []))
                if transition is not None and transition > start:
                    end = min(end, transition)
            ranges.append(
                {
                    "start": start,
                    "end": end,
                    "start_segment_id": start_id,
                    "end_segment_id": end_id,
                    "confidence": confidence,
                    "kind": str(item.get("kind") or "paid_sponsor").strip(),
                    "brand": brand,
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        return ranges

    @staticmethod
    def _refine_commercial_start(
        start_id: int, end_id: int, window: list[Segment]
    ) -> int:
        strong_start = re.compile(
            r"спонсор|партн[её]р видео|промокод|без комиссии|плеерок|"
            r"скидк|прикуп|покуп|тысяч[аи]? товаров|ключи.{0,30}донат|"
            r"выгодно.{0,40}(сделать|купить)|ссылк.{0,30}описани|"
            r"телеграм|тгк|qr.?код|куар.?код|закрепл[её]нн.{0,20}пост|"
            r"подпис(ывай|аться|к)|розыгрыш|мерч|пожертвован|донат",
            flags=re.IGNORECASE,
        )
        for segment in window:
            if start_id <= segment.id <= end_id and strong_start.search(segment.ru):
                return segment.id
        return start_id

    @staticmethod
    def _expand_commercial_start(
        start_id: int, window: list[Segment], *, brand: str = ""
    ) -> int:
        commercial = re.compile(
            r"промокод|ссылк|скидк|комисси|выгодн|покуп|купить|прикуп|"
            r"товар|донат|валют|подписк|оформлен|плеерок|спонсор|партн[её]р|"
            r"телеграм|тгк|канал|qr.?код|куар.?код|закрепл[её]нн|розыгрыш|"
            r"мерч|пожертвован|описани",
            flags=re.IGNORECASE,
        )
        index_by_id = {segment.id: index for index, segment in enumerate(window)}
        index = index_by_id[start_id]
        normalized_brand = re.sub(r"[^\w\d]+", "", brand.casefold())
        while index > 0:
            previous = window[index - 1]
            current = window[index]
            if current.start - previous.end > 2.0:
                break
            normalized_text = re.sub(r"[^\w\d]+", "", previous.ru.casefold())
            repeats_brand = (
                len(normalized_brand) >= 4 and normalized_brand in normalized_text
            )
            if not commercial.search(previous.ru) and not repeats_brand:
                break
            index -= 1
        return window[index].id

    @staticmethod
    def _quote_boundary(
        words: list[dict[str, Any]], quote: str, *, use_end: bool
    ) -> float | None:
        tokens = re.findall(r"[\wё]+", quote.lower(), flags=re.UNICODE)
        timed = [
            (
                re.sub(r"[^\wё]+", "", str(item.get("word") or "").lower()),
                item,
            )
            for item in words
        ]
        timed = [(token, item) for token, item in timed if token]
        if not tokens or len(tokens) > len(timed):
            return None
        for offset in range(len(timed) - len(tokens) + 1):
            if [token for token, _ in timed[offset : offset + len(tokens)]] == tokens:
                item = timed[offset + len(tokens) - 1][1] if use_end else timed[offset][1]
                value = item.get("end" if use_end else "start")
                return float(value) if value is not None else None
        return None

    @staticmethod
    def _transition_boundary(words: list[dict[str, Any]]) -> float | None:
        timed = [
            (
                re.sub(r"[^\wё]+", "", str(item.get("word") or "").lower()),
                item,
            )
            for item in words
        ]
        timed = [(token, item) for token, item in timed if token]
        transitions = (
            ("как", "вы", "поняли"),
            ("а", "теперь", "продолжим"),
            ("ну", "а", "теперь"),
            ("вернемся", "к"),
            ("вернёмся", "к"),
            ("возвращаемся", "к"),
            ("перейдем", "к"),
            ("перейдём", "к"),
        )
        tokens = [token for token, _ in timed]
        for phrase in transitions:
            for offset in range(len(tokens) - len(phrase) + 1):
                if tuple(tokens[offset : offset + len(phrase)]) == phrase:
                    if offset == 0:
                        value = timed[offset][1].get("start")
                    else:
                        value = timed[offset - 1][1].get("end")
                    return float(value) if value is not None else None
        return None

    @staticmethod
    def _load_word_timings(path: Path) -> dict[int, list[dict[str, Any]]]:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, list):
            return {}
        result: dict[int, list[dict[str, Any]]] = {}
        normalized_id = 0
        for item in raw_segments:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            if item.get("start") is None or item.get("end") is None:
                continue
            normalized_id += 1
            words = item.get("words")
            if isinstance(words, list):
                result[normalized_id] = [word for word in words if isinstance(word, dict)]
        return result

    @classmethod
    def _remove_ad_speech(
        cls,
        segments: list[Segment],
        ranges: list[dict[str, Any]],
        word_timings: dict[int, list[dict[str, Any]]],
    ) -> list[Segment]:
        output: list[Segment] = []
        next_id = max(segment.id for segment in segments) + 1
        for segment in segments:
            cuts = [
                (max(segment.start, float(item["start"])), min(segment.end, float(item["end"])))
                for item in ranges
                if segment.start < float(item["end"])
                and segment.end > float(item["start"])
            ]
            cuts = [(start, end) for start, end in cuts if end > start]
            if not cuts:
                output.append(segment)
                continue
            if not word_timings.get(segment.id):
                continue

            kept_intervals: list[tuple[float, float]] = []
            cursor = segment.start
            for start, end in sorted(cuts):
                if start > cursor:
                    kept_intervals.append((cursor, start))
                cursor = max(cursor, end)
            if cursor < segment.end:
                kept_intervals.append((cursor, segment.end))

            for piece_index, (start, end) in enumerate(kept_intervals):
                words = [
                    item
                    for item in word_timings[segment.id]
                    if item.get("start") is not None
                    and item.get("end") is not None
                    and (float(item["start"]) + float(item["end"])) / 2 >= start
                    and (float(item["start"]) + float(item["end"])) / 2 <= end
                ]
                if not words:
                    continue
                piece_start = max(start, float(words[0]["start"]))
                piece_end = min(end, float(words[-1]["end"]))
                text = " ".join(str(item.get("word") or "").strip() for item in words).strip()
                if not text or piece_end <= piece_start:
                    continue
                piece_id = segment.id if piece_index == 0 else next_id
                if piece_index > 0:
                    next_id += 1
                output.append(
                    Segment(
                        id=piece_id,
                        start=piece_start,
                        end=piece_end,
                        ru=text,
                        speaker=segment.speaker,
                    )
                )
        return sorted(output, key=lambda item: (item.start, item.id))

    def _translate_segments(self, segments: list[Segment], port: int) -> dict[int, str]:
        translations: dict[int, str] = {}
        primary_batches = (len(segments) + self._batch_size - 1) // self._batch_size
        for offset in range(0, len(segments), self._batch_size):
            batch = segments[offset : offset + self._batch_size]
            previous_text = segments[offset - 1].ru if offset > 0 else None
            next_index = offset + len(batch)
            next_text = segments[next_index].ru if next_index < len(segments) else None
            prompt = self._translation_prompt(batch, previous_text, next_text)
            batch_result = self._request_translations(
                port,
                batch,
                prompt,
                split_prompt_builder=lambda subset: self._translation_prompt(
                    subset, previous_text, next_text
                ),
            )
            translations.update(batch_result)
            completed_batches = offset // self._batch_size + 1
            self.report_progress(
                0.10 + 0.65 * completed_batches / primary_batches,
                f"Translated {len(translations)} of {len(segments)} segments",
            )

        for pass_index in range(3):
            overlong = [
                segment
                for segment in segments
                if self._word_count(translations[segment.id])
                > self._word_budget(segment) + 2
            ]
            if not overlong:
                break
            compression_batches = (
                len(overlong) + self._batch_size - 1
            ) // self._batch_size
            for offset in range(0, len(overlong), self._batch_size):
                batch = overlong[offset : offset + self._batch_size]
                prompt = self._compression_prompt(batch, translations)
                translations.update(
                    self._request_translations(
                        port,
                        batch,
                        prompt,
                        split_prompt_builder=lambda subset: self._compression_prompt(
                            subset, translations
                        ),
                    )
                )
                completed_batches = offset // self._batch_size + 1
                pass_fraction = (pass_index + completed_batches / compression_batches) / 3
                self.report_progress(
                    0.75 + 0.20 * pass_fraction,
                    (
                        f"Shortening long lines: pass {pass_index + 1}/3, "
                        f"processed {min(offset + len(batch), len(overlong))} "
                        f"of {len(overlong)}"
                    ),
                )
        return translations

    def _request_translations(
        self,
        port: int,
        batch: list[Segment],
        prompt: str,
        *,
        split_prompt_builder: Callable[[list[Segment]], str] | None = None,
    ) -> dict[int, str]:
        expected_ids = {segment.id for segment in batch}
        last_error: Exception | None = None
        for attempt in range(3):
            content: str | None = None
            try:
                content = self._chat_completion(
                    port,
                    prompt,
                    temperature=0.1 if attempt == 0 else 0.0,
                    max_tokens=max(256, len(batch) * 96),
                )
                translations = self._parse_response(content)
                if set(translations) != expected_ids:
                    raise TranslationExecutionError(
                        "model returned a different set of segment IDs: "
                        f"expected {sorted(expected_ids)}, got {sorted(translations)}; "
                        f"response={content[:500]!r}"
                    )
                return translations
            except (TranslationExecutionError, HTTPError, URLError, TimeoutError) as error:
                last_error = error
                self._record_failure(batch, attempt + 1, error, content)
        if len(batch) > 1:
            midpoint = len(batch) // 2
            halves = (batch[:midpoint], batch[midpoint:])
            self.report_progress(
                None,
                "Qwen returned a corrupt response; splitting the batch "
                f"{sorted(expected_ids)} into smaller parts…",
            )
            recovered: dict[int, str] = {}
            for subset in halves:
                subset_prompt = (
                    split_prompt_builder(subset)
                    if split_prompt_builder is not None
                    else self._translation_prompt(subset, None, None)
                )
                recovered.update(
                    self._request_translations(
                        port,
                        subset,
                        subset_prompt,
                        split_prompt_builder=split_prompt_builder,
                    )
                )
            return recovered
        raise TranslationExecutionError(
            "translation of individual segment failed after 3 attempts: "
            f"IDs={sorted(expected_ids)}; {last_error}"
        )

    def _record_failure(
        self,
        batch: list[Segment],
        attempt: int,
        error: Exception,
        content: str | None,
    ) -> None:
        if self._failure_log_path is None:
            return
        payload = {
            "timestamp": time.time(),
            "segment_ids": [segment.id for segment in batch],
            "attempt": attempt,
            "error": f"{type(error).__name__}: {error}",
            "response": content[-12_000:] if content is not None else None,
        }
        with self._failure_log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _translation_prompt(
        self,
        batch: list[Segment],
        previous_text: str | None,
        next_text: str | None,
    ) -> str:
        input_payload = {
            "required_ids": [segment.id for segment in batch],
            "previous_context_ru": previous_text,
            "segments_to_translate": [
                {
                    "id": segment.id,
                    "ru": segment.ru,
                    "target_seconds": round(segment.duration, 3),
                    "maximum_spoken_words": self._word_budget(segment),
                }
                for segment in batch
            ],
            "next_context_ru": next_text,
        }
        return render_prompt(
            "translation.translate_user",
            required=("payload_json",),
            payload_json=json.dumps(input_payload, ensure_ascii=False),
        )

    def _compression_prompt(
        self, batch: list[Segment], translations: dict[int, str]
    ) -> str:
        input_payload = {
            "required_ids": [segment.id for segment in batch],
            "segments_to_shorten": [
                {
                    "id": segment.id,
                    "ru": segment.ru,
                    "current_en": translations[segment.id],
                    "target_seconds": round(segment.duration, 3),
                    "maximum_spoken_words": self._word_budget(segment),
                }
                for segment in batch
            ]
        }
        return render_prompt(
            "translation.compress_user",
            required=("payload_json",),
            payload_json=json.dumps(input_payload, ensure_ascii=False),
        )

    def _chat_completion(
        self,
        port: int,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        return self._chat_completion_messages(
            port,
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
        )

    def _chat_completion_messages(
        self,
        port: int,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        system: str | None = None,
    ) -> str:
        payload = {
            "model": self._model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system or get_prompt("translation.default_system"),
                },
                *messages,
            ],
            "temperature": temperature,
            "top_p": 0.8,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._request_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as error:
            raise TranslationExecutionError("llama-server returned invalid chat JSON") from error

    @staticmethod
    def _parse_response(content: str) -> dict[int, str]:
        payload = QwenTranslationStage._parse_json_payload(content, "translations")
        items = payload.get("translations")
        if not isinstance(items, list):
            raise TranslationExecutionError("model JSON has no translations list")

        translations: dict[int, str] = {}
        for item in items:
            if not isinstance(item, dict) or "id" not in item or "en" not in item:
                raise TranslationExecutionError("model returned an invalid translation item")
            segment_id = int(item["id"])
            text = str(item["en"]).strip()
            if segment_id in translations or not text:
                raise TranslationExecutionError("model returned duplicate or empty translation")
            translations[segment_id] = text
        return translations

    @staticmethod
    def _parse_json_payload(content: str, required_key: str) -> dict[str, Any]:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        payload: Any | None = None
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", cleaned):
            candidate = cleaned[match.start() :]
            try:
                decoded, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
                try:
                    decoded, _ = decoder.raw_decode(repaired)
                except json.JSONDecodeError:
                    continue
            if isinstance(decoded, dict) and required_key in decoded:
                payload = decoded
                break
        if payload is None:
            if "{" not in cleaned:
                raise TranslationExecutionError("model response does not contain JSON")
            raise TranslationExecutionError("model returned malformed JSON")
        return payload

    def _start_server(
        self, server: str, model: Path, port: int, log_path: Path
    ) -> tuple[subprocess.Popen[bytes], Any]:
        log_file = log_path.open("wb")
        command = [
            server,
            "--model",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--n-gpu-layers",
            str(self._gpu_layers),
            "--ctx-size",
            str(self._context_size),
            "--parallel",
            "1",
            "--jinja",
        ]
        if self._vision_projector is not None:
            projector = self._vision_projector.expanduser().resolve()
            if not projector.is_file():
                log_file.close()
                raise TranslationServerNotFoundError(
                    f"vision projector not found: {projector}"
                )
            command.extend(["--mmproj", str(projector)])
        try:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                ),
            )
        except OSError as error:
            log_file.close()
            raise TranslationServerNotFoundError(
                f"could not start llama-server: {error}"
            ) from error
        return process, log_file

    def _wait_until_ready(
        self, process: subprocess.Popen[bytes], port: int, log_path: Path
    ) -> None:
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            return_code = process.poll()
            if return_code is not None:
                details = self._read_log_tail(log_path)
                raise TranslationExecutionError(
                    f"llama-server stopped during startup ({return_code}): {details}"
                )
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (HTTPError, URLError, TimeoutError):
                pass
            time.sleep(0.25)
        raise TranslationExecutionError("llama-server startup timed out")

    @staticmethod
    def _stop_server(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _word_budget(segment: Segment) -> int:
        return max(1, round(segment.duration * 2.6))

    @staticmethod
    def _word_count(text: str) -> int:
        return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))

    @staticmethod
    def _resolve_executable(command: str) -> str:
        command_path = Path(command).expanduser()
        if command_path.parent != Path("."):
            if command_path.is_file():
                return str(command_path.resolve())
            raise TranslationServerNotFoundError(
                f"llama-server executable not found: {command_path}"
            )
        resolved = shutil.which(command)
        if resolved is None:
            raise TranslationServerNotFoundError(
                f"llama-server executable not found: {command}"
            )
        return resolved

    @staticmethod
    def _read_log_tail(path: Path, maximum_chars: int = 4000) -> str:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "server log is unavailable"
        return content[-maximum_chars:].strip() or "server log is empty"
