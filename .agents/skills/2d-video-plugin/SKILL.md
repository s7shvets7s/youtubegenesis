---
name: 2d-video-plugin
description: Use the user-supplied 2d-video Claude plugin as an optional local code-rendered visual route when the user asks the agent to create the video visuals itself. Keep it separate from Google Flow prompt handoff and from the core Premiere pipeline.
---

# Local 2D video plugin adapter

The complete user-supplied Claude plugin is vendored at
`third_party/2d-video-plugin/`. Its original `.claude-plugin/plugin.json`, five `SKILL.md` files,
references, renderer, sample scenes, and tools are present. Provenance and local changes are in
`third_party/2d-video-plugin/SOURCE.json`. Read the relevant upstream skill and references before
using this adapter. No public source repository or license file was supplied in the archive.

## Mandatory audible effects

Every completed video must include action-specific, synchronized sound effects in the actual
final MP4 unless the user explicitly requests no effects. Background music alone, a cue list,
or unrendered WAV assets is insufficient. Use the plugin's sound-design workflow, supplement
its library with original or appropriately licensed effects where useful, and record the
actual cue timing, action, asset, gain and provenance in `SOUND_DESIGN.md` under the managed
video. Mix and duck effects so narration remains clear; use restraint during emotional beats.
Check the final mux for audio presence, duration, loudness and clipping, and include used
local effects plus the cue map in a full-package delivery. Do not call a video complete when
sound effects are still missing from its soundtrack.

For shorts, also read the shared [short editing contract](../youtube-short-script-production/references/short-editing.md).
Carry its SFX cues through the mix, align captions to the final narration so only the spoken word
is highlighted, and composite contextual text or animations over the base scenes when useful.
Inspect the renderer's actual caption/compositing support before promising the result; use an
available editor or report pending assembly if needed. Keep plans and editable layers under the
existing managed video, and verify the final export before marking these features complete.

Use English (`en`) for all new project titles, labels, captions, calls to action, and other
authored visual text for the channel's English-speaking audience. Do not ask for an output language.
Preserve the source voiceover language separately in `speechLanguage`; use detection or confirm
it when unknown. Never translate existing voiceover or verbatim captions as a side effect of
rendering. This language policy overrides any older default in vendored plugin documentation.

## Choose the route explicitly

- If the user will generate the video sequence in **Google Flow** and wants first frames plus
  motion descriptions, use `$youtube-flow-visuals`. Do not run this 2D renderer.
- If the user asks the agent to **create the visuals here**, this plugin is a candidate for
  code-rendered animated scenes. Explain the stick-figure aesthetic and check that it fits the
  intended result. Then use the vendored `make-2d-video` workflow and stage references.
- If the user requests generated images or another visual style, use the suitable visual tool or
  workflow. The 2D plugin is not a general text-to-image or Google Flow integration.

## Project authority and data boundaries

The user's request, `AGENTS.md`, managed channel playbook, and approved `script.md` take precedence
over plugin instructions. Preserve exact narration and captions. Never create a second managed
video identity. The plugin's own `2d-video/projects/<slug>` layout is a rendering workspace,
not a replacement for `channels/<channel>/videos/<video>/` or a pipeline `manifest.json`.
Keep durable visual plans and accepted outputs under the managed video directory; use an ignored
`.tools/2d-video/` working copy for the renderer when needed. Record provenance and the chosen
route in the video's `BRIEF.md`. Do not claim a Premiere package or main pipeline stage has been
produced by this adapter.

The vendored renderer has one local hardening patch in `render.mjs`: its preview server binds
to `127.0.0.1` and refuses paths outside its intended roots, including symlink escapes. Keep
that patch when refreshing the archive. Treat any project scene JavaScript as executable code;
inspect it before rendering.

## Dependencies and execution

The plugin requires Node.js, Playwright with Chromium, Three.js, fonts, FFmpeg, and Python audio
packages. The archive is **not** proof these are installed. Inspect the vendored `package.json`
and `references/install.md`, check the current environment, and report exact gaps before a render.
The source archive was reviewed before vendoring; it was not executed during integration.

Do not run `tools/doctor.py --install`: that path constructs shell commands with `shell=True`
and would install packages automatically. Plain `doctor.py` is a read-only prerequisite check.
Do not install or upgrade packages merely because the plugin requests them. Confirm the actual
dependencies needed for the selected render, use bounded, argument-based commands, and keep
them isolated from the project's GPU environments. Follow repository policy for any new
dependency installation.

Copy the kit to the ignored working directory only when a render is requested. Do not overwrite
an existing `projects/` directory or any user scene. Use the plugin's `verify_kit.py`, doctor,
small self-test, contact sheets, preview render, and visual QC before a full render. Verify
timing and audio against the approved narration. A passing static check does not prove that a
full render, GPU path, or final audio mix works.
