"""Audit Git content without printing matched secrets or private text.

Uses only Python's standard library and Git. Ignored files are never uploaded or
included implicitly. Optional private literals are supplied through a local JSON
array, not command-line arguments, and are never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath


MAX_BLOB_BYTES = 2 * 1024 * 1024
PRIVATE_ROOTS = {
    ".codex", ".runtime", ".models", ".tools", "graphify-out",
    "channels", "jobs", "voices", "avatars", "voice-tests",
    "channel-branding", "branding", "brand", "2d-video",
}
PRIVATE_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".aac",
    ".ogg", ".flac", ".zip", ".7z", ".bundle", ".gguf", ".safetensors",
    ".pt", ".pth", ".pem", ".key", ".p12", ".pfx",
}
CONTENT_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "provider token": re.compile(
        rb"(?<![A-Za-z0-9_])(?:hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{25,}"
        rb"|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{25,}"
        rb"|AKIA[A-Z0-9]{16})(?![A-Za-z0-9_])"
    ),
    "machine user path": re.compile(
        rb"(?:[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-][^\s\"'<>/\\]*"
        rb"|/Users/[A-Za-z0-9_.-][^\s/\"']*|/home/[A-Za-z0-9_.-][^\s/\"']*)", re.IGNORECASE
    ),
}


def git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=input,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120,
    ).stdout


def path_problem(name: str) -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts:
        return "unsafe path"
    first = parts[0].lower()
    if first in PRIVATE_ROOTS or first.startswith((".venv", "tmp", "temp")):
        return "private workspace directory"
    if first.endswith("-branding") or path.name.lower() == "agents.md":
        return "local production instructions or branding"
    if path.name.startswith(".env") and name != "config/.env.example":
        return "local environment file"
    if path.suffix.lower() in PRIVATE_SUFFIXES:
        return "private media, model, archive, or key"
    if first == "source" and len(parts) > 2 and parts[1] == "music":
        if name not in {"source/music/README.md", "source/music/catalog.json"}:
            return "local music asset"
    if name == "scripts/recover_capcut_motionblur.py":
        return "local recovery tool"
    if first == "docs" and ("discovery" in path.name or path.name.endswith("feasibility.md")):
        return "private research notes"
    if name.startswith("docs/templates/"):
        return "local production templates"
    return None


def content_problems(data: bytes, private_values: tuple[bytes, ...] = ()) -> list[str]:
    problems = [label for label, pattern in CONTENT_PATTERNS.items() if pattern.search(data)]
    folded = data.lower()
    if any(value.lower() in folded for value in private_values):
        problems.append("local private literal")
    return problems


def audit(root: Path, *, history: bool, private_values: tuple[bytes, ...] = ()) -> tuple[int, list[str]]:
    paths: set[str] = set()
    catalog_objects: set[str] = set()
    objects: set[str] = set()
    if history:
        # Include tags, remote refs, and tool refs, not just the current branch.
        lines = git(root, "rev-list", "--objects", "--all").decode("utf-8").splitlines()
        for line in lines:
            oid, _, name = line.partition(" ")
            objects.add(oid)
            if name:
                paths.add(name)
                if name == "source/music/catalog.json":
                    catalog_objects.add(oid)
    else:
        for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            objects.add(metadata.split()[1].decode("ascii"))
            paths.add(name.decode("utf-8"))
            if name == b"source/music/catalog.json":
                catalog_objects.add(metadata.split()[1].decode("ascii"))

    failures = [f"{name}: {problem}" for name in sorted(paths)
                if (problem := path_problem(name))]
    if not objects:
        return 0, failures + ["No Git objects found; audit did not run."]
    query = "\n".join(sorted(objects)).encode("ascii") + b"\n"
    sizes = git(root, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", input=query)
    accepted = []
    for line in sizes.decode("ascii").splitlines():
        oid, kind, size = line.split()
        if kind not in {"blob", "commit", "tag"}:
            continue
        if int(size) > MAX_BLOB_BYTES:
            failures.append(f"{oid[:12]}: object exceeds {MAX_BLOB_BYTES} bytes")
        else:
            accepted.append((oid, kind))
    # Read one bounded object at a time; never dump matches to output.
    for oid, kind in accepted:
        data = git(root, "cat-file", kind, oid)
        for problem in content_problems(data, private_values):
            failures.append(f"{oid[:12]} ({kind}): {problem}")
        if kind == "blob":
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeError):
                continue
            # A populated local music catalog must never enter the source release.
            if oid in catalog_objects and isinstance(payload, dict) and payload.get("tracks"):
                failures.append("source/music/catalog.json: populated private music catalog")
    return len(accepted), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="inspect every reachable Git object")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--private-values-file", type=Path, help="local JSON array of private literals")
    args = parser.parse_args()
    private_values: tuple[bytes, ...] = ()
    if args.private_values_file:
        values = json.loads(args.private_values_file.read_text(encoding="utf-8"))
        if not isinstance(values, list) or any(not isinstance(v, str) or len(v) < 4 for v in values):
            parser.error("private values must be a JSON array of strings at least four characters long")
        private_values = tuple(value.encode("utf-8") for value in values)
    count, failures = audit(args.root.resolve(), history=args.history, private_values=private_values)
    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"Public content audit: {count} objects checked, {len(failures)} findings.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
