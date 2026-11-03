from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.prompts import get_prompt, render_prompt
from ru_en_dubber.security import MediaSecurityError, validate_media_file, validate_probe_payload
from ru_en_dubber.translation.qwen import QwenTranslationStage, TranslationError
from ru_en_dubber.visuals.models import fingerprint
from ru_en_dubber.visuals.transport import ProviderError, Transport, write_json

from .jamendo import JamendoProvider
from .library import MusicCatalog, MusicTrack, file_sha256


MUSIC_BED_VERSION = "music-bed-v1-local-first-jamendo-library"
PLAN_FIELDS = {"brief", "slots"}
BRIEF_FIELDS = {"genres", "moods", "energy", "instrumental_only", "search_terms", "avoid"}
SLOT_FIELDS = {"start", "target_duration", "mood", "energy", "reason"}


class MusicBedError(RuntimeError):
    pass


def _tokens(values: list[str] | tuple[str, ...]) -> set[str]:
    return {
        token
        for value in values
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1
    }


def validate_music_plan(payload: object, duration: float) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != PLAN_FIELDS:
        raise ValueError("invalid music plan schema")
    brief = payload.get("brief")
    slots = payload.get("slots")
    if not isinstance(brief, dict) or set(brief) != BRIEF_FIELDS:
        raise ValueError("invalid music brief schema")
    if not isinstance(slots, list) or not 1 <= len(slots) <= 20:
        raise ValueError("music plan must contain 1-20 slots")
    genres = _validated_strings(brief.get("genres"), 8)
    moods = _validated_strings(brief.get("moods"), 8)
    search_terms = _validated_strings(brief.get("search_terms"), 12)
    avoid = _validated_strings(brief.get("avoid"), 12, allow_empty=True)
    energy = str(brief.get("energy") or "").casefold()
    if brief.get("instrumental_only") is not True:
        raise ValueError("music plan must require instrumental music")
    if energy not in {"very_low", "low", "medium", "high", "very_high"}:
        raise ValueError("invalid music energy")
    normalized_slots = []
    last_start = -1.0
    for raw in slots:
        if not isinstance(raw, dict) or set(raw) != SLOT_FIELDS:
            raise ValueError("invalid music slot schema")
        start = float(raw["start"])
        target = float(raw["target_duration"])
        slot_energy = str(raw["energy"]).casefold()
        if (not math.isfinite(start) or not math.isfinite(target) or start < last_start
                or start < 0 or start >= duration or target <= 0 or target > duration
                or slot_energy not in {"very_low", "low", "medium", "high", "very_high"}):
            raise ValueError("invalid music slot timing")
        normalized_slots.append({
            "start": round(start, 3),
            "target_duration": round(min(target, duration - start), 3),
            "mood": str(raw["mood"]).strip()[:100],
            "energy": slot_energy,
            "reason": str(raw["reason"]).strip()[:500],
        })
        last_start = start
    return {
        "brief": {
            "genres": genres,
            "moods": moods,
            "energy": energy,
            "instrumental_only": True,
            "search_terms": search_terms,
            "avoid": avoid,
        },
        "slots": normalized_slots,
    }


def _validated_strings(value: object, maximum: int, *, allow_empty: bool = False) -> list[str]:
    minimum = 0 if allow_empty else 1
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("music plan tags must be non-empty bounded arrays")
    result = [str(item).strip()[:100] for item in value]
    if any(not item for item in result):
        raise ValueError("music plan tags must be non-empty strings")
    return result


def score_track(track: MusicTrack, plan: dict[str, Any]) -> float:
    brief = plan["brief"]
    wanted_genres = _tokens(brief["genres"])
    wanted_moods = _tokens(brief["moods"])
    wanted_context = _tokens(brief["search_terms"])
    avoided = _tokens(brief["avoid"])
    track_genres = _tokens(track.genres)
    track_moods = _tokens(track.moods)
    track_context = _tokens(track.use_for)
    forbidden = _tokens(track.avoid_for)
    query_all = wanted_genres | wanted_moods | wanted_context
    track_all = track_genres | track_moods | track_context
    if avoided & track_all or forbidden & query_all:
        return 0.0
    genre = len(wanted_genres & track_genres) / max(1, len(wanted_genres))
    mood = len(wanted_moods & track_moods) / max(1, len(wanted_moods))
    context = len(wanted_context & track_context) / max(1, len(wanted_context))
    energy = 1.0 if track.energy == brief["energy"] else 0.35
    instrumental = 1.0 if (track.instrumental or not brief["instrumental_only"]) else 0.0
    return round(0.30 * genre + 0.30 * mood + 0.15 * context
                 + 0.15 * energy + 0.10 * instrumental, 4)


