from __future__ import annotations

import gc
import os
import time
from typing import Any

import torch
from chatterbox.mtl_tts import ChatterboxMultilingualTTS


def configure_device(device: str) -> None:
    if device == "cpu":
        torch.set_num_threads(max(1, min(os.cpu_count() or 1, 12)))


def resolve_device(requested: str) -> str:
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA was requested, but this PyTorch build cannot access it. "
                "Use --device cpu or install the CUDA TTS requirements."
            )
        return "cuda"
    if torch.cuda.is_available():
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        print(
            "CUDA detected: "
            f"{torch.cuda.get_device_name(0)} "
            f"({free_bytes / 1024**3:.1f}/{total_bytes / 1024**3:.1f} GiB free)",
            flush=True,
        )
        return "cuda"
    print("CUDA is unavailable; using CPU.", flush=True)
    return "cpu"


def is_cuda_failure(error: BaseException) -> bool:
    message = str(error).lower()
    markers = (
        "cuda",
        "cublas",
        "cudnn",
        "out of memory",
        "device-side",
        "gpu",
        "driver",
    )
    return isinstance(error, MemoryError) or any(marker in message for marker in markers)


def release_cuda() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_model(model_name: str, device: str) -> tuple[Any, float]:
    configure_device(device)
    print(
        f"Loading Chatterbox Multilingual {model_name.upper()} on {device.upper()}...",
        flush=True,
    )
    started = time.perf_counter()
    model = ChatterboxMultilingualTTS.from_pretrained(
        # Chatterbox compares this value with the literal "cpu" while loading
        # CUDA-authored checkpoints, so keep the device as a string.
        device=device,
        t3_model=model_name,
    )
    if device == "cuda":
        torch.cuda.synchronize()
    return model, time.perf_counter() - started
