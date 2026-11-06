"""Local semantic planning with strict JSON validation and a conservative fallback."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.request import Request, urlopen

from ru_en_dubber.prompts import get_prompt
from ru_en_dubber.translation.qwen import QwenTranslationStage
from .models import INTENTS, VisualRequest, fingerprint
from .transport import write_json


def fallback_request(beat, legacy: bool = False) -> VisualRequest:
    return VisualRequest(beat.index, beat.start, beat.end, beat.narration,
                         beat.search_query, visual_intent="atmosphere" if legacy else "abstract",
                         accuracy="illustrative" if legacy else "exact",
                         media_types=("video",) if legacy else ("video", "image"))


def parse_plan(value: dict, base: VisualRequest) -> VisualRequest:
    allowed = {"domain", "visual_intent", "subject", "entities", "location", "year_from", "year_to", "accuracy", "media_types"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("planner returned unknown fields")
    if not {"subject", "visual_intent", "accuracy", "entities"} <= value.keys():
        raise ValueError("incomplete visual request")
    for key in ("subject", "domain", "visual_intent", "accuracy"):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 500):
            raise ValueError("invalid planner text")
    entities = value["entities"]
    if not isinstance(entities, list) or len(entities) > 8 or any(not isinstance(e, str) or not e.strip() or len(e) > 150 for e in entities):
        raise ValueError("invalid entities")
    types = value.get("media_types", ["video", "image"])
    if not isinstance(types, list) or any(not isinstance(t, str) for t in types):
        raise ValueError("invalid media types")
    location = value.get("location")
    if location is not None and (not isinstance(location, str) or len(location) > 150):
        raise ValueError("invalid location")
    parsed = dict(value, entities=tuple(entities), media_types=tuple(types))
    if parsed["visual_intent"] not in INTENTS:
        parsed["visual_intent"] = "abstract"
        parsed["accuracy"] = "exact"
    result = replace(base, **parsed)
    if result.visual_intent in {"event", "person", "number", "document", "map"}:
        result = replace(result, accuracy="exact")
    return result


def ground_generated_plan(value: dict, base: VisualRequest) -> dict:
    """Do not carry dates from neighboring topics into the current narration."""
    parsed = parse_plan(value, base)
    result = {k: v for k, v in parsed.to_dict().items() if k not in {"index", "start", "end", "narration"}}
    result["entities"] = [e for e in parsed.entities if not re.fullmatch(r"\d+", e)]
    for key in ("year_from", "year_to"):
        year = result[key]
        if year is not None and not re.search(rf"\b{year}\b", base.narration):
            result[key] = None
    location = result["location"]
    if location and (location.casefold() not in base.narration.casefold() or not location[0].isupper()):
        result["location"] = None
    result["media_types"] = [t for t in parsed.media_types if t != "chart" or parsed.visual_intent == "number"]
    if not result["media_types"]:
        result["media_types"] = ["video", "image"]
    return result


class VisualPlanner:
    def __init__(self, mode="auto", server=None, model=None, gpu_layers=99):
        self.mode, self.server, self.model, self.gpu_layers = mode, server, model, gpu_layers
        self.events = []

    def plan(self, beats, directory: Path, progress=None) -> list[VisualRequest]:
        base = [fallback_request(b, self.mode == "legacy") for b in beats]
        model = Path(self.model) if self.model else None
        model_identity = [str(model), model.stat().st_size, model.stat().st_mtime_ns] if model and model.is_file() else None
        key = fingerprint(["planner-v2", self.mode, model_identity, [r.to_dict() for r in base]])
        cache = directory / "planner-cache.json"
        entries = {}
        if cache.is_file():
            try:
                saved = json.loads(cache.read_text(encoding="utf-8"))
                if saved.get("fingerprint") == key:
                    entries = saved.get("entries", {})
            except (OSError, ValueError):
                pass
        if self.mode == "legacy":
            return base
        if not self.server or not model or not model.is_file():
            self.events.append({"status": "model_unavailable", "fallback": "no_safe_match"})
            return base
        runtime = QwenTranslationStage(server=self.server, model=model, gpu_layers=self.gpu_layers)
        process, log = None, None
        results = []
        try:
            port = runtime._reserve_port()
            for i, request in enumerate(base):
                cached = entries.get(str(request.index))
                if cached:
                    try:
                        results.append(parse_plan(cached, request))
                        continue
                    except ValueError:
                        pass
                if process is None:
                    executable = runtime._resolve_executable(self.server)
                    process, log = runtime._start_server(executable, model, port, directory / "visual-planner.log")
                    runtime._wait_until_ready(process, port, directory / "visual-planner.log")
                if progress:
                    progress(0.01 + 0.02 * i / len(base), f"Semantic plan: {i + 1}/{len(base)}")
                context = [r.narration for r in base[max(0, i-1):i+2]]
                try:
                    payload = ground_generated_plan(self._request(port, request.narration, context), request)
                    planned = parse_plan(payload, request)
                except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError) as error:
                    self.events.append({"beat": request.index, "status": type(error).__name__, "fallback": "no_safe_match"})
                    results.append(request)
                    continue
                entries[str(request.index)] = payload
                write_json(cache, {"fingerprint": key, "entries": entries})
                results.append(planned)
        except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError) as error:
            self.events.append({"status": type(error).__name__, "fallback": "no_safe_match"})
            results.extend(base[len(results):])
        finally:
            if process is not None:
                runtime._stop_server(process)
            if log is not None:
                log.close()
        return results

    @staticmethod
    def _request(port: int, narration: str, context: list[str]) -> dict:
        system = get_prompt("visual_planner.system")
        payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps({"current": narration, "context": context})}],
                   "temperature": 0, "max_tokens": 600, "stream": False,
                   "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}}
        req = Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=90) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("planner response too large")
            content = json.loads(raw)["choices"][0]["message"]["content"]
        return json.loads(content)
