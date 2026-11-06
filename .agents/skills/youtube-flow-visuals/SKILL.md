---
name: youtube-flow-visuals
description: Turn an approved managed-video script and timed beats into consistent first-frame and Google Flow image-to-video prompts, with a durable style bible and visual QA. Use for new visual production; use youtube-visual-repair for replacing intervals in a finished timeline.
---

# YouTube Flow Visual Production

Build reviewable visual instructions for one managed video. This is a prompt and handoff workflow;
it does not claim to operate Google Flow, generate clips, or import a CapCut project.

Write new style bibles, first-frame prompts, motion prompts, and authored on-screen text in
English for the channel's English-speaking audience. Preserve exact approved
narration even if its language differs; do not translate it as part of visual planning.

## Ground the shot plan

1. Resolve the channel by `channel.json`, the video by `video.json`, and any relevant run by its
   `manifest.json`. Read the ready `script.md`, `BRIEF.md`, `SOURCES.md`, channel playbook, existing
   `VISUAL_BEATS.md`, and `CAPCUT_BEATS.srt` when present. Preserve exact narration and scene order.
   For archival work, also read the frozen `PRODUCTION_INPUTS.json` snapshot, `FORMAT_PROFILE.json`,
   `GRAPHICS_CATALOG.md`, `SHOTLIST.json` and existing overlay/sound plans. Preserve stable shot IDs
   across exports; estimated windows are not word-aligned timing.
2. Read `$kaomei-stickman-director` as a secondary continuity method; its stickman examples do not
   replace an approved archival photograph/sketch style. Inspect the channel's actual reference
   images when available. Do not claim an image was checked based only on a filename or description.
3. Reconcile beat count and timing with the narration windows. A rough beat is a planning note,
   not a production prompt. If a window has too much speech, flag it for script/timing revision;
   changing visual words does not solve narration overflow.
4. For shorts, read the shared [short editing contract](../youtube-short-script-production/references/short-editing.md)
   and existing `SOUND_DESIGN.md`, `OVERLAY_PLAN.md`, and word/caption assets when present. Carry
   SFX anchors and overlay IDs into each shot's editor notes; reserve room for active-word captions
   and contextual graphics while keeping generated footage clean for compositing.

## Durable outputs in the video directory

Create or update these files without replacing the canonical script or existing user media:

- `VISUAL_STYLE_BIBLE.md`: source and date of each inspected reference; output ratio; drawing
  grammar; frozen descriptions of each recurring character; costume and prop ownership; palette
  roles; locations, lighting, and future/flashback signatures; approved text-overlay policy;
  explicit forbidden drift and unresolved reference gaps.
- `FLOW_SHOT_PROMPTS.md`: one complete entry for every planned shot ID and time window. Include
  its exact narration text and source cue, narrative purpose, first-frame prompt, image-to-video
  prompt, final-frame/transition state, editor overlays, and inspection status. Store reference
  image paths relative to the video directory when the images live there. For external inputs,
  record the source without making a manifest artifact path absolute.

For each **first-frame prompt**, specify the active character and its frozen visual anchor, the
distinctive location, foreground/middle/background layout, shot size, camera angle, lighting,
palette roles, relevant object positions, emotion expressed through visible action, and the single
story fact the frame must communicate. State what is absent only when it prevents a known failure.
Do not write “cinematic scene” or a narration paraphrase as the whole prompt.

For each **image-to-video prompt**, say that the approved image controls appearance and layout.
Give a small sequence of physical actions tied to the beat, camera movement only when it helps,
elements that must stay rigid, and a stable end frame. Use timed sub-beats when the model's actual
clip length supports them. Do not require motion of UI glyphs, countdown digits, or exact text;
place such elements as editor overlays when necessary. Keep local narration, SFX, and music out of
the generated clip unless the user selects generated sound for that video.

Each prompt must stand alone: repeat the relevant cast/style locks rather than relying on a global
paragraph the provider cannot see. Adapt the upstream negative constraints to the approved style;
never ban a filled head or facial marks when the channel reference requires them. Preserve the
script's fictional rules and facts; do not invent people, places, or actions that change the story.

## Review and handoff

- Audit every shot against exact narration, source beat, style bible, prop ownership, location,
  screen direction, adjacent end/start states, ratio, and text policy. Mark missing images and
  untested Flow assumptions rather than describing them as verified.
- Inspect a representative opening, middle, and ending still before batch production. After
  generation, inspect opening/action/ending frames and listen to any generated audio. Record
  drift, retries, selected clips, and remaining manual edits in the video folder.
- Keep Flow prompts and generated clips separate from run `manifest.json` until the application
  actually creates or imports those artifacts. Use `CAPCUT_BEATS.srt`, `narration.wav`, and
  `narration.srt` as the timing and audio handoff when that route is selected.
- For shorts, include the sound and overlay plans plus aligned word timings and styled captions
  when available. List missing alignment, mixing, compositing, and final-export checks explicitly;
  a Flow prompt package does not execute these editing steps.

Better prompts may improve externally generated visuals, but do not make the local visual
provider or full render automatically pass. State this boundary when discussing expected results.

Project-specific worked examples and run evidence are private local materials. Use them only
when available in the workspace; do not add them to the repository.
