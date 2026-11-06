---
name: new-video
description: >
  This skill should be used when the user runs /new-video or asks to "start a new 2D video", "set up a video
  from this voiceover", "timestamp my voiceover", "transcribe and time my narration", "import my SRT/timestamped
  script", or "plan the shots for my script" in the 2D Video. It covers intake, installing or checking
  the studio, creating the project, building word-level timestamps (Whisper or the user's timestamped script),
  and the story breakdown with the shot list.
metadata:
  version: "0.1.1"
  author: Андрей Зенин
---

# /new-video: intake → install → timeline → story breakdown

This command covers stages 1–4 of the 2D Video pipeline. Shared references live in the sibling skill folder `../make-2d-video/references/` (relative to this skill's base directory), and the studio kit lives in `../make-2d-video/kit/`.

## Language policy

- The channel audience is English-speaking. Set `config.json` → `language` to `en` for every new project. Write all new titles, labels, charts, captions, calls to action, and other on-screen text in English. Do not ask which output language to use.
- Preserve names and the user's exact approved narration. Store source voiceover language separately as `speechLanguage`; detect it when unknown or ask only if detection cannot resolve it. Never assume that English output text proves the source audio is English.
- Preserve verbatim captions in the voiceover language. If existing voiceover is not English, an English-facing final video needs an approved English script, matching replacement narration, and aligned captions before rendering; the kit does not translate or synthesize them automatically.
- For an existing project with non-English authored text, do not silently relabel or overwrite it. Resolve the English script, voiceover, and captions as a separate deliberate production change.
- Pass explicit English text to UI helpers, including `subscribeLabel`, and check previews for stray non-English text or missing glyphs.

## 1. Intake
Ask once (AskUserQuestion when available), skipping anything the user already said:
- **Voiceover file** (required).
- **Script:** timestamped (SRT, VTT, JSON, or TXT with `[mm:ss]`), plain text, or none.
- **Aspect:** 16:9 or 9:16.
- **Resolution:** 720p / 1080p / 1440p / 2160p.
- **Captions:** default on for 9:16.
- **Music:** generated, own file, or none. **SFX:** on or off.
- **Title.**

Defaults: language `en`, source speech language detected or explicitly supplied, 16:9, 1080p, generated music + SFX.

## 2. Studio
If `2d-video/render.mjs` doesn't exist in the workspace, follow `../make-2d-video/references/install.md` in full. Otherwise run `python tools/doctor.py` inside it and fix anything reported.

## 3. Project and timestamps (inside `2d-video/`)
```bash
python tools/new_project.py --name <slug> --lang <language> --speech-lang <speech-language> --voice "<voiceover>" [--script "<script>"] --aspect <16:9|9:16> --resolution <res> --title "<title>" [--captions]
```
- **Timestamped script** → `python tools/parse_script.py --project projects/<slug> --script projects/<slug>/input/script.<ext>`. It accepts SRT, VTT, JSON (`[{start,end,text}]`, `{segments}`, `{words}`) and TXT lines like `[00:12.5] text`, `00:12 - 00:15 text` or `00:00:12,500 --> 00:00:15,000 text`. If the parser reports no timestamps, fall back to the plain-script route.
- **Plain script** → `python tools/transcribe.py --project projects/<slug> --script projects/<slug>/input/script.txt --lang <speech-language> --model small`. It keeps the script's exact words with Whisper timing. Require ≥ 90% match; if lower, tell the user the script and audio differ.
- **No script** → `python tools/transcribe.py --project projects/<slug> --lang <speech-language> --model small` (`base` for speed, `large-v3` for difficult audio). The first run downloads the model.

Open `timeline/transcript.txt` and confirm it reads correctly and ends near the voiceover length. Then run `python tools/beats.py --project projects/<slug>` (add `--force` to overwrite an existing shots.json).

## 4. Story breakdown
Read `../make-2d-video/references/style-bible.md` and `../make-2d-video/references/story-breakdown.md` fully, then:
1. Write `projects/<slug>/story/bible.md`: logline and tone, the cast (one saturated signature item each), locations (mood, key light, window view, props), motifs and the music plan.
2. Write `projects/<slug>/scenes/cast.js` with the outfits.
3. Rewrite `projects/<slug>/story/shots.json` following the pacing rules. Every shot needs a concrete `scene` description, plus transitions, bars, sfx and amb where they matter.
4. Send the user a 5–10 line summary: the cast, the locations, the shot count, and the highlights per act.

Finish by saying the next step is `/video-styles` (or continue with the full pipeline in the `make-2d-video` skill if the user asked for the whole video).
