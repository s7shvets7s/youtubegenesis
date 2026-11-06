"""Visual rebuild orchestration independent of API response formats."""
from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import replace, asdict, is_dataclass
from pathlib import Path

from ru_en_dubber.security import resolve_job_path, validate_media_file, validate_probe_payload, validate_download_url
from .animations import AnimationPlanStage, render_animation
from .models import AssetCandidate, POLICY_VERSION, VisualRequest, fingerprint
from .planner import VisualPlanner, fallback_request
from .policy import rejection_reason, rank, route
from .providers import PexelsProvider, PixabayProvider, WikimediaProvider, NaraProvider, LocProvider
from .transport import Transport, ProviderError, download, write_json


def run(stage, manifest, job_dir):
    from .rebuild import VisualRebuildError
    job_dir = job_dir.resolve()
    if not manifest.segments or not all(s.en for s in manifest.segments):
        raise VisualRebuildError("visual rebuild requires a complete English translation")
    ffmpeg = stage._resolve_executable(stage._ffmpeg, "FFmpeg")
    ffprobe = stage._resolve_executable(stage._ffprobe, "FFprobe")
    script_job = manifest.metadata.get("input", {}).get("type") == "script"
    if script_job:
        original = None
        width, height, fps = 1280, 720, 24.0
        source_duration = max(segment.end for segment in manifest.segments)
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise VisualRebuildError("script narration has no valid visual duration")
    else:
        original = resolve_job_path(job_dir, manifest.job.source_file, label="source video")
        validate_media_file(original, label="source", maximum_bytes=stage._MAX_SOURCE_BYTES)
        media = stage._probe(ffprobe, original)
        stage._validate_media(media, label="source", strict_stock=False)
        width, height, fps, source_duration = stage._video_properties(media)
    # H.264 yuv420p requires even dimensions.
    width, height = width // 2 * 2, height // 2 * 2
    episode_plan = None
    if manifest.artifacts.get("episode_plan"):
        try:
            episode_path = resolve_job_path(job_dir, manifest.artifacts["episode_plan"], label="episode plan")
            episode_plan = json.loads(episode_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise VisualRebuildError("episode plan is invalid") from error
    beats = (stage.build_episode_beats(manifest, episode_plan, source_duration=source_duration)
             if episode_plan else
             stage.build_beats(manifest.segments, source_duration=source_duration,
                               target_seconds=stage._target_beat_seconds))
    beats = [replace(b, start=round(b.start * fps) / fps, end=round(b.end * fps) / fps) for b in beats]
    beats = [b for b in beats if b.end > b.start]
    if script_job:
        beats = [replace(b, action="replace_media") if b.action in {"keep", "localize_text"} else b
                 for b in beats]
    if stage._media_type_mode != "mixed":
        # Strict source-media modes exclude generated animation beats too.
        beats = [replace(b, action="replace_media") if b.action == "replace_animation" else b
                 for b in beats]
    if not beats:
        raise VisualRebuildError("English transcript produced no visual beats")
    directory = resolve_job_path(job_dir, "visual-rebuild", label="visual rebuild directory")
    media_dir, clips_dir = directory / "media", directory / "clips"
    media_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    planner = VisualPlanner(stage._planner_mode, stage._planner_server, stage._planner_model, stage._planner_gpu_layers)
    if episode_plan:
        requests = [VisualRequest(b.index, b.start, b.end, b.narration, b.subject or b.search_query,
                                  visual_intent=b.visual_intent,
                                  accuracy="exact" if b.visual_intent in {"event", "person", "number", "document", "map"} else "illustrative",
                                  media_types=("video", "image")) for b in beats]
    else:
        requests = ([fallback_request(b) for b in beats] if stage._requests_file else
                    planner.plan(beats, directory, stage.report_progress))
    if stage._requests_file:
        raw = json.loads(Path(stage._requests_file).read_text(encoding="utf-8"))
        from .planner import parse_plan
        if not isinstance(raw, list) or len(raw) != len(requests):
            raise VisualRebuildError("visual requests override must contain one object per beat")
        updated = []
        for item, base in zip(raw, requests):
            if not isinstance(item, dict):
                raise VisualRebuildError("each visual override must be an object")
            editable = dict(item)
            for field in ("index", "start", "end", "narration"):
                if field in editable and editable.pop(field) != getattr(base, field):
                    raise VisualRebuildError("visual override must not change the timeline or narration")
            updated.append(parse_plan(editable, base))
        requests = updated
    script_specs = {}
    if (script_job and not episode_plan and stage._media_type_mode == "mixed"
            and stage._script_animation_mode != "off"):
        episodes = [
            {"index": request.index, "action": "replace_animation", "visual_intent": "process",
             "subject": request.subject, "source_start": request.start,
             "source_end": request.end, "summary": request.narration}
            for request in requests if request.visual_intent == "process"
        ]
        if episodes:
            transcript = [{"id": request.index, "start": request.start, "end": request.end,
                           "text": request.narration} for request in requests]
            animation_dir = directory / "animations"
            animation_dir.mkdir(exist_ok=True)
            animation = AnimationPlanStage(mode=stage._script_animation_mode,
                server=stage._planner_server, model=stage._planner_model,
                gpu_layers=stage._planner_gpu_layers)
            specs, reason = animation.plan_specs(transcript, episodes, animation_dir)
            script_specs = {spec["episode_index"]: spec for spec in specs}
            write_json(animation_dir / "script-specs.json", {"animations": specs,
                       "mode": stage._script_animation_mode, "fallback_reason": reason})
            beats = [replace(beat, action="replace_animation", episode_index=beat.index)
                     if beat.index in script_specs else beat for beat in beats]
    allowed_types = {"image": ("image",), "mixed": ("video", "image"), "video": ("video",)}
    requests = [replace(request, media_types=allowed_types[stage._media_type_mode]) for request in requests]
    write_json(directory / "visual-requests.json", [r.to_dict() for r in requests])
    transport = Transport(directory / "search-cache", budget=stage._request_budget)
    keys = {"pexels": stage._api_key or os.environ.get("PEXELS_API_KEY", ""),
            "pixabay": os.environ.get("PIXABAY_API_KEY", ""), "nara": os.environ.get("NARA_API_KEY", "")}
    classes = {p.name: p for p in (PexelsProvider, PixabayProvider, WikimediaProvider, NaraProvider, LocProvider)}
    providers = stage._providers if stage._providers is not None else [
        classes[name](transport, keys.get(name, ""), stage._candidates_per_query)
        for name in stage._provider_names]
    providers_by_name = {p.name: p for p in providers}
    animation_specs = script_specs
    if manifest.artifacts.get("animation_plan"):
        try:
            animation_path = resolve_job_path(job_dir, manifest.artifacts["animation_plan"], label="animation plan")
            animation_payload = json.loads(animation_path.read_text(encoding="utf-8"))
            animation_specs = {int(item["episode_index"]): item for item in animation_payload.get("animations", [])}
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise VisualRebuildError("animation plan is invalid") from error
    settings = ["visual-render-v3-timebase-90000", POLICY_VERSION, [p.name for p in providers], stage._fallback,
                stage._media_type_mode,
                [(p.name, p.enabled) for p in providers], stage._candidates_per_query, stage._request_budget]
    source_identity = (["script", source_duration] if script_job else
                       ["video", original.stat().st_size, original.stat().st_mtime_ns])
    run_key = fingerprint([settings, width, height, fps, source_identity, [r.to_dict() for r in requests],
                           [{"action": b.action, "author": b.contains_author,
                             "episode_index": b.episode_index} for b in beats], animation_specs])
    output = directory / "rebuilt-video.mp4"
    plan_path = directory / "visual-plan.json"
    old_items = {}
    if plan_path.is_file():
        try:
            old = json.loads(plan_path.read_text(encoding="utf-8"))
            if old.get("fingerprint") == run_key:
                old_items = {item["index"]: item for item in old.get("beats", [])}
        except (ValueError, OSError, KeyError, TypeError):
            pass
    items, clips, all_candidates, credits = [], [], [], {}
    if old_items and (directory / "asset-candidates.json").is_file():
        try:
            saved_candidates = json.loads((directory / "asset-candidates.json").read_text(encoding="utf-8"))
            if isinstance(saved_candidates, list):
                all_candidates = saved_candidates
        except (ValueError, OSError):
            pass
    used = set()
    report = {"planner": planner.events, "media_type_mode": stage._media_type_mode,
              "providers": {p.name: {
        "enabled": p.enabled, "reason": getattr(p, "disabled_reason", None) if not p.enabled else None,
        "capabilities": asdict(p.capabilities) if is_dataclass(getattr(p, "capabilities", None)) else None
    } for p in providers}, "errors": []}
    plan = {"version": 2, "fingerprint": run_key, "policy_version": POLICY_VERSION,
            "source_duration": source_duration, "beats": items, "complete": False}

    def checkpoint():
        write_json(plan_path, plan)
        write_json(directory / "asset-candidates.json", all_candidates)
        write_json(directory / "credits.json", list(credits.values()))
        report.update(requests=transport.calls, cache_hits=transport.cache_hits, events=transport.events)
        write_json(directory / "provider-report.json", report)

    for i, (beat, request) in enumerate(zip(beats, requests)):
        stage.report_progress(0.04 + 0.84 * i / len(requests), f"Подбор видеоряда: {i+1}/{len(requests)}")
        clip = (directory / "animations" / f"episode-{beat.episode_index:04d}.mp4"
                if beat.action == "replace_animation" and beat.episode_index in animation_specs
                else clips_dir / f"beat-{request.index:04d}.mp4")
        clip.parent.mkdir(parents=True, exist_ok=True)
        old_item = old_items.get(request.index)
        if old_item and clip.is_file() and old_item.get("clip_sha256") == file_hash(clip):
            try:
                verify_clip(stage, ffprobe, clip, request.duration)
                if old_item.get("asset"):
                    asset = AssetCandidate(**old_item["asset"])
                    if rejection_reason(asset, request):
                        raise ValueError("cached asset no longer allowed")
                    credits[asset.key] = asset.to_dict()
                    used.update((asset.key, asset.download_url))
                items.append(old_item)
                clips.append(clip)
                checkpoint()
                continue
            except (RuntimeError, ValueError, OSError):
                pass
        if beat.action in {"keep", "localize_text"}:
            if script_job:
                raise VisualRebuildError("script visual beat cannot use source footage")
            render(stage, ffmpeg, original, clip, request.duration, width, height, fps, "original", request.start)
            verify_clip(stage, ffprobe, clip, request.duration)
            item = {"index": request.index, "start": request.start, "end": request.end,
                    "narration": request.narration, "action": beat.action, "status": "kept_source",
                    "fallback": None, "asset": None, "clip_sha256": file_hash(clip)}
            items.append(item)
            clips.append(clip)
            checkpoint()
            continue
        selected = None
        rendered_animation = False
        if beat.action == "replace_animation" and beat.episode_index in animation_specs:
            try:
                render_animation(ffmpeg, clip, animation_specs[beat.episode_index], request.duration,
                                 width, height, fps)
                verify_clip(stage, ffprobe, clip, request.duration)
                rendered_animation = True
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
                report["errors"].append({"beat": request.index, "renderer": "animation",
                                         "error": type(error).__name__})
                clip.unlink(missing_ok=True)
                clip = clips_dir / f"beat-{request.index:04d}.mp4"
        if rendered_animation:
            items.append({"index": request.index, "start": request.start, "end": request.end,
                          "narration": request.narration, "action": beat.action, "status": "animation",
                          "fallback": None, "asset": None, "clip_sha256": file_hash(clip)})
            clips.append(clip)
            checkpoint()
            continue
        for provider in route(providers, request):
            try:
                candidates = provider.search(request)
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                report["errors"].append({"beat": request.index, "provider": provider.name, "error": type(error).__name__})
                continue
            accepted = []
            for asset in candidates:
                reason = rejection_reason(asset, request)
                if asset.provider != provider.name:
                    reason = "provider_mismatch"
                try:
                    validate_download_url(asset.download_url, allowed_hosts=provider.media_hosts)
                except RuntimeError:
                    reason = "untrusted_media_host"
                all_candidates.append({"beat": request.index, "asset": asset.to_dict(), "rejection": reason})
                if reason is None:
                    accepted.append(asset)
            for asset in rank(accepted, request, used, width, height):
                identity = fingerprint([asset.key, asset.download_url])
                material = media_dir / (identity + (".image" if asset.media_type == "image" else ".video"))
                try:
                    if not material.is_file():
                        download(asset.download_url, material, provider.media_hosts, asset.media_type)
                    validate_media_file(material, label="external media", maximum_bytes=
                                        50*1024*1024 if asset.media_type == "image" else stage._MAX_STOCK_BYTES)
                    probe = stage._probe(ffprobe, material)
                    validate_external(probe, asset.media_type)
                    render(stage, ffmpeg, material, clip, request.duration, width, height, fps, asset.media_type)
                    verify_clip(stage, ffprobe, clip, request.duration)
                    selected = asset
                    break
                except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
                    report["errors"].append({"beat": request.index, "asset": asset.key, "error": type(error).__name__})
                    # Never reuse a file that failed validation on the next run.
                    material.unlink(missing_ok=True)
                    clip.unlink(missing_ok=True)
            if selected:
                break
        if selected is None:
            fallback = "card" if script_job else "original"
            render(stage, ffmpeg, original, clip, request.duration, width, height, fps, fallback,
                   request.start)
            verify_clip(stage, ffprobe, clip, request.duration)
        else:
            used.update((selected.key, selected.download_url))
            credits[selected.key] = selected.to_dict()
        items.append({"index": request.index, "start": request.start, "end": request.end,
                      "narration": request.narration, "action": beat.action,
                      "status": "matched" if selected else "no_safe_match",
                      "fallback": None if selected else fallback,
                      "asset": selected.to_dict() if selected else None, "clip_sha256": file_hash(clip)})
        clips.append(clip)
        checkpoint()
    stage.report_progress(0.90, "Сборка нового видеоряда…")
    temporary = output.with_name("rebuilt-video.pending.mp4")
    stage._concat_clips(
        ffmpeg, clips, temporary,
        durations=[request.duration for request in requests],
    )
    verify_clip(stage, ffprobe, temporary, source_duration)
    temporary.replace(output)
    plan.update(complete=True, output_duration=stage._video_properties(stage._probe(ffprobe, output))[3])
    checkpoint()
    lines = [f"{a['provider']}: {a['author'] or 'Creator not specified'} — {a['page_url']}\n"
             f"License: {a['license_code']} {a['license_url']}\nChanges: cropped/scaled, trimmed or looped; audio omitted.\n"
             for a in credits.values()]
    (directory / "credits.txt").write_text("\n".join(lines), encoding="utf-8")
    manifest.artifacts.update(visual_plan=plan_path.relative_to(job_dir).as_posix(),
                             rebuild_video=output.relative_to(job_dir).as_posix(),
                             source_video=output.relative_to(job_dir).as_posix(),
                             visual_credits=(directory / "credits.json").relative_to(job_dir).as_posix())
    video_mode = str(manifest.metadata.get("video_mode") or "rebuild")
    manifest.metadata["video_mode"] = video_mode
    manifest.metadata["visual_rebuild"] = {"fingerprint": run_key, "providers": list(providers_by_name),
        "media_type_mode": stage._media_type_mode,
        "beat_count": len(requests), "unique_asset_count": len(credits), "duration": source_duration,
        "no_safe_match_count": sum(item["status"] == "no_safe_match" for item in items),
        "kept_source_count": sum(item["status"] == "kept_source" for item in items),
        "animation_count": sum(item["status"] == "animation" for item in items),
        "source_background_audio": video_mode != "rebuild"}
    manifest.completed_stages = [s for s in manifest.completed_stages
                                 if s not in {"avatar_overlay", "music_bed", "premiere_export"}]
    for key in ("avatar_video", "avatar_overlay_video", "avatar_base_video", "avatar_composite",
                "premiere_export", "premiere_xml"):
        manifest.artifacts.pop(key, None)
    for key in ("music_report", "music_bed", "music_plan", "music_candidates", "music_credits"):
        manifest.artifacts.pop(key, None)
    manifest.metadata.pop("avatar", None)
    manifest.metadata.pop("music_bed", None)
    manifest.metadata.pop("premiere_export", None)
    stage.report_progress(0.99, f"Видеоряд готов; без точного материала: {manifest.metadata['visual_rebuild']['no_safe_match_count']}")
    return manifest


def file_hash(path: Path) -> str:
    import hashlib
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_external(probe: dict, kind: str) -> None:
    if any(s.get("codec_type") in {"data", "attachment", "subtitle"} for s in probe.get("streams", [])):
        raise ProviderError("external media has forbidden streams")
    if kind == "image":
        # Still images have no duration/fps. Supply validation-only values after checking single image stream.
        streams = probe.get("streams", [])
        if len(streams) != 1 or streams[0].get("codec_name") not in {"mjpeg", "png"}:
            raise ProviderError("unsupported still image")
        probe = {"streams": [dict(streams[0], duration="1", avg_frame_rate="1/1")],
                 "format": dict(probe.get("format", {}), duration="1")}
    validate_probe_payload(probe, label="external media", require_video=True,
        maximum_duration=7200, allowed_video_codecs={"h264", "hevc", "vp8", "vp9", "theora", "mjpeg", "png"},
        allowed_format_tokens={"mov", "mp4", "matroska", "webm", "ogg", "image2", "jpeg_pipe", "png_pipe"})


def verify_clip(stage, ffprobe, path, duration):
    probe = stage._probe(ffprobe, path)
    stage._validate_media(probe, label="rendered clip", strict_stock=False)
    if abs(stage._video_properties(probe)[3] - duration) > 0.25:
        raise ProviderError("rendered duration mismatch")


def render(stage, ffmpeg, source, output, duration, width, height, fps, kind, start=0):
    args = [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-max_alloc", str(256*1024*1024)]
    trim_start = 0.0
    if kind == "card":
        args += ["-f", "lavfi", "-i", f"color=c=0x20242b:s={width}x{height}:r={fps:.6f}"]
    else:
        args += ["-protocol_whitelist", "file"]
        if kind == "image":
            args += ["-loop", "1", "-framerate", f"{fps:.6f}"]
        elif kind == "original":
            # Seeking exactly to the final frame can leave FFmpeg with an empty
            # MP4 container. Decode a short preroll, then trim accurately in the
            # filter graph; this is still fast for long source videos.
            input_start = max(0.0, start - 1.0)
            trim_start = start - input_start
            args += ["-ss", f"{input_start:.6f}"]
        else:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(source)]
    # Fit archive images without cutting off maps, captions or documents.
    filters = ""
    if kind == "original" and trim_start > 0:
        # Some web containers report the audio/container duration as longer
        # than the final video packet. Hold the last real frame across that
        # tiny tail so the rebuilt timeline remains gap-free.
        filters += (
            "tpad=stop_mode=clone:stop_duration=1,"
            f"trim=start={trim_start:.6f},setpts=PTS-STARTPTS,"
        )
    filters += f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps:.6f},setsar=1,format=yuv420p"
    args += ["-t", f"{duration:.6f}", "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
             "-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-video_track_timescale", "90000", "-movflags", "+faststart", str(output)]
    result = subprocess.run(args, capture_output=True, timeout=max(120, duration*12),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not output.is_file():
        details = result.stderr.decode("utf-8", errors="replace").strip()
        raise ProviderError(
            "FFmpeg could not prepare visual clip: " + (details[-2000:] or "no output")
        )
