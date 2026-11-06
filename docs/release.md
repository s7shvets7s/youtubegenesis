# Beta release status

This release packages the local dubbing pipeline, CLI, Web UI, and automated tests.
It excludes private production content, local agent workflows, credentials, voice
references, model weights, downloaded binaries, and unlicensed optional plugins.

Local preparation checks on November 4, 2026 used Python 3.14.3: **215 tests passed,
72% branch-aware coverage**, Ruff passed, no high-severity/high-confidence Bandit
findings, and `pip check` passed. Wheel and source archive content checks passed;
the installed wheel's imports, CLI parser, and workspace selection were smoke-tested.
These results do not establish a clean Python 3.12 install or a completed GitHub CI run.

## Verification scope

Automated quality gates run Python compilation, Ruff, high-severity Bandit,
unittest with a 50% coverage floor, dependency consistency, a public-content audit,
and wheel-content checks. The coverage floor is a regression gate, not a claim of
complete coverage. Provider tests use fixtures and mocks; selected speech/media
tests execute FFmpeg when available.

Manual acceptance is still required for:

- A clean install on the intended Windows/NVIDIA driver configuration.
- A short WhisperX → Qwen → TTS run with the chosen models and voice reference.
- Live media/music APIs, their attribution, and unavailable-provider fallbacks.
- An exported package opened in the target Adobe Premiere version, including
  timing, separate tracks, relinkable media, captions, and ad-removal boundaries.
- Listening and viewing review of the full output.

The Web UI is in English by default with a Russian toggle; some runtime pipeline
progress messages are still Russian. CUDA, model setup, live APIs, and
Premiere imports are not exercised by GitHub CI. The project has no automated
whole-channel research or Google Flow integration. MuseTalk setup follows an
external checkout and is not a complete reproducible lock.

## Before publishing

Run the checks in [Contributing](../.github/CONTRIBUTING.md), inspect the resulting
wheel and source tree, and run:

```powershell
python scripts/audit_public_release.py --history
```

This audit examines all reachable Git objects, including remote-tracking and
non-branch refs. It rejects private workspace paths, media, recognizable secret
formats, machine-specific user paths, and unexpectedly large blobs. It cannot
recognize every personal name or paraphrased private detail; manual review matters.

The prepared public history starts with one anonymous root commit. Keep any local
backup of the original history private. Publish only the reviewed branch; never
use `git push --mirror` or upload the entire working directory.

If an earlier version was already pushed, local cleanup alone does not remove
GitHub history, tags, pull-request references, caches, forks, or other people's
clones. Review remote refs, replace the intended branch with an explicit
`--force-with-lease` if needed, remove obsolete public refs, and follow GitHub's
sensitive-data removal procedure where applicable. Rotate any exposed credentials.
Publication and remote cleanup require a separate verified operation.
