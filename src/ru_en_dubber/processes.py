from __future__ import annotations

import subprocess


def stop_process(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 10.0,
) -> None:
    """Stop a child process and always reap it before returning."""
    if process.poll() is not None:
        process.wait()
        return
    if grace_seconds > 0:
        try:
            process.terminate()
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass
    process.wait()
