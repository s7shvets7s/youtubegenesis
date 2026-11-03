# Architecture

The package processes one source video or one English script per job. It does not
automatically research channels, competitors, comments, CTR, retention, or outliers.

`cli.py` composes sequential `PipelineStage` adapters. `pipeline/runner.py` persists
progress, completed stages, and failure evidence in the manifest. Model contracts
live in `models/`; `workspace.py` manages channels, videos, research locations, and runs.

| Module | Responsibility |
| --- | --- |
| `ingest`, `separation`, `transcription` | yt-dlp, FFmpeg, Demucs, WhisperX |
| `translation`, `content` | Qwen adaptation, content/visual analysis, text localization |
| `tts`, `editing/timing.py` | Speech workers, mastering, dialogue placement |
| `visuals` | Planning, provider routing, licensing, animations, visual rebuild |
| `avatar`, `music` | Optional MuseTalk overlay and music bed |
| `editing/premiere.py` | FCP 7 XML, media, and editable track export |
| `web` | Loopback standard-library server and worker orchestration |
| `security`, `processes`, `preflight` | Path/media validation, process bounds, runtime checks |

Script jobs start at synthesis. The editable prompt catalog is
`src/ru_en_dubber/agent_prompts.json`; keep new model prompt prose there rather than
embedding it in Python call sites.

## Boundaries

- Manifest writes are atomic; artifact references are relative to a run.
- Resolve artifacts through the security helpers; never trust paths or URLs from models,
  provider responses, manifests, or downloaded files.
- Subprocesses use argument lists, bounded execution, and `shell=False`.
- Ambiguous visual analysis retains source material. Unsafe or unverifiable assets
  become `no_safe_match`; optional-provider failures must not corrupt a job.
- Downloaded visuals and music retain license evidence and attribution.
- The Web UI binds to loopback, validates requests, and separates worker state from content.

Tests use fixtures and mocks for heavyweight models and providers. Integration
contracts and small FFmpeg checks complement them; they cannot prove full CUDA,
live-service, or Premiere behavior. See [release status](release.md).
