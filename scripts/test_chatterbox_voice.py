from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
import torchaudio

from chatterbox_runtime import (
    is_cuda_failure,
    load_model,
    release_cuda,
    resolve_device,
)


PRESETS = {
    "neutral": {
        "text": "Welcome back. Today we are testing a new English voice for the video.",
        "exaggeration": 0.5,
        "cfg_weight": 0.5,
    },
    "energetic": {
        "text": "Great! Everything is working, and now we can move on to the next part.",
        "exaggeration": 0.75,
        "cfg_weight": 0.3,
    },
    "calm": {
        "text": "Take your time. We will check each result carefully and choose the best version.",
        "exaggeration": 0.25,
        "cfg_weight": 0.6,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate short Chatterbox voice-cloning samples."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--device", choices=("auto", "cpu", "cuda"), default="auto"
    )
    parser.add_argument("--model", choices=("v2", "v3"), default="v3")
    parser.add_argument(
        "--preset",
        choices=tuple(PRESETS),
        action="append",
        help="Generate only this preset; repeat to select several.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reference = args.reference.resolve()
    output_dir = args.output_dir.resolve()
    if not reference.is_file():
        raise FileNotFoundError(f"Voice reference not found: {reference}")

    output_dir.mkdir(parents=True, exist_ok=True)
    selected = args.preset or list(PRESETS)
    device = resolve_device(args.device)
    fallback_reason = None
    try:
        model, load_seconds = load_model(args.model, device)
    except Exception as error:
        if args.device != "auto" or device != "cuda" or not is_cuda_failure(error):
            raise
        fallback_reason = str(error)
        print(f"CUDA failed while loading; falling back to CPU: {error}", flush=True)
        release_cuda()
        device = "cpu"
        model, load_seconds = load_model(args.model, device)
    print(f"Model loaded in {load_seconds:.1f} s", flush=True)

    results = []
    for index, preset_name in enumerate(selected, start=1):
        preset = PRESETS[preset_name]
        torch.manual_seed(20260831 + index)
        print(
            f"[{index}/{len(selected)}] Generating {preset_name}: {preset['text']}",
            flush=True,
        )
        started = time.perf_counter()
        try:
            waveform = model.generate(
                preset["text"],
                language_id="en",
                audio_prompt_path=str(reference),
                exaggeration=preset["exaggeration"],
                cfg_weight=preset["cfg_weight"],
            )
            if device == "cuda":
                torch.cuda.synchronize()
        except Exception as error:
            if args.device != "auto" or device != "cuda" or not is_cuda_failure(error):
                raise
            fallback_reason = str(error)
            print(f"CUDA failed; retrying on CPU: {error}", flush=True)
            del model
            release_cuda()
            device = "cpu"
            model, extra_load_seconds = load_model(args.model, device)
            load_seconds += extra_load_seconds
            torch.manual_seed(20260831 + index)
            started = time.perf_counter()
            waveform = model.generate(
                preset["text"],
                language_id="en",
                audio_prompt_path=str(reference),
                exaggeration=preset["exaggeration"],
                cfg_weight=preset["cfg_weight"],
            )
        elapsed = time.perf_counter() - started
        output_path = output_dir / f"{preset_name}.wav"
        torchaudio.save(str(output_path), waveform.cpu(), model.sr)
        duration = waveform.shape[-1] / model.sr
        results.append(
            {
                "preset": preset_name,
                "text": preset["text"],
                "path": output_path.name,
                "duration_seconds": round(duration, 3),
                "generation_seconds": round(elapsed, 3),
                "realtime_factor": round(elapsed / duration, 3),
                "exaggeration": preset["exaggeration"],
                "cfg_weight": preset["cfg_weight"],
                "device": device,
            }
        )
        print(
            f"Saved {output_path.name}: {duration:.1f} s audio in {elapsed:.1f} s",
            flush=True,
        )

    report = {
        "provider": "chatterbox",
        "model": f"multilingual-{args.model}",
        "requested_device": args.device,
        "device": device,
        "fallback_reason": fallback_reason,
        "reference": str(reference),
        "sample_rate": model.sr,
        "load_seconds": round(load_seconds, 3),
        "results": results,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
