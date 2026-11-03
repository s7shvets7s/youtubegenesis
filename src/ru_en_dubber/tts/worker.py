from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import torch
import torchaudio

from ru_en_dubber.tts.runtime import (
    is_cuda_failure,
    load_model,
    release_cuda,
    resolve_device,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chatterbox pipeline worker")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--model", choices=("v2", "v3"), default="v3")
    parser.add_argument("--exaggeration", type=float, default=0.5)
    parser.add_argument("--cfg-weight", type=float, default=0.5)
    return parser


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    report_path = args.report.resolve()
    reference = args.reference.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"TTS input not found: {input_path}")
    if not reference.is_file():
        raise FileNotFoundError(f"Voice reference not found: {reference}")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("TTS input has no segments")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    device = resolve_device(args.device)
    fallback_reason: str | None = None
    try:
        model, load_seconds = load_model(args.model, device)
    except Exception as error:
        if args.device != "auto" or device != "cuda" or not is_cuda_failure(error):
            raise
        fallback_reason = str(error)
        emit({"event": "fallback", "reason": fallback_reason})
        release_cuda()
        device = "cpu"
        model, load_seconds = load_model(args.model, device)

    results: list[dict[str, Any]] = []
    generation_started = time.perf_counter()
    for index, item in enumerate(segments, start=1):
        segment_id = int(item["id"])
        text = str(item["text"]).strip()
        target_seconds = float(item["target_seconds"])
        if segment_id < 1 or not text or target_seconds <= 0:
            raise ValueError(f"Invalid TTS segment at index {index}")

        torch.manual_seed(20260831 + segment_id)
        started = time.perf_counter()
        try:
            waveform = model.generate(
                text,
                language_id="en",
                audio_prompt_path=str(reference),
                exaggeration=args.exaggeration,
                cfg_weight=args.cfg_weight,
            )
            if device == "cuda":
                torch.cuda.synchronize()
        except Exception as error:
            if args.device != "auto" or device != "cuda" or not is_cuda_failure(error):
                raise
            fallback_reason = str(error)
            emit({"event": "fallback", "reason": fallback_reason})
            del model
            release_cuda()
            device = "cpu"
            model, extra_load_seconds = load_model(args.model, device)
            load_seconds += extra_load_seconds
            torch.manual_seed(20260831 + segment_id)
            started = time.perf_counter()
            waveform = model.generate(
                text,
                language_id="en",
                audio_prompt_path=str(reference),
                exaggeration=args.exaggeration,
                cfg_weight=args.cfg_weight,
            )

        waveform = waveform.cpu()
        elapsed = time.perf_counter() - started
        duration = waveform.shape[-1] / model.sr
        filename = f"{segment_id:06d}.wav"
        output_path = output_dir / filename
        temporary_path = output_dir / f".{filename}.tmp.wav"
        torchaudio.save(str(temporary_path), waveform, model.sr)
        temporary_path.replace(output_path)
        relative_path = output_path.relative_to(report_path.parent)
        results.append(
            {
                "id": segment_id,
                "text": text,
                "target_seconds": round(target_seconds, 3),
                "duration_seconds": round(duration, 3),
                "generation_seconds": round(elapsed, 3),
                "realtime_factor": round(elapsed / duration, 3),
                "device": device,
                "path": relative_path.as_posix(),
            }
        )
        emit(
            {
                "event": "segment_complete",
                "id": segment_id,
                "completed": index,
                "total": len(segments),
            }
        )

    generation_seconds = time.perf_counter() - generation_started
    report = {
        "provider": "chatterbox",
        "model": f"multilingual-{args.model}",
        "requested_device": args.device,
        "device": device,
        "fallback_reason": fallback_reason,
        "reference": str(reference),
        "sample_rate": model.sr,
        "load_seconds": round(load_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "exaggeration": args.exaggeration,
        "cfg_weight": args.cfg_weight,
        "segments": results,
    }
    write_json(report_path, report)
    emit({"event": "complete", "segments": len(results)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
