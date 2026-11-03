# Speech postprocessing

Generated voice now passes through `SpeechPostprocessStage` (`speech_postprocess`) after TTS
and before timing, subtitles, music, avatar work, and export. The CLI uses it for both CosyVoice 3
and Chatterbox, including audio-only / CapCut jobs, TTS-only jobs, and existing-job re-exports.
The Web UI exposes the same settings in its speech group. Custom Python pipeline compositions
must include this stage between TTS and timing too.

The default `speech` profile uses the installed FFmpeg and FFprobe, without a new model or Python
dependency. It applies a 65 Hz high-pass filter, restrained de-essing, and mild downward compression
before measured two-pass loudness normalization. Default voice targets are **−18 LUFS** and a
**−2 dBTP** ceiling. A small internal peak margin covers resampling. This is a dialogue-stem
target; the final voice/music/effects mix requires its own mastering check.

Speech remains mono, exported as 48 kHz 16-bit PCM for editing. Resampling is format conversion,
not recovery of missing high-frequency detail. The stage does not change speed, pitch, wording,
or edge silence. It preserves duration to the nearest 48 kHz sample. Very quiet material receives
at most 6 dB of requested loudness boost; silence or ungated short material uses peak control
without loudness amplification. No aggressive denoising or neural enhancement is applied.

## Controls

```powershell
--speech-postprocess speech --speech-target-lufs -18 --speech-true-peak-db -2
```

`--speech-postprocess off` explicitly bypasses processing and restores raw TTS references when
reprocessing an existing job. Supported target ranges are −24 to −14 LUFS and −6 to −1 dBTP.
All settings are available through CLI and Web UI; processing is enabled by default. Plain
audio-only jobs now require FFmpeg and FFprobe while the profile is enabled.

## Artifacts and repeat runs

Raw `tts/` files and `tts.json` remain intact. Each processing pass writes a new directory:

```text
speech-postprocess/<pass-id>/000001.wav
speech-postprocess/<pass-id>/report.json
```

Only after all clips pass format, duration, and peak checks does the stage update `tts_file` /
`tts_duration` in the manifest. `dub_*` placements and stale downstream export references are
invalidated so timing and exports are rebuilt from the accepted files. Errors leave previous
manifest references intact; unreferenced failed-pass files can remain as diagnostic evidence.

The manifest records `speech_postprocess_report`, `speech_postprocess_audio`, and processing
settings under `metadata.speech_postprocess`. Report paths are relative to the run and include
source/output hashes, measurements, normalization mode, and duration per segment. Non-finite
silence measurements are stored as JSON null. Raw TTS report paths remain the source for repeat
passes, so rerunning does not compound compression on already processed clips. Programmatic
jobs without a TTS report retain their raw-source mapping in the processing report.

## Verification and limits

Tests cover real local FFmpeg processing, duration / format / peak preservation, silent and quiet
input, raw-file preservation, repeated passes, explicit bypass, untrusted paths, failure before
publication, and CLI composition/preflight. Unit checks do not establish preferred voice taste.
Audible synthesis errors, incorrect pronunciation, and already clipped source detail may require
regeneration or voice-reference changes; this stage does not claim to reconstruct them.

Filter behavior is documented by [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).
