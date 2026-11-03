# Contributing

Start with a focused issue or pull request describing the input, expected result,
and actual result. For bugs, include the Python/package versions, stage name, and a
minimal synthetic reproduction. Redact paths, tokens, channel names, scripts, voice
references, and provider responses. Do not attach a complete production run.

## Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . `
  -r requirements/console.txt -r requirements/editing.txt -r requirements/dev.txt
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m compileall -q src tests scripts/audit_public_release.py
.\.venv\Scripts\python.exe -m ruff check src tests scripts/audit_public_release.py
.\.venv\Scripts\python.exe -m bandit -r src -lll -iii
.\.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m coverage report
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/audit_public_release.py --history
```

Keep model/GPU dependencies in their dedicated environments. Tests must use
synthetic fixtures and mock external services unless explicitly marked as local
integration checks. Report skipped checks and unavailable models honestly.

Use typed Python 3.11+, `pathlib.Path`, argument-based subprocesses, and portable
relative manifest paths. Add processing steps through the `PipelineStage` contract
with stable persisted stage names. Preserve conservative media/license validation.
Update docs and relevant tests when changing CLI behavior, artifact layouts, or contracts.

The source tree contains no private channels or generated video assets. Keep those
directories ignored, and run the public-content audit before publishing changes.
