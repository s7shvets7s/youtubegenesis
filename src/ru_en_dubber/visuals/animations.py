"""Strict animation planning and trusted declarative scene rendering."""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from ru_en_dubber.processes import stop_process

from ru_en_dubber.models import Manifest
from ru_en_dubber.pipeline.stage import PipelineStage
from ru_en_dubber.prompts import get_prompt, render_prompt
from ru_en_dubber.security import resolve_job_path
from ru_en_dubber.translation.qwen import QwenTranslationStage, TranslationExecutionError
from .models import fingerprint
from .transport import write_json


TEMPLATES = {"title", "process", "chart", "comparison", "counter", "map", "timeline"}
SCENE_ELEMENTS = {
    "text", "rectangle", "circle", "line", "arrow", "polyline", "counter", "progress"
}
MOTION_PROPERTIES = {"opacity", "x", "y", "scale", "draw", "value"}
EASINGS = {"linear", "ease_in", "ease_out", "ease_in_out", "overshoot"}
RENDERER_VERSION = "declarative-scenes-v1"


class AnimationError(RuntimeError):
    """Raised for an invalid animation plan or a failed trusted render."""


def _short_label(value: str, limit: int = 80) -> str:
    value = " ".join(value.split()).strip(" ,.;:-")
    if len(value) <= limit:
        return value
    prefix = value[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{prefix or value[: limit - 1]}…"


def fallback_spec(episode: dict[str, Any]) -> dict[str, Any]:
    intent = str(episode.get("visual_intent") or "abstract")
    template = {"map": "map", "number": "chart", "process": "process"}.get(intent, "title")
    subject = str(episode.get("subject") or episode.get("topic") or "Explainer")
    parts = re.split(r"[.!?;:]+|\b(?:and|versus|vs\.?|then)\b", subject, flags=re.IGNORECASE)
    items = []
    for part in parts:
        label = _short_label(part)
        if label and label not in items:
            items.append(label)
        if len(items) == 5:
            break
    items = items or [_short_label(subject) or "Explainer"]
    values = []
    for token in re.findall(r"-?\d+(?:\.\d+)?", subject):
        try:
            values.append(float(token))
        except ValueError:
            pass
    return validate_spec({
        "episode_index": int(episode["index"]),
        "template": template,
        "title": str(episode.get("topic") or subject)[:90],
        "subtitle": subject[:180],
        "items": items,
        "values": values[:5],
        "accent": "#49C6E5",
    })


def _validate_template_spec(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {"episode_index", "template", "title", "subtitle", "items", "values", "accent"}
    if set(value) != allowed:
        raise ValueError("invalid animation specification fields")
    episode_index = value["episode_index"]
    template = value["template"]
    if type(episode_index) is not int or episode_index < 1 or template not in TEMPLATES:
        raise ValueError("invalid animation episode or template")
    title, subtitle, accent = value["title"], value["subtitle"], value["accent"]
    if not isinstance(title, str) or not title.strip() or len(title) > 90:
        raise ValueError("invalid animation title")
    if not isinstance(subtitle, str) or len(subtitle) > 180:
        raise ValueError("invalid animation subtitle")
    if not isinstance(accent, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
        raise ValueError("invalid animation accent")
    items, values = value["items"], value["values"]
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError("invalid animation items")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 80 for item in items):
        raise ValueError("invalid animation item")
    if not isinstance(values, list) or len(values) > 8 or any(
        type(number) not in {int, float} or not math.isfinite(float(number)) or abs(float(number)) > 1e12
        for number in values
    ):
        raise ValueError("invalid animation values")
    return {"episode_index": episode_index, "template": template, "title": title.strip(),
            "subtitle": subtitle.strip(), "items": [item.strip() for item in items],
            "values": [float(number) for number in values], "accent": accent.upper()}


def _color(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"invalid animation {label}")
    return value.upper()


def _number(value: object, label: str, minimum: float, maximum: float) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"invalid animation {label}")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"invalid animation {label}")
    return result


