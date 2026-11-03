"""Check wheel/sdist contents without extracting or executing package files."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from audit_public_release import MAX_BLOB_BYTES, content_problems, path_problem


REQUIRED_WHEEL = {
    "ru_en_dubber/cli.py", "ru_en_dubber/agent_prompts.json",
    "ru_en_dubber/web/static/index.html", "ru_en_dubber/web/static/app.js",
    "ru_en_dubber/web/static/style.css",
}
REQUIRED_SOURCE = {
    "pyproject.toml", "LICENSE", "README.md", "requirements/dev.txt",
    "config/.env.example", "tests/fixtures/ad_sponsor_boundaries.json",
    "scripts/audit_public_release.py", "scripts/check_distribution.py",
}


def check(directory: Path) -> list[str]:
    failures: list[str] = []
    archives = sorted([*directory.glob("*.whl"), *directory.glob("*.tar.gz")])
    if not any(p.suffix == ".whl" for p in archives) or not any(
        p.name.endswith(".tar.gz") for p in archives
    ):
        failures.append("Expected both a wheel and source distribution")
    for path in archives:
        names: set[str] = set()

        def inspect(name: str, size: int, read) -> None:
            names.add(name)
            problem = path_problem(name)
            if problem:
                failures.append(f"{path.name}: {name}: {problem}")
            if size > MAX_BLOB_BYTES:
                failures.append(f"{path.name}: {name}: unexpectedly large file")
                return
            for finding in content_problems(read()):
                failures.append(f"{path.name}: {name}: {finding}")

        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                for item in archive.infolist():
                    if not item.is_dir():
                        inspect(item.filename, item.file_size, lambda: archive.read(item))
            required = REQUIRED_WHEEL
        else:
            with tarfile.open(path, "r:gz") as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    parts = PurePosixPath(member.name).parts
                    if len(parts) < 2 or not member.isfile():
                        failures.append(f"{path.name}: unexpected archive member")
                        continue
                    stream = archive.extractfile(member)
                    assert stream is not None
                    with stream:
                        inspect("/".join(parts[1:]), member.size, stream.read)
            required = REQUIRED_SOURCE
        for missing in sorted(required - names):
            failures.append(f"{path.name}: missing required file {missing}")
        print(f"{path.name}: inspected {len(names)} files")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    failures = check(args.directory)
    for finding in failures:
        print(f"FAIL: {finding}")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