class MusicBedStage(PipelineStage):
    def __init__(
        self,
        *,
        mode: str,
        library: Path,
        server: str,
        model: Path,
        ffmpeg: str,
        ffprobe: str,
        jamendo_client_id: str = "",
        min_score: float = 0.72,
        target_lufs: float = -24.0,
        crossfade_seconds: float = 3.0,
        request_budget: int = 20,
        gpu_layers: int = 99,
    ) -> None:
        if mode not in {"off", "local", "hybrid"}:
            raise ValueError("music mode must be off, local or hybrid")
        if not 0 <= min_score <= 1:
            raise ValueError("music minimum score must be between 0 and 1")
        if not -40 <= target_lufs <= -8:
            raise ValueError("music target LUFS must be between -40 and -8")
        if not 0 <= crossfade_seconds <= 10:
            raise ValueError("music crossfade must be between 0 and 10 seconds")
        self.mode = mode
        self.library = library
        self.server = server
        self.model = model
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.jamendo_client_id = jamendo_client_id.strip()
        self.min_score = min_score
        self.target_lufs = target_lufs
        self.crossfade_seconds = crossfade_seconds
        self.request_budget = request_budget
        self.gpu_layers = gpu_layers

    @property
    def name(self) -> str:
        return "music_bed"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        job_dir = job_dir.resolve()
        music_dir = job_dir / "music"
        music_dir.mkdir(parents=True, exist_ok=True)
        report_path = music_dir / "music-report.json"
        bed_path = music_dir / "ai-music.wav"
        plan_path = music_dir / "music-plan.json"
        credits_path = music_dir / "music-credits.json"
        playlist_path = music_dir / "playlist.json"
        candidates_path = music_dir / "candidates.json"
        self._invalidate_premiere(manifest)

        if self.mode == "off":
            report = {"version": 1, "status": "off", "mode": self.mode}
            write_json(report_path, report)
            self._apply_report(
                manifest, job_dir, report_path, report, None, None, None, None
            )
            return manifest

        try:
            ffprobe = QwenTranslationStage._resolve_executable(self.ffprobe)
            ffmpeg = QwenTranslationStage._resolve_executable(self.ffmpeg)
            duration = self._timeline_duration(manifest, job_dir, ffprobe)
        except (OSError, ValueError, RuntimeError) as error:
            return self._no_safe_music(
                manifest, job_dir, report_path, 0.0,
                f"timeline_unavailable:{type(error).__name__}",
            )
        try:
            catalog = MusicCatalog.load(self.library)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return self._no_safe_music(
                manifest, job_dir, report_path, duration,
                f"invalid_catalog:{type(error).__name__}",
            )
        source_key = fingerprint([
            MUSIC_BED_VERSION, self.mode, duration, self.min_score,
            self.target_lufs, self.crossfade_seconds,
            bool(self.jamendo_client_id),
            [track.to_dict() for track in catalog.tracks],
            manifest.metadata.get("youtube"),
            [(segment.id, segment.start, segment.end, segment.en) for segment in manifest.segments],
        ])
        if report_path.is_file():
            try:
                saved = json.loads(report_path.read_text(encoding="utf-8"))
                if (saved.get("status") == "ready"
                        and source_key in {saved.get("fingerprint"), saved.get("output_fingerprint")}):
                    cached_bed = bed_path
                    if cached_bed.is_file():
                        self._apply_report(
                            manifest, job_dir, report_path, saved, cached_bed,
                            plan_path if plan_path.is_file() else None,
                            credits_path if credits_path.is_file() else None,
                            candidates_path if candidates_path.is_file() else None,
                        )
                        return manifest
            except (OSError, ValueError, KeyError, TypeError):
                pass

        usable = [track for track in catalog.tracks if track.resolve(catalog.root).is_file()]
        if not usable and (self.mode != "hybrid" or not self.jamendo_client_id):
            return self._no_safe_music(
                manifest, job_dir, report_path, duration, "empty_library_and_no_provider",
                fingerprint_value=source_key,
            )

        self.report_progress(0.05, "Qwen составляет музыкальный профиль ролика…")
        try:
            plan = self._plan(manifest, job_dir, duration)
        except (OSError, ValueError, TranslationError, MusicBedError) as error:
            return self._no_safe_music(
                manifest, job_dir, report_path, duration,
                f"planner_unavailable:{type(error).__name__}",
                fingerprint_value=source_key,
            )
        write_json(plan_path, {"version": 1, "timeline_duration": duration, **plan})

        self.report_progress(0.28, "Выбор музыки из локальной библиотеки…")
        selected = self._local_candidates(catalog, usable, plan, ffprobe)
        candidate_report = {
            "version": 1,
            "invalid_catalog_entries": catalog.invalid_entries,
            "local": [{
                "id": track.id,
                "title": track.title,
                "artist": track.artist,
                "score": score_track(track, plan),
                "selected": any(item[0].id == track.id for item in selected),
            } for track in usable],
            "jamendo": [],
        }
        write_json(candidates_path, candidate_report)
        required = duration + self.crossfade_seconds * max(0, len(selected) - 1)
        total = sum(item[1] for item in selected)
        provider_events: list[dict[str, Any]] = []
        if total < required and self.mode == "hybrid" and self.jamendo_client_id:
            self.report_progress(0.42, "Поиск недостающей музыки через Jamendo…")
            transport = Transport(music_dir / "api-cache", budget=self.request_budget)
            provider = JamendoProvider(transport, self.jamendo_client_id)
            try:
                results = provider.search(
                    plan["brief"]["genres"] + plan["brief"]["moods"]
                    + plan["brief"]["search_terms"]
                )
                downloaded = self._download_jamendo(
                    provider, catalog, results, plan, ffprobe,
                    duration - total, selected,
                )
                selected.extend(downloaded)
                downloaded_ids = {item[0].provider_id for item in downloaded}
                candidate_report["jamendo"] = [{
                    "provider_id": item["provider_id"],
                    "title": item["title"],
                    "artist": item["artist"],
                    "license": item["license"],
                    "source_url": item["source_url"],
                    "selected": item["provider_id"] in downloaded_ids,
                } for item in results]
                write_json(candidates_path, candidate_report)
            except (ProviderError, OSError, ValueError, MediaSecurityError) as error:
                provider_events.append({"provider": "jamendo", "status": type(error).__name__})
            provider_events.extend(transport.events)

        if not selected:
            return self._no_safe_music(
                manifest, job_dir, report_path, duration, "no_safe_music",
                fingerprint_value=source_key, plan_path=plan_path,
                provider_events=provider_events, candidates_path=candidates_path,
            )

        # Repeat the approved playlist if it is shorter than the final sequence.
        playlist = list(selected)
        effective = sum(item[1] for item in playlist) - self.crossfade_seconds * max(0, len(playlist) - 1)
        while effective < duration and len(playlist) < 40:
            for item in selected:
                playlist.append(item)
                effective += item[1] - self.crossfade_seconds
                if effective >= duration or len(playlist) >= 40:
                    break
        if effective < duration:
            return self._no_safe_music(
                manifest, job_dir, report_path, duration, "insufficient_safe_music",
                fingerprint_value=source_key, plan_path=plan_path,
                provider_events=provider_events, candidates_path=candidates_path,
            )

        tracks_dir = music_dir / "tracks"
        tracks_dir.mkdir(parents=True, exist_ok=True)
        copied: dict[str, Path] = {}
        entries = []
        cursor = 0.0
        for index, (track, track_duration, score) in enumerate(playlist, 1):
            source = track.resolve(catalog.root)
            if track.id not in copied:
                destination = tracks_dir / f"{track.id}{source.suffix.casefold()}"
                shutil.copy2(source, destination)
                copied[track.id] = destination
            entries.append({
                "index": index, "track_id": track.id, "provider": track.provider,
                "title": track.title, "artist": track.artist,
                "start": round(max(0.0, cursor), 3), "source_duration": track_duration,
                "score": score, "file": copied[track.id].relative_to(job_dir).as_posix(),
            })
            cursor += track_duration - self.crossfade_seconds
            if cursor >= duration:
                break
        playlist = playlist[:len(entries)]
        write_json(playlist_path, {"version": 1, "duration": duration, "tracks": entries})
        credits = self._credits(playlist)
        write_json(credits_path, {"version": 1, "tracks": credits})

        self.report_progress(0.72, "Сборка отдельной музыкальной дорожки…")
        try:
            self._render(ffmpeg, [copied[item[0].id] for item in playlist],
                         [item[1] for item in playlist], bed_path, duration)
            self._validate_audio(ffprobe, bed_path, maximum_duration=duration + 2)
        except (OSError, ValueError, MediaSecurityError, MusicBedError) as error:
            bed_path.unlink(missing_ok=True)
            return self._no_safe_music(
                manifest, job_dir, report_path, duration,
                f"render_unavailable:{type(error).__name__}",
                fingerprint_value=source_key, plan_path=plan_path,
                provider_events=provider_events,
            )
        output_fingerprint = fingerprint([
            MUSIC_BED_VERSION, self.mode, duration, self.min_score,
            self.target_lufs, self.crossfade_seconds, bool(self.jamendo_client_id),
            [track.to_dict() for track in catalog.tracks],
            manifest.metadata.get("youtube"),
            [(segment.id, segment.start, segment.end, segment.en) for segment in manifest.segments],
        ])
        report = {
            "version": 1, "status": "ready", "mode": self.mode,
            "fingerprint": source_key, "output_fingerprint": output_fingerprint,
            "timeline_duration": duration, "track_count": len(entries),
            "unique_track_count": len(copied), "target_lufs": self.target_lufs,
            "crossfade_seconds": self.crossfade_seconds,
            "provider_events": provider_events,
        }
        write_json(report_path, report)
        self._apply_report(
            manifest, job_dir, report_path, report, bed_path, plan_path,
            credits_path, candidates_path,
        )
        self.report_progress(0.99, f"Музыкальная дорожка готова: {len(entries)} фрагментов")
        return manifest

    def _plan(self, manifest: Manifest, job_dir: Path, duration: float) -> dict[str, Any]:
        runtime = QwenTranslationStage(
            server=self.server, model=self.model, gpu_layers=self.gpu_layers,
            context_size=16384, request_timeout=300,
        )
        executable = runtime._resolve_executable(self.server)
        model = self.model.expanduser().resolve()
        if not model.is_file():
            raise MusicBedError(f"music planner model not found: {model}")
        port = runtime._reserve_port()
        log_path = job_dir / "music" / "planner.log"
        process, log = runtime._start_server(executable, model, port, log_path)
        try:
            runtime._wait_until_ready(process, port, log_path)
            title = str(manifest.metadata.get("youtube", {}).get("title") or "")[:500]
            transcript = " ".join((segment.en or segment.ru) for segment in manifest.segments)[:8000]
            content_summary = json.dumps(
                manifest.metadata.get("content_analysis", {}), ensure_ascii=False
            )[:4000]
            prompt = render_prompt(
                "music.user",
                required=("duration", "payload_json"),
                duration=f"{duration:.3f}",
                payload_json=json.dumps(
                    {"title": title, "summary": content_summary, "transcript": transcript},
                    ensure_ascii=False,
                ),
            )
            attempts: list[dict[str, Any]] = []
            for attempt in range(1, 4):
                retry_instruction = "" if attempt == 1 else "\n" + get_prompt("music.retry")
                response = runtime._chat_completion(
                    port, prompt + retry_instruction, temperature=0.05,
                    max_tokens=1000,
                    system=get_prompt("music.system"),
                )
                try:
                    payload = runtime._parse_json_payload(response, "brief")
                    validated = validate_music_plan(payload, duration)
                except (TranslationError, ValueError) as error:
                    attempts.append({
                        "attempt": attempt,
                        "status": "invalid",
                        "error": type(error).__name__,
                        "response": response[:20_000],
                    })
                    write_json(job_dir / "music" / "planner-responses.json", {
                        "version": 1, "attempts": attempts,
                    })
                    if attempt == 3:
                        raise MusicBedError("music planner returned invalid JSON") from error
                    continue
                attempts.append({
                    "attempt": attempt, "status": "accepted",
                    "response": response[:20_000],
                })
                write_json(job_dir / "music" / "planner-responses.json", {
                    "version": 1, "attempts": attempts,
                })
                return validated
            raise MusicBedError("music planner exhausted retries")
        finally:
            runtime._stop_server(process)
            log.close()

    def _local_candidates(
        self, catalog: MusicCatalog, tracks: list[MusicTrack], plan: dict[str, Any], ffprobe: str
    ) -> list[tuple[MusicTrack, float, float]]:
        ranked = sorted(((track, score_track(track, plan)) for track in tracks),
                        key=lambda item: (-item[1], item[0].id))
        selected = []
        for track, score in ranked:
            if score < self.min_score:
                continue
            try:
                duration = self._validate_audio(ffprobe, track.resolve(catalog.root))
            except (OSError, ValueError, MediaSecurityError, MusicBedError):
                continue
            selected.append((track, duration, score))
        return selected

    def _download_jamendo(
        self, provider: JamendoProvider, catalog: MusicCatalog, results: list[dict],
        plan: dict[str, Any], ffprobe: str, missing_duration: float,
        selected: list[tuple[MusicTrack, float, float]],
    ) -> list[tuple[MusicTrack, float, float]]:
        output = []
        existing_ids = {track.provider_id for track in catalog.tracks if track.provider_id}
        selected_ids = {track.id for track, _, _ in selected}
        covered = 0.0
        for raw in results:
            provider_id = str(raw["provider_id"])
            if provider_id in existing_ids:
                continue
            identifier = f"jamendo-{provider_id}"
            if identifier in selected_ids:
                continue
            destination = catalog.root / "jamendo" / f"{identifier}.mp3"
            try:
                provider.download(raw["download_url"], destination)
                duration = self._validate_audio(ffprobe, destination)
            except (ProviderError, OSError, ValueError, MediaSecurityError, MusicBedError):
                destination.unlink(missing_ok=True)
                continue
            brief = plan["brief"]
            track = MusicTrack(
                id=identifier, file=f"jamendo/{identifier}.mp3",
                title=raw["title"], artist=raw["artist"], provider="jamendo",
                provider_id=provider_id, genres=tuple(brief["genres"]),
                moods=tuple(brief["moods"]), energy=brief["energy"],
                instrumental=True, use_for=tuple(brief["search_terms"]),
                avoid_for=tuple(brief["avoid"]), license=raw["license"],
                license_url=raw["license_url"], source_url=raw["source_url"] or None,
                attribution=f"{raw['title']} — {raw['artist']}", approved=True,
                downloaded_at=datetime.now(timezone.utc).isoformat(),
                sha256=file_sha256(destination), duration=duration,
                classification_source="qwen_music_plan",
            )
            try:
                catalog.add(track)
            except OSError:
                destination.unlink(missing_ok=True)
                continue
            score = max(self.min_score, score_track(track, plan))
            output.append((track, duration, score))
            covered += duration - self.crossfade_seconds
            if covered >= max(0.0, missing_duration):
                break
        return output

    def _render(
        self, ffmpeg: str, paths: list[Path], durations: list[float],
        output: Path, target_duration: float,
    ) -> None:
        if not paths:
            raise MusicBedError("music render has no input tracks")
        fade = min(self.crossfade_seconds, max(0.0, min(durations) / 3))
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
        for path in paths:
            command.extend(["-protocol_whitelist", "file", "-i", str(path)])
        filters = []
        for index in range(len(paths)):
            filters.append(
                f"[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:"
                f"channel_layouts=stereo,loudnorm=I={self.target_lufs}:TP=-2:LRA=11[a{index}]"
            )
        current = "a0"
        for index in range(1, len(paths)):
            label = f"x{index}"
            if fade > 0:
                filters.append(f"[{current}][a{index}]acrossfade=d={fade:.3f}:c1=tri:c2=tri[{label}]")
            else:
                filters.append(f"[{current}][a{index}]concat=n=2:v=0:a=1[{label}]")
            current = label
        fade_out = min(3.0, target_duration / 4)
        fade_start = max(0.0, target_duration - fade_out)
        filters.append(
            f"[{current}]apad=whole_dur={target_duration:.6f},"
            f"atrim=duration={target_duration:.6f},"
            f"afade=t=out:st={fade_start:.6f}:d={fade_out:.6f}[out]"
        )
        command.extend([
            "-filter_complex", ";".join(filters), "-map", "[out]", "-vn", "-sn", "-dn",
            "-map_metadata", "-1", "-map_chapters", "-1", "-c:a", "pcm_s16le",
            "-ar", "48000", "-ac", "2", str(output),
        ])
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=max(180.0, target_duration * 3),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not output.is_file():
            raise MusicBedError("music render failed: " + (result.stderr[-2000:] or "no output"))

    def _timeline_duration(self, manifest: Manifest, job_dir: Path, ffprobe: str) -> float:
        plan_relative = manifest.artifacts.get("episode_plan")
        if plan_relative:
            try:
                plan_path = (job_dir.resolve() / plan_relative).resolve()
                plan_path.relative_to(job_dir.resolve())
                payload = json.loads(plan_path.read_text(encoding="utf-8"))
                ends = [float(item["timeline_end"]) for item in payload.get("episodes", [])]
                if ends and max(ends) > 0:
                    return round(max(ends), 6)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        relative = manifest.artifacts.get("source_video") or manifest.job.source_file
        source = (job_dir.resolve() / relative).resolve()
        try:
            source.relative_to(job_dir.resolve())
        except ValueError as error:
            raise MusicBedError("source video escapes job directory") from error
        payload = self._probe(ffprobe, source)
        duration = float(payload["format"]["duration"])
        report = manifest.metadata.get("ad_removal", {})
        if report.get("mode") == "remove":
            for item in report.get("ranges", []):
                duration -= max(0.0, float(item["end"]) - float(item["start"]))
        if not math.isfinite(duration) or duration <= 0:
            raise MusicBedError("invalid final timeline duration")
        return round(duration, 6)

    @staticmethod
    def _probe(ffprobe: str, path: Path) -> dict[str, Any]:
        result = subprocess.run([
            ffprobe, "-protocol_whitelist", "file", "-max_alloc", str(256 * 1024 * 1024),
            "-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,codec_tag_string,duration,sample_rate,channels:"
            "format=format_name,duration,size", "-of", "json", str(path),
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise MusicBedError("FFprobe failed for music media")
        return json.loads(result.stdout)

    def _validate_audio(
        self, ffprobe: str, path: Path, *, maximum_duration: float = 3600.0
    ) -> float:
        validate_media_file(path, label="music track", maximum_bytes=100 * 1024 * 1024)
        payload = self._probe(ffprobe, path)
        validate_probe_payload(
            payload, label="music track", require_audio=True,
            maximum_duration=maximum_duration,
            allowed_format_tokens={"mp3", "wav", "flac", "ogg"},
        )
        return float(payload["format"]["duration"])

    def _no_safe_music(
        self, manifest: Manifest, job_dir: Path, report_path: Path, duration: float,
        reason: str, *, fingerprint_value: str | None = None,
        plan_path: Path | None = None, provider_events: list[dict] | None = None,
        candidates_path: Path | None = None,
    ) -> Manifest:
        report = {
            "version": 1, "status": "no_safe_music", "mode": self.mode,
            "reason": reason, "timeline_duration": duration,
            "fingerprint": fingerprint_value, "provider_events": provider_events or [],
        }
        write_json(report_path, report)
        self._apply_report(
            manifest, job_dir, report_path, report, None, plan_path, None,
            candidates_path,
        )
        self.report_progress(0.99, "Подходящая безопасная музыка не найдена; экспорт продолжится")
        return manifest

    @staticmethod
    def _credits(playlist: list[tuple[MusicTrack, float, float]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for track, _, _ in playlist:
            if track.id in seen:
                continue
            seen.add(track.id)
            result.append({
                "id": track.id, "title": track.title, "artist": track.artist,
                "provider": track.provider, "provider_id": track.provider_id,
                "license": track.license, "license_url": track.license_url,
                "source_url": track.source_url, "attribution": track.attribution,
                "sha256": track.sha256,
            })
        return result

    @staticmethod
    def _invalidate_premiere(manifest: Manifest) -> None:
        if "premiere_export" in manifest.completed_stages:
            manifest.completed_stages.remove("premiere_export")
        manifest.metadata.pop("premiere_export", None)
        for key in ("premiere_export", "premiere_xml"):
            manifest.artifacts.pop(key, None)

    @staticmethod
    def _apply_report(
        manifest: Manifest, job_dir: Path, report_path: Path, report: dict[str, Any],
        bed_path: Path | None, plan_path: Path | None, credits_path: Path | None,
        candidates_path: Path | None,
    ) -> None:
        manifest.artifacts["music_report"] = report_path.relative_to(job_dir).as_posix()
        if bed_path is not None:
            manifest.artifacts["music_bed"] = bed_path.relative_to(job_dir).as_posix()
        else:
            manifest.artifacts.pop("music_bed", None)
        if plan_path is not None:
            manifest.artifacts["music_plan"] = plan_path.relative_to(job_dir).as_posix()
        else:
            manifest.artifacts.pop("music_plan", None)
        if credits_path is not None:
            manifest.artifacts["music_credits"] = credits_path.relative_to(job_dir).as_posix()
        else:
            manifest.artifacts.pop("music_credits", None)
        if candidates_path is not None:
            manifest.artifacts["music_candidates"] = candidates_path.relative_to(job_dir).as_posix()
        else:
            manifest.artifacts.pop("music_candidates", None)
        manifest.metadata["music_bed"] = report
