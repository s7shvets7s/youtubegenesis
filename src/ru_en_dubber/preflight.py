from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


class PreflightError(RuntimeError):
    """Raised when a long-running pipeline cannot start safely."""


@dataclass(frozen=True, slots=True)
class PreflightRequirements:
    executables: tuple[tuple[str, str], ...] = ()
    files: tuple[tuple[str, Path], ...] = ()
    work_directory: Path = Path("jobs")
    minimum_free_bytes: int = 0


def run_preflight(requirements: PreflightRequirements) -> None:
    """Validate static runtime inputs and leave no probe artifacts behind."""
    problems: list[str] = []
    for label, command in requirements.executables:
        if _resolve_executable(command) is None:
            problems.append(f"{label}: executable not found ({command})")
    for label, path in requirements.files:
        candidate = path.expanduser()
        if not candidate.is_file():
            problems.append(f"{label}: file not found ({candidate})")

    work_directory = requirements.work_directory.expanduser().resolve()
    try:
        work_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".preflight-", dir=work_directory, delete=True
        ) as probe:
            probe.write(b"ok")
            probe.flush()
        free_bytes = shutil.disk_usage(work_directory).free
        if free_bytes < requirements.minimum_free_bytes:
            required_gib = requirements.minimum_free_bytes / (1024**3)
            available_gib = free_bytes / (1024**3)
            problems.append(
                "job storage: "
                f"{available_gib:.1f} GiB free, at least {required_gib:.1f} GiB required"
            )
    except OSError as error:
        problems.append(f"job storage is not writable ({work_directory}): {error}")

    if problems:
        details = "\n- ".join(problems)
        raise PreflightError(f"runtime requirements are not satisfied:\n- {details}")


def _resolve_executable(command: str) -> Path | None:
    candidate = Path(command).expanduser()
    if candidate.parent != Path("."):
        return candidate.resolve() if candidate.is_file() else None
    resolved = shutil.which(command)
    return Path(resolved).resolve() if resolved else None
