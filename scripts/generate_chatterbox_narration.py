from __future__ import annotations

import argparse
import json
import re
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a long Chatterbox narration in sentence-sized chunks."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--device", choices=("auto", "cpu", "cuda"), default="auto"
    )
    parser.add_argument("--model", choices=("v2", "v3"), default="v3")
    parser.add_argument("--max-chars", type=int, default=220)
    parser.add_argument("--pause-ms", type=int, default=280)
    parser.add_argument("--exaggeration", type=float, default=0.5)
    parser.add_argument("--cfg-weight", type=float, default=0.5)
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Reuse saved parts whose text matches the previous JSON report.",
    )
    return parser.parse_args()


def split_text(text: str, max_chars: int) -> list[str]:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
        if sentence.strip()
    ]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def main() -> int:
    args = parse_args()
    reference = args.reference.resolve()
    text_file = args.text_file.resolve()
    output = args.output.resolve()
    if not reference.is_file():
        raise FileNotFoundError(f"Voice reference not found: {reference}")
    if not text_file.is_file():
        raise FileNotFoundError(f"Text file not found: {text_file}")
    if not 80 <= args.max_chars <= 400:
        raise ValueError("max-chars must be between 80 and 400")
    if not 0 <= args.pause_ms <= 2000:
        raise ValueError("pause-ms must be between 0 and 2000")

    text = text_file.read_text(encoding="utf-8").strip()
    chunks = split_text(text, args.max_chars)
    if not chunks:
        raise ValueError("Text file is empty")

    output.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = output.parent / f"{output.stem}.parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    report_path = output.with_suffix(".json")
    previous_chunks: dict[int, str] = {}
    if args.reuse_existing and report_path.is_file():
        previous_report = json.loads(report_path.read_text(encoding="utf-8"))
        previous_chunks = {
            int(item["index"]): str(item["text"])
            for item in previous_report.get("chunks", [])
        }
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
    print(f"Model loaded in {load_seconds:.1f} s; chunks: {len(chunks)}", flush=True)

    pause = torch.zeros(1, round(model.sr * args.pause_ms / 1000))
    combined: list[torch.Tensor] = []
    chunk_reports = []
    total_started = time.perf_counter()
    for index, chunk in enumerate(chunks, start=1):
        print(f"[{index}/{len(chunks)}] {chunk}", flush=True)
        part_path = parts_dir / f"{index:03d}.wav"
        reused = (
            args.reuse_existing
            and part_path.is_file()
            and previous_chunks.get(index) == chunk
        )
        if reused:
            waveform, sample_rate = torchaudio.load(str(part_path))
            if sample_rate != model.sr or waveform.shape[0] != 1:
                raise ValueError(
                    f"Cannot reuse {part_path}: expected mono {model.sr} Hz audio"
                )
            elapsed = 0.0
        else:
            torch.manual_seed(20260831 + index)
            started = time.perf_counter()
            try:
                waveform = model.generate(
                    chunk,
                    language_id="en",
                    audio_prompt_path=str(reference),
                    exaggeration=args.exaggeration,
                    cfg_weight=args.cfg_weight,
                )
                if device == "cuda":
                    torch.cuda.synchronize()
            except Exception as error:
                if (
                    args.device != "auto"
                    or device != "cuda"
                    or not is_cuda_failure(error)
                ):
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
                    chunk,
                    language_id="en",
                    audio_prompt_path=str(reference),
                    exaggeration=args.exaggeration,
                    cfg_weight=args.cfg_weight,
                )
            waveform = waveform.cpu()
            elapsed = time.perf_counter() - started
            torchaudio.save(str(part_path), waveform, model.sr)
        duration = waveform.shape[-1] / model.sr
        combined.append(waveform)
        if index < len(chunks):
            combined.append(pause)
        chunk_reports.append(
            {
                "index": index,
                "text": chunk,
                "duration_seconds": round(duration, 3),
                "generation_seconds": round(elapsed, 3),
                "realtime_factor": round(elapsed / duration, 3),
                "reused": reused,
                "device": "reused" if reused else device,
                "path": str(part_path.relative_to(output.parent)),
            }
        )
        if reused:
            print(f"Chunk {index}: reused {duration:.1f} s audio", flush=True)
        else:
            print(f"Chunk {index}: {duration:.1f} s audio in {elapsed:.1f} s", flush=True)

    narration = torch.cat(combined, dim=-1)
    torchaudio.save(str(output), narration, model.sr)
    generation_seconds = time.perf_counter() - total_started
    duration_seconds = narration.shape[-1] / model.sr
    report = {
        "provider": "chatterbox",
        "model": f"multilingual-{args.model}",
        "requested_device": args.device,
        "device": device,
        "fallback_reason": fallback_reason,
        "reference": str(reference),
        "text_file": str(text_file),
        "output": str(output),
        "sample_rate": model.sr,
        "load_seconds": round(load_seconds, 3),
        "duration_seconds": round(duration_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "realtime_factor": round(generation_seconds / duration_seconds, 3),
        "pause_ms": args.pause_ms,
        "chunks": chunk_reports,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Saved {output}: {duration_seconds:.1f} s audio in "
        f"{generation_seconds:.1f} s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
