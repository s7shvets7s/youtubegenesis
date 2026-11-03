from __future__ import annotations

import argparse
import json
from pathlib import Path

from scenedetect import SceneManager, open_video
from scenedetect.detectors import AdaptiveDetector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect local video shot boundaries")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=3.0)
    parser.add_argument("--minimum-shot-seconds", type=float, default=0.4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source video not found: {source}")
    if args.threshold <= 0:
        raise ValueError("threshold must be positive")
    if args.minimum_shot_seconds <= 0:
        raise ValueError("minimum shot duration must be positive")

    video = open_video(str(source))
    manager = SceneManager()
    manager.add_detector(
        AdaptiveDetector(
            adaptive_threshold=args.threshold,
            min_scene_len=args.minimum_shot_seconds,
        )
    )
    manager.detect_scenes(video=video, show_progress=False)
    scene_list = manager.get_scene_list(start_in_scene=True)
    scenes = [
        {
            "index": index,
            "start_frame": start.get_frames(),
            "end_frame": end.get_frames(),
            "start_seconds": start.get_seconds(),
            "end_seconds": end.get_seconds(),
        }
        for index, (start, end) in enumerate(scene_list, start=1)
    ]
    if not scenes:
        duration = video.duration
        scenes = [
            {
                "index": 1,
                "start_frame": 0,
                "end_frame": duration.get_frames(),
                "start_seconds": 0.0,
                "end_seconds": duration.get_seconds(),
            }
        ]

    payload = {
        "provider": "pyscenedetect",
        "detector": "adaptive",
        "threshold": args.threshold,
        "frame_rate": float(video.frame_rate),
        "scenes": scenes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps({"event": "complete", "shots": len(scenes)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
