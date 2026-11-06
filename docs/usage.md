# Usage and outputs

Start the Web UI from the repository root:

```powershell
.\.venv\Scripts\python.exe -m ru_en_dubber.web.server --open
```

The service binds to loopback at port 8765. Create a channel and video in the
catalog first. Select their run directory when using the low-level CLI; the CLI
does not create channel/video metadata itself.

## Video input

```powershell
.\.venv\Scripts\python.exe -m ru_en_dubber.cli input.mp4 `
  --jobs-dir "channels/<channel-folder>/videos/<video-folder>/runs" `
  --video-mode original --ad-removal mark
```

A YouTube URL can replace `input.mp4`. Use media you are entitled to process.
`scripts/START_PIPELINE.cmd` is a legacy interactive launcher: it writes to `jobs/`
and requires WhisperX CUDA and diarization access. Prefer the Web UI for managed
channel/video runs.

| Video mode | Behavior |
| --- | --- |
| `original` | Preserve source frames, subject to the configured ad-removal policy |
| `source-clean` | Keep useful source material; replace confirmed presenter scenes |
| `hybrid` | Default; also replace selected explanatory scenes |
| `rebuild` | Rebuild the picture from planned material |

`--visual-media-type image|mixed|video` limits replacements. An unavailable allowed
asset does not authorize a different type. Review visual and licensing reports.
The default `--ad-removal remove --ad-confidence 0.85` ripple-removes confirmed
ranges; use `mark` for a review pass or `off` to disable detection/removal.

## English script input

```powershell
.\.venv\Scripts\python.exe -m ru_en_dubber.cli `
  --script narration.txt --audio-only `
  --jobs-dir "channels/<channel-folder>/videos/<video-folder>/runs"
```

UTF-8 `.txt`, `.md`, and timed `.srt` inputs skip download, separation,
transcription, and translation. Without `--audio-only`, script jobs rebuild visuals
and export an editing package. TTS setup is still required. Audio-only jobs require
FFmpeg/FFprobe while speech postprocessing is enabled.

In mixed media mode, the semantic planner can select process beats for local animation.
Use `--animation-mode templates` for template graphics, `agent` for Qwen-generated
animation specs with template fallback, or `off` to use media search for those beats.

Speech defaults to `--speech-postprocess speech --speech-target-lufs -18
--speech-true-peak-db -2`. Bypass with `--speech-postprocess off`. Raw provider audio
is preserved; see [processing details](P6-speech-postprocess.md).

## Optional layers

Use `--avatar-mode musetalk --avatar-source avatar-motion.mp4` for a local avatar.
Music modes are `off`, `local`, and `hybrid` (the default). Hybrid searches approved
local tracks first, then uses Jamendo if configured. Without safe music, export
continues with `no_safe_music`. Original background and added music remain separate;
their combination requires an editorial decision.

## Run artifacts

```text
channels/<channel-folder>/videos/<video-folder>/runs/<run-id>/
  manifest.json
  ad-removal.json
  content-analysis/
  tts/
  speech-postprocess/
  visual-rebuild/
  music/
  avatar/
  premiere-export/
```

Only enabled stages produce their directories. `manifest.json` is the source of
truth; artifact paths remain relative to this run. Channel/video metadata supplies
display names; immutable slugs and stable IDs supply identity. Worker logs live in
`.runtime/runs/<operation-id>/worker.log`, separate from durable production content.

## Recovery

```powershell
.\.venv\Scripts\python.exe -m ru_en_dubber.cli --resume-job JOB_ID --jobs-dir RUNS_DIR
.\.venv\Scripts\python.exe -m ru_en_dubber.cli --translate-job JOB_ID --jobs-dir RUNS_DIR
.\.venv\Scripts\python.exe -m ru_en_dubber.cli --tts-job JOB_ID --jobs-dir RUNS_DIR
.\.venv\Scripts\python.exe -m ru_en_dubber.cli --premiere-export-job JOB_ID --jobs-dir RUNS_DIR
```

These are defined entry points with required upstream artifacts, not arbitrary
instruction-level resume. Preserve a failed run and read its manifest/error log
before retrying. Do not hand-edit artifact paths or copy individual outputs between runs.
