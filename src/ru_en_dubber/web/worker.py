"""A separate process owns the pipeline and its GPU subprocesses."""
import json
import sys
from pathlib import Path

from ru_en_dubber.cli import main
from ru_en_dubber.web.progress import FileProgress


def run() -> int:
    config = Path(sys.argv[1])
    progress = FileProgress(config.with_name("progress.json"))
    try:
        return main(json.loads(config.read_text(encoding="utf-8"))["argv"], progress=progress)
    except (Exception, SystemExit) as error:
        progress.fail_stage("startup", error)
        progress.finish_pipeline(False)
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
