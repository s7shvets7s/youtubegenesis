# Installation

Run commands from the repository root in PowerShell. Windows 10/11 is the target;
the automated workflow uses Python 3.12. A basic UI install does not install models.
Keep the environments below separate because their Python, PyTorch, and CUDA
requirements differ. Do not combine every requirements file into one environment.

## Main environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . `
  -r requirements/console.txt `
  -r requirements/downloader.txt `
  -r requirements/editing.txt
```

Provide FFmpeg and FFprobe in `PATH` or `.tools/ffmpeg/bin/`. For Demucs on NVIDIA,
install the dedicated CUDA wheels before the separation requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/torch-cu128.txt
.\.venv\Scripts\python.exe -m pip install -r requirements/separation.txt
```

The checked-in pins describe the project's environment; they are not a complete
lockfile. Verify driver and wheel compatibility on the target machine.

## WhisperX

The supplied Windows CUDA requirements use CPython 3.12:

```powershell
py -3.12 -m venv .venv-whisperx
.\.venv-whisperx\Scripts\python.exe -m pip install -r requirements/whisperx.txt
```

Diarization additionally requires access to `pyannote/speaker-diarization-community-1`.
Accept its terms, then log in interactively with a read-only Hugging Face token:

```powershell
$env:HF_HOME = "$PWD\.models\huggingface"
$env:HF_HUB_CACHE = "$env:HF_HOME\hub"
.\.venv-whisperx\Scripts\hf.exe auth login
```

Never place the token in a command, README, fixture, or manifest.

## Qwen through llama.cpp

Provide these defaults or override them using `--llama-server`,
`--translation-model`, and `--vision-projector`:

```text
.tools/llama.cpp/llama-server.exe
.models/qwen/Qwen3VL-4B-Instruct-Q4_K_M.gguf
.models/qwen/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf
```

The prompt catalog is `src/ru_en_dubber/agent_prompts.json`.
`RU_EN_DUBBER_PROMPTS_FILE` selects a custom catalog. Models are not bundled.

## Chatterbox

The supplied CUDA requirements use Python 3.12 and a pinned upstream revision:

```powershell
py -3.12 -m venv .venv-chatterbox
.\.venv-chatterbox\Scripts\python.exe -m pip install -r requirements/tts-cuda.txt
```

Use `requirements/tts.txt` for the CPU dependency route. Provide a voice reference
at `voices/my_voice/reference.wav` or set `--tts-reference`. Use only voices you
have permission to synthesize. A separate CosyVoice worker adapter exists; no
tested standalone CosyVoice installation recipe is supplied in this release.

## Optional MuseTalk avatar

MuseTalk uses Python 3.10 in `.tools/python310/python.exe`, or an explicit installer
argument. Review `scripts/setup_musetalk.ps1` before running it: it installs
dependencies, clones upstream, and downloads model weights.

```powershell
.\scripts\setup_musetalk.ps1
```

It creates `.venv-musetalk` and `.tools/MuseTalk`. A short front-facing motion video
is required. No avatar or identity sample is distributed.

## Optional provider keys

```powershell
Copy-Item config/.env.example .env
```

The loader accepts only `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `NARA_API_KEY`, and
`JAMENDO_CLIENT_ID`; it does not execute the file. NARA live access is disabled
pending storage-policy resolution. Missing keys disable their corresponding
optional sources. Translation and synthesis do not require these provider keys.

Preflight checks executable/model paths, required inputs, write access, and free
space (10 GiB by default; `--minimum-free-gb` changes it). It does not validate an
entire GPU runtime. Run a short media job on the target machine before relying on it.