def _validate_element(value: object, accent: str) -> dict[str, Any]:
    allowed = {
        "id", "type", "text", "x", "y", "x2", "y2", "width", "height",
        "color", "fill_color", "stroke_width", "font_scale", "points", "value",
        "max_value", "opacity", "z_index",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("invalid animation element fields")
    element_id = value.get("id")
    kind = value.get("type")
    if not isinstance(element_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", element_id):
        raise ValueError("invalid animation element id")
    if kind not in SCENE_ELEMENTS:
        raise ValueError("invalid animation element type")
    x = _number(value.get("x"), "element x", 0.0, 1.0)
    y = _number(value.get("y"), "element y", 0.0, 1.0)
    width = _number(value.get("width", 0.1), "element width", 0.005, 1.0)
    height = _number(value.get("height", 0.1), "element height", 0.005, 1.0)
    text = value.get("text", "")
    if not isinstance(text, str) or len(text) > 160 or (kind == "text" and not text.strip()):
        raise ValueError("invalid animation element text")
    x2 = _number(value.get("x2", x), "element x2", 0.0, 1.0)
    y2 = _number(value.get("y2", y), "element y2", 0.0, 1.0)
    if kind in {"line", "arrow"} and x == x2 and y == y2:
        raise ValueError("animation line endpoints must differ")
    points = value.get("points", [])
    if not isinstance(points, list) or len(points) > 16:
        raise ValueError("invalid animation element points")
    normalized_points = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("invalid animation point")
        normalized_points.append([
            _number(point[0], "point x", 0.0, 1.0),
            _number(point[1], "point y", 0.0, 1.0),
        ])
    if kind == "polyline" and len(normalized_points) < 2:
        raise ValueError("animation polyline requires at least two points")
    current_value = _number(value.get("value", 0.0), "element value", -1e12, 1e12)
    maximum = _number(value.get("max_value", 1.0), "element maximum", 1e-9, 1e12)
    return {
        "id": element_id,
        "type": kind,
        "text": text.strip(),
        "x": x,
        "y": y,
        "x2": x2,
        "y2": y2,
        "width": width,
        "height": height,
        "color": _color(value.get("color", accent), "element color"),
        "fill_color": _color(value.get("fill_color", "#20252B"), "element fill color"),
        "stroke_width": int(_number(value.get("stroke_width", 2), "stroke width", 1, 20)),
        "font_scale": _number(value.get("font_scale", 1.0), "font scale", 0.4, 3.0),
        "points": normalized_points,
        "value": current_value,
        "max_value": maximum,
        "opacity": _number(value.get("opacity", 1.0), "element opacity", 0.0, 1.0),
        "z_index": int(_number(value.get("z_index", 0), "z index", -20, 20)),
    }


def _validate_motion(value: object, elements: dict[str, dict[str, Any]]) -> dict[str, Any]:
    allowed = {"target", "property", "start", "end", "from", "to", "easing"}
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError("invalid animation motion fields")
    target = value["target"]
    prop = value["property"]
    if target not in elements or prop not in MOTION_PROPERTIES or value["easing"] not in EASINGS:
        raise ValueError("invalid animation motion target, property or easing")
    start = _number(value["start"], "motion start", 0.0, 1.0)
    end = _number(value["end"], "motion end", 0.0, 1.0)
    if end <= start:
        raise ValueError("animation motion must have positive duration")
    limits = {
        "opacity": (0.0, 1.0),
        "x": (-0.25, 1.25),
        "y": (-0.25, 1.25),
        "scale": (0.05, 4.0),
        "draw": (0.0, 1.0),
        "value": (-1e12, 1e12),
    }[prop]
    if prop == "draw" and elements[target]["type"] not in {"line", "arrow", "polyline", "progress"}:
        raise ValueError("draw motion requires a drawable element")
    if prop == "value" and elements[target]["type"] not in {"counter", "progress"}:
        raise ValueError("value motion requires a counter or progress element")
    return {
        "target": target,
        "property": prop,
        "start": start,
        "end": end,
        "from": _number(value["from"], "motion from", *limits),
        "to": _number(value["to"], "motion to", *limits),
        "easing": value["easing"],
    }


def _validate_scene_spec(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "episode_index", "kind", "title", "subtitle", "background", "accent",
        "grid", "elements", "motions",
    }
    if set(value) != allowed or value.get("kind") != "scene":
        raise ValueError("invalid declarative animation fields")
    episode_index = value["episode_index"]
    if type(episode_index) is not int or episode_index < 1:
        raise ValueError("invalid animation episode")
    title, subtitle = value["title"], value["subtitle"]
    if not isinstance(title, str) or not title.strip() or len(title) > 90:
        raise ValueError("invalid animation title")
    if not isinstance(subtitle, str) or len(subtitle) > 180:
        raise ValueError("invalid animation subtitle")
    if type(value["grid"]) is not bool:
        raise ValueError("invalid animation grid setting")
    background = _color(value["background"], "background")
    accent = _color(value["accent"], "accent")
    raw_elements = value["elements"]
    if not isinstance(raw_elements, list) or not 1 <= len(raw_elements) <= 16:
        raise ValueError("declarative animation requires 1-16 elements")
    elements = [_validate_element(item, accent) for item in raw_elements]
    by_id = {item["id"]: item for item in elements}
    if len(by_id) != len(elements):
        raise ValueError("animation element ids must be unique")
    raw_motions = value["motions"]
    if not isinstance(raw_motions, list) or not 1 <= len(raw_motions) <= 48:
        raise ValueError("declarative animation requires 1-48 motions")
    motions = [_validate_motion(item, by_id) for item in raw_motions]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for motion in motions:
        grouped.setdefault((motion["target"], motion["property"]), []).append(motion)
    for sequence in grouped.values():
        ordered = sorted(sequence, key=lambda item: (item["start"], item["end"]))
        if any(current["start"] < previous["end"] for previous, current in zip(ordered, ordered[1:])):
            raise ValueError("animation motions for one property cannot overlap")
    return {
        "episode_index": episode_index,
        "kind": "scene",
        "title": title.strip(),
        "subtitle": subtitle.strip(),
        "background": background,
        "accent": accent,
        "grid": value["grid"],
        "elements": sorted(elements, key=lambda item: item["z_index"]),
        "motions": sorted(motions, key=lambda item: (item["start"], item["end"])),
    }


def validate_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid animation specification")
    if value.get("kind") == "scene":
        return _validate_scene_spec(value)
    return _validate_template_spec(value)


def parse_agent_plan(value: object, episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"animations"} or not isinstance(value["animations"], list):
        raise ValueError("invalid animation plan")
    expected = {int(item["index"]) for item in episodes}
    specs = [validate_spec(item) for item in value["animations"]]
    if {item["episode_index"] for item in specs} != expected or len(specs) != len(expected):
        raise ValueError("animation plan does not cover each requested episode exactly once")
    return sorted(specs, key=lambda item: item["episode_index"])


def parse_agent_spec(value: object, episode_index: int) -> dict[str, Any]:
    if isinstance(value, dict) and set(value) == {"animation"}:
        value = value["animation"]
    if not isinstance(value, dict):
        raise ValueError("invalid animation object")
    bound = dict(value)
    bound["episode_index"] = episode_index
    return validate_spec(bound)


class AnimationPlanStage(PipelineStage):
    def __init__(self, *, mode: str = "templates", server: str | None = None,
                 model: Path | None = None, gpu_layers: int = 99) -> None:
        if mode not in {"off", "templates", "agent"}:
            raise ValueError("animation mode must be off, templates or agent")
        self.mode, self.server, self.model, self.gpu_layers = mode, server, model, gpu_layers

    @property
    def name(self) -> str:
        return "animation_plan"

    def run(self, manifest: Manifest, job_dir: Path) -> Manifest:
        job_dir = job_dir.resolve()
        relative = manifest.artifacts.get("episode_plan")
        if not relative:
            raise AnimationError("animation planning requires an episode plan")
        episode_path = resolve_job_path(job_dir, relative, label="episode plan")
        try:
            episode_plan = json.loads(episode_path.read_text(encoding="utf-8"))
            episodes = [item for item in episode_plan["episodes"] if item.get("action") == "replace_animation"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AnimationError("episode plan is invalid") from error
        directory = job_dir / "visual-rebuild" / "animations"
        directory.mkdir(parents=True, exist_ok=True)
        transcript = [{"id": item.id, "start": item.start, "end": item.end, "text": item.en}
                      for item in manifest.segments]
        model_path = self.model.expanduser().resolve() if self.model else None
        model_identity = ([str(model_path), model_path.stat().st_size, model_path.stat().st_mtime_ns]
                          if model_path and model_path.is_file() else None)
        key = fingerprint([RENDERER_VERSION, self.mode, model_identity, transcript, episodes])
        output = directory / "animation-plan.json"
        if output.is_file():
            try:
                saved = json.loads(output.read_text(encoding="utf-8"))
                if saved.get("fingerprint") == key:
                    manifest.artifacts["animation_plan"] = output.relative_to(job_dir).as_posix()
                    manifest.metadata["animation_plan"] = saved["summary"]
                    return manifest
            except (OSError, ValueError, KeyError):
                pass
        specs, fallback_reason = self.plan_specs(transcript, episodes, directory)
        summary = {"mode": self.mode, "requested_count": len(episodes), "planned_count": len(specs),
                   "fallback_reason": fallback_reason, "renderer_version": RENDERER_VERSION}
        write_json(output, {"version": 2, "fingerprint": key, "summary": summary, "animations": specs})
        for spec in specs:
            write_json(directory / f"episode-{spec['episode_index']:04d}-spec.json", spec)
        manifest.artifacts["animation_plan"] = output.relative_to(job_dir).as_posix()
        manifest.metadata["animation_plan"] = summary
        for downstream in ("visual_rebuild", "avatar_overlay", "music_bed", "premiere_export"):
            if downstream in manifest.completed_stages:
                manifest.completed_stages.remove(downstream)
        for artifact in ("visual_plan", "rebuild_video", "visual_credits", "avatar_video", "avatar_overlay_video",
                         "avatar_base_video", "avatar_composite", "premiere_export", "premiere_xml"):
            manifest.artifacts.pop(artifact, None)
        manifest.metadata.pop("avatar", None)
        for artifact in ("music_report", "music_bed", "music_plan", "music_candidates", "music_credits"):
            manifest.artifacts.pop(artifact, None)
        manifest.metadata.pop("music_bed", None)
        self.report_progress(0.99, f"Animation plan: {len(specs)}")
        return manifest

    def plan_specs(
        self, transcript: list[dict[str, Any]], episodes: list[dict[str, Any]], directory: Path,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if self.mode == "off" or not episodes:
            return [], None
        specs = [fallback_spec(item) for item in episodes]
        if self.mode == "agent":
            try:
                return self._agent_specs(transcript, episodes, directory), None
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                return specs, type(error).__name__
        return specs, None

    def _agent_specs(self, transcript: list[dict[str, Any]], episodes: list[dict[str, Any]],
                     directory: Path) -> list[dict[str, Any]]:
        if not self.server or not self.model or not self.model.is_file():
            raise AnimationError("local animation agent is unavailable")
        runtime = QwenTranslationStage(server=self.server, model=self.model, gpu_layers=self.gpu_layers,
                                       context_size=32768)
        executable, port = runtime._resolve_executable(self.server), runtime._reserve_port()
        process, log = runtime._start_server(executable, self.model.resolve(), port, directory / "agent.log")
        try:
            runtime._wait_until_ready(process, port, directory / "agent.log")
            results = []
            response_log = directory / "agent-responses.jsonl"
            for episode in episodes:
                expected_id = int(episode["index"])
                prompt = render_prompt(
                    "animation.user",
                    required=("episode_index", "payload_json"),
                    episode_index=expected_id,
                    payload_json=json.dumps(
                        {
                            "complete_transcript": transcript,
                            "animation_episode": episode,
                            "style": get_prompt("animation.style"),
                        },
                        ensure_ascii=False,
                    ),
                )
                last_error: Exception | None = None
                for attempt in range(3):
                    raw = runtime._chat_completion(
                        port, prompt, temperature=0.35, max_tokens=3500,
                        system=get_prompt("animation.system"),
                    )
                    with response_log.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"episode_index": expected_id, "attempt": attempt + 1,
                                                 "response": raw}, ensure_ascii=False) + "\n")
                    try:
                        try:
                            payload = runtime._parse_json_payload(raw, "animation")
                        except TranslationExecutionError:
                            payload = runtime._parse_json_payload(raw, "template")
                        results.append(parse_agent_spec(payload, expected_id))
                        break
                    except (ValueError, KeyError, TypeError, RuntimeError) as error:
                        last_error = error
                        prompt += "\n" + get_prompt("animation.retry")
                else:
                    raise AnimationError(f"animation agent returned invalid JSON for episode {expected_id}: {last_error}")
            return results
        finally:
            runtime._stop_server(process)
            log.close()


def render_animation(ffmpeg: str, output: Path, spec: dict[str, Any], duration: float,
                     width: int, height: int, fps: float) -> None:
    """Render frames in trusted code and stream raw pixels directly to FFmpeg."""
    if not 0 < duration <= 60 or not 16 <= width <= 4096 or not 16 <= height <= 2160 or not 1 <= fps <= 60.1:
        raise AnimationError("animation render limits exceeded")
    import cv2
    import numpy as np

    checked = validate_spec(spec)
    command = [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
               "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-video_track_timescale", "90000", "-movflags", "+faststart", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.stdin is not None
    frames = max(1, round(duration * fps))
    try:
        for index in range(frames):
            progress = min(1.0, index / max(1, frames - 1))
            frame = _draw_frame(np, cv2, checked, progress, width, height)
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr else b""
        if process.stderr:
            process.stderr.close()
        timeout = max(120, duration * 12)
        if process.wait(timeout=timeout) != 0 or not output.is_file():
            raise AnimationError("FFmpeg animation render failed: " + stderr.decode(errors="replace")[-500:])
    except subprocess.TimeoutExpired as error:
        stop_process(process, grace_seconds=0)
        output.unlink(missing_ok=True)
        raise AnimationError(
            f"FFmpeg animation render timed out after {timeout:.0f}s"
        ) from error
    except BaseException:
        if process.poll() is None:
            stop_process(process, grace_seconds=0)
        output.unlink(missing_ok=True)
        raise


def _draw_frame(np, cv2, spec: dict[str, Any], progress: float, width: int, height: int):
    if spec.get("kind") == "scene":
        return _draw_scene_frame(np, cv2, spec, progress, width, height)
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (24, 22, 19)
    accent = tuple(int(spec["accent"][offset:offset + 2], 16) for offset in (5, 3, 1))
    margin, baseline = max(24, width // 18), max(30, height // 14)
    for x in range(0, width, max(40, width // 16)):
        cv2.line(frame, (x, 0), (x, height), (31, 30, 28), 1)
    for y in range(0, height, max(40, height // 10)):
        cv2.line(frame, (0, y), (width, y), (31, 30, 28), 1)
    cv2.putText(frame, spec["title"], (margin, baseline * 2), cv2.FONT_HERSHEY_DUPLEX,
                max(0.7, width / 1250), (242, 242, 238), 2, cv2.LINE_AA)
    cv2.putText(frame, spec["subtitle"], (margin, baseline * 3), cv2.FONT_HERSHEY_SIMPLEX,
                max(0.42, width / 2100), (170, 172, 174), 1, cv2.LINE_AA)
    items, template = spec["items"], spec["template"]
    shown = max(1, math.ceil(progress * len(items)))
    left, top, bottom = margin, baseline * 4, height - baseline
    if template in {"chart", "comparison", "counter"}:
        values = spec["values"] or [float(i + 1) for i in range(len(items))]
        values = (values + [values[-1]] * len(items))[:len(items)]
        maximum = max(1.0, max(abs(value) for value in values))
        row = max(34, (bottom - top) // max(1, len(items)))
        for i, (label, value) in enumerate(zip(items[:shown], values[:shown])):
            y = top + i * row
            bar = int((width - margin * 3) * abs(value) / maximum * min(1.0, progress * 1.4))
            cv2.rectangle(frame, (left + margin * 2, y), (left + margin * 2 + bar, y + row // 2), accent, -1)
            cv2.putText(frame, label, (left, y + row // 2), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.38, width / 2400), (225, 225, 220), 1, cv2.LINE_AA)
    elif template in {"process", "timeline"}:
        y, span = (top + bottom) // 2, (width - margin * 2) / max(1, len(items) - 1)
        for i, label in enumerate(items[:shown]):
            x = int(margin + i * span)
            if i:
                cv2.line(frame, (int(margin + (i - 1) * span), y), (x, y), accent, 4)
            cv2.circle(frame, (x, y), max(9, width // 90), accent, -1)
            cv2.putText(frame, label[:18], (max(margin, x - 50), y + 45), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.35, width / 2600), (225, 225, 220), 1, cv2.LINE_AA)
    elif template == "map":
        points = []
        for i, label in enumerate(items):
            x = int(margin + (width - margin * 2) * (i + 1) / (len(items) + 1))
            y = int(top + (bottom - top) * (0.25 + 0.5 * ((i * 37) % 100) / 100))
            points.append((x, y, label))
        for i, (x, y, label) in enumerate(points[:shown]):
            if i:
                cv2.line(frame, points[i - 1][:2], (x, y), accent, 4)
            cv2.circle(frame, (x, y), max(8, width // 100), accent, -1)
            cv2.putText(frame, label[:20], (x + 12, y - 12), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.35, width / 2600), (225, 225, 220), 1, cv2.LINE_AA)
    else:
        cv2.rectangle(frame, (margin, top), (int(margin + (width - margin * 2) * progress), top + 8), accent, -1)
        for i, label in enumerate(items[:shown]):
            cv2.putText(frame, label, (margin, top + baseline * (i + 2)), cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.55, width / 1800), (225, 225, 220), 1, cv2.LINE_AA)
    return frame


def _bgr(color: str) -> tuple[int, int, int]:
    return tuple(int(color[offset:offset + 2], 16) for offset in (5, 3, 1))


def _ease(value: float, easing: str) -> float:
    value = max(0.0, min(1.0, value))
    if easing == "ease_in":
        return value * value
    if easing == "ease_out":
        return 1.0 - (1.0 - value) ** 2
    if easing == "ease_in_out":
        return value * value * (3.0 - 2.0 * value)
    if easing == "overshoot":
        shifted = value - 1.0
        return 1.0 + 2.2 * shifted**3 + 1.2 * shifted**2
    return value


def _element_state(element: dict[str, Any], motions: list[dict[str, Any]], progress: float) -> dict[str, float]:
    state = {
        "x": element["x"],
        "y": element["y"],
        "scale": 1.0,
        "opacity": element["opacity"],
        "draw": 1.0,
        "value": element["value"],
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for motion in motions:
        if motion["target"] == element["id"]:
            grouped.setdefault(motion["property"], []).append(motion)
    for prop, sequence in grouped.items():
        sequence.sort(key=lambda item: (item["start"], item["end"]))
        state[prop] = sequence[0]["from"]
        for motion in sequence:
            if progress < motion["start"]:
                break
            if progress >= motion["end"]:
                state[prop] = motion["to"]
                continue
            amount = (progress - motion["start"]) / (motion["end"] - motion["start"])
            amount = _ease(amount, motion["easing"])
            state[prop] = motion["from"] + (motion["to"] - motion["from"]) * amount
            break
    return state


def _partial_points(points: list[tuple[int, int]], draw: float) -> list[tuple[int, int]]:
    if len(points) < 2 or draw <= 0:
        return []
    if draw >= 1:
        return points
    lengths = [math.dist(points[index - 1], points[index]) for index in range(1, len(points))]
    target = sum(lengths) * draw
    result = [points[0]]
    for index, length in enumerate(lengths, start=1):
        if target >= length:
            result.append(points[index])
            target -= length
            continue
        if length > 0:
            left, right = points[index - 1], points[index]
            fraction = target / length
            result.append((
                round(left[0] + (right[0] - left[0]) * fraction),
                round(left[1] + (right[1] - left[1]) * fraction),
            ))
        break
    return result


def _draw_text(cv2, image, text: str, center: tuple[int, int], scale: float,
               color: tuple[int, int, int], thickness: int) -> None:
    lines = text.splitlines() or [text]
    font = cv2.FONT_HERSHEY_DUPLEX
    line_height = max(14, round(34 * scale))
    start_y = center[1] - line_height * (len(lines) - 1) // 2
    for index, line in enumerate(lines[:4]):
        size = cv2.getTextSize(line, font, scale, thickness)[0]
        origin = (center[0] - size[0] // 2, start_y + index * line_height + size[1] // 2)
        cv2.putText(image, line, origin, font, scale, color, thickness, cv2.LINE_AA)


def _draw_scene_element(cv2, overlay, element: dict[str, Any], state: dict[str, float],
                        width: int, height: int) -> None:
    x, y = round(state["x"] * width), round(state["y"] * height)
    scale = state["scale"]
    element_width = max(2, round(element["width"] * width * scale))
    element_height = max(2, round(element["height"] * height * scale))
    color, fill = _bgr(element["color"]), _bgr(element["fill_color"])
    thickness = element["stroke_width"]
    kind = element["type"]
    if kind == "text":
        text_scale = max(0.3, width / 1400 * element["font_scale"] * scale)
        _draw_text(cv2, overlay, element["text"], (x, y), text_scale, color, thickness)
    elif kind == "rectangle":
        left, top = x - element_width // 2, y - element_height // 2
        right, bottom = x + element_width // 2, y + element_height // 2
        cv2.rectangle(overlay, (left, top), (right, bottom), fill, -1, cv2.LINE_AA)
        cv2.rectangle(overlay, (left, top), (right, bottom), color, thickness, cv2.LINE_AA)
    elif kind == "circle":
        radius = max(2, min(element_width, element_height) // 2)
        cv2.circle(overlay, (x, y), radius, fill, -1, cv2.LINE_AA)
        cv2.circle(overlay, (x, y), radius, color, thickness, cv2.LINE_AA)
    elif kind in {"line", "arrow"}:
        end = (round(element["x2"] * width), round(element["y2"] * height))
        endpoint = (round(x + (end[0] - x) * state["draw"]),
                    round(y + (end[1] - y) * state["draw"]))
        if kind == "arrow":
            cv2.arrowedLine(overlay, (x, y), endpoint, color, thickness, cv2.LINE_AA, tipLength=0.08)
        else:
            cv2.line(overlay, (x, y), endpoint, color, thickness, cv2.LINE_AA)
    elif kind == "polyline":
        points = [(round(px * width), round(py * height)) for px, py in element["points"]]
        visible = _partial_points(points, state["draw"])
        for index in range(1, len(visible)):
            cv2.line(overlay, visible[index - 1], visible[index], color, thickness, cv2.LINE_AA)
    elif kind == "counter":
        label = f"{element['text']} " if element["text"] else ""
        number = state["value"]
        rendered = f"{number:,.0f}" if abs(number - round(number)) < 0.001 else f"{number:,.1f}"
        text_scale = max(0.4, width / 1000 * element["font_scale"] * scale)
        _draw_text(cv2, overlay, label + rendered, (x, y), text_scale, color, thickness)
    elif kind == "progress":
        left, top = x - element_width // 2, y - element_height // 2
        right, bottom = x + element_width // 2, y + element_height // 2
        cv2.rectangle(overlay, (left, top), (right, bottom), fill, -1, cv2.LINE_AA)
        amount = max(0.0, min(1.0, state["value"] / element["max_value"])) * state["draw"]
        cv2.rectangle(overlay, (left, top), (round(left + element_width * amount), bottom), color, -1, cv2.LINE_AA)
        cv2.rectangle(overlay, (left, top), (right, bottom), color, thickness, cv2.LINE_AA)


def _draw_scene_frame(np, cv2, spec: dict[str, Any], progress: float, width: int, height: int):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = _bgr(spec["background"])
    if spec["grid"]:
        grid_color = tuple(round(channel * 0.7) for channel in _bgr(spec["background"]))
        for x in range(0, width, max(48, width // 16)):
            cv2.line(frame, (x, 0), (x, height), grid_color, 1)
        for y in range(0, height, max(48, height // 10)):
            cv2.line(frame, (0, y), (width, y), grid_color, 1)
    header_alpha = min(1.0, progress / 0.10) if progress > 0 else 0.0
    if header_alpha:
        overlay = frame.copy()
        _draw_text(cv2, overlay, spec["title"], (width // 2, max(28, height // 11)),
                   max(0.45, width / 1500), (242, 242, 238), 2)
        if spec["subtitle"]:
            _draw_text(cv2, overlay, spec["subtitle"], (width // 2, max(52, height // 7)),
                       max(0.3, width / 2300), (176, 178, 180), 1)
        cv2.addWeighted(overlay, header_alpha, frame, 1.0 - header_alpha, 0, frame)
    for element in spec["elements"]:
        state = _element_state(element, spec["motions"], progress)
        opacity = max(0.0, min(1.0, state["opacity"]))
        if opacity <= 0.001:
            continue
        overlay = frame.copy()
        _draw_scene_element(cv2, overlay, element, state, width, height)
        cv2.addWeighted(overlay, opacity, frame, 1.0 - opacity, 0, frame)
    return frame
