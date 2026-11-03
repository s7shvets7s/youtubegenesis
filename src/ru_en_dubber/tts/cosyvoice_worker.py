"""Isolated CosyVoice 3 worker with the same per-segment output contract as Chatterbox."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from ru_en_dubber.prompts import get_prompt, render_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CosyVoice 3 pipeline worker")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser


def _instruction(style: str, intensity: float) -> str:
    strength = "subtle" if intensity < 0.34 else "moderate" if intensity < 0.67 else "strong"
    return render_prompt(
        "tts.cosyvoice3.wrapper",
        required=("style", "intensity"),
        style=get_prompt(f"tts.cosyvoice3.styles.{style}"),
        intensity=get_prompt(f"tts.cosyvoice3.intensities.{strength}"),
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import AutoModel

    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CosyVoice CUDA was requested but is unavailable")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if not args.reference.is_file() or not (args.model_dir / "cosyvoice3.yaml").is_file():
        raise FileNotFoundError("CosyVoice reference or local model directory is missing")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("TTS input has no segments")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    loaded = time.perf_counter()
    model = AutoModel(model_dir=str(args.model_dir.resolve()), fp16=device == "cuda")
    load_seconds = time.perf_counter() - loaded
    results: list[dict[str, Any]] = []
    generation_started = time.perf_counter()
    for index, item in enumerate(segments, start=1):
        segment_id = int(item["id"])
        spoken = str(item["text"]).strip()
        target_seconds = float(item["target_seconds"])
        style = str(item["style"])
        intensity = float(item["intensity"])
        if segment_id < 1 or not spoken or target_seconds <= 0:
            raise ValueError(f"Invalid TTS segment at index {index}")
        torch.manual_seed(20260831 + segment_id)
        started = time.perf_counter()
        chunks = [
            output["tts_speech"].detach().cpu()
            for output in model.inference_instruct2(
                spoken, _instruction(style, intensity), str(args.reference.resolve()),
                stream=False,
            )
        ]
        if not chunks:
            raise RuntimeError(f"CosyVoice returned no audio for segment {segment_id}")
        waveform = torch.cat(chunks, dim=-1)
        duration = waveform.shape[-1] / model.sample_rate
        if duration <= 0:
            raise RuntimeError(f"CosyVoice returned empty audio for segment {segment_id}")
        filename = f"{segment_id:06d}.wav"
        destination = args.output_dir / filename
        temporary = args.output_dir / f".{filename}.tmp.wav"
        torchaudio.save(str(temporary), waveform, model.sample_rate)
        temporary.replace(destination)
        results.append({
            "id": segment_id, "text": spoken, "style": style,
            "intensity": intensity, "target_seconds": round(target_seconds, 3),
            "duration_seconds": round(duration, 3),
            "generation_seconds": round(time.perf_counter() - started, 3),
            "device": device, "path": destination.relative_to(args.report.parent).as_posix(),
        })
        print(json.dumps({"event": "segment_complete", "id": segment_id,
                          "completed": index, "total": len(segments)}), flush=True)
    _write_json(args.report, {
        "provider": "cosyvoice3", "model": "Fun-CosyVoice3-0.5B",
        "requested_device": args.device, "device": device,
        "reference": args.reference.name, "sample_rate": model.sample_rate,
        "load_seconds": round(load_seconds, 3),
        "generation_seconds": round(time.perf_counter() - generation_started, 3),
        "segments": results,
    })
    print(json.dumps({"event": "complete", "segments": len(results)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
