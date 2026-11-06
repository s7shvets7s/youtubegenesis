---
name: youtube-archival-story-production
description: Produce an archival mystery or found-record entertainment story from completed competitor research and a selected channel playbook. Use for genre-specific narration, still prompts, paper graphics and edit planning; youtube-script-production owns the managed video and publication lifecycle.
---

# Archival Story Production

Use after competitor research, not as a replacement for it. Reproduce the selected format's
structure, retention mechanics, voice, visual grammar and sound with a new story. Write authored
scripts, plans, prompts and metadata in English; preserve source-language evidence.

## Ownership and intake

`$youtube-channel-research` owns collection and synthesis. `$youtube-script-production` owns title
intake, managed identity, canonical `script.md`, complete-script review, voice direction and
publication delivery. This skill supplies the genre method inside that lifecycle. When invoked
directly, read and follow the parent without recursively restarting or creating another video.
Use `$youtube-short-script-production` for shorts; adapt this long-form profile only if requested.

Within project policy, current user choices override the selected profile, which overrides bundled
defaults. The profile controls genre choices; the parent controls persistence/review/delivery; the
selected visual skill controls its real execution. A selected profile may use another speech rate
than the generic 150-wpm default. It cannot silently change approved narration or requested output.

Minimum inputs: selected channel/playbook, user title, and duration unless the profile supplies it.
Plot, protagonist, era and ending are optional direction. Ask only for a missing title, ambiguous
channel/profile or blocking choice. Language, transcripts, script and scene list are not extra
manual intake when the workflow already determines or produces them.

## Consume and freeze the playbook

Read `SOURCES.md`, `MASTER_SCRIPT_PROMPT.md`, `FORMAT_PROFILE.json`, `GRAPHICS_CATALOG.md`, relevant
dossier conclusions and covered topics. Follow the [playbook contract](references/playbook-contract.md),
also used by research when compiling an archival format.

Do not repeat competitor downloads, transcription or channel collection for every episode. Return
a material evidence gap to research; do not label incomplete required coverage complete. A minor
editing choice can be a recorded adopted setting, never a fabricated observation.

When research exists but has no compiled profile, compile it once. The
[archival mystery preset](references/archival-mystery-v1.json) supplies concrete starting settings
only for a matching format. Mark these as adopted defaults, record research overrides and save the
resolved channel profile. This preset is not competitor evidence or a substitute for research.
Do not replace an existing selected profile simply because a bundled preset is newer.

Freeze the resolved inputs per video using the contract. A resume uses its snapshot and accepted
assets; later playbook edits do not silently change an episode in production.

## Build and review the complete story

Before prose, plan the causal arc in `SCRIPT_STATE.md`. The bundled beats cover a concrete artifact
and narrow mystery, discovery, protagonist, unusual task, escalating attempts, historical context,
reinterpretation, deliberate test, climax, consequential choice, aftermath and callback. Follow the
selected profile's weights and purposes, not a copied plot about one profession or creature.

Make the premise concrete in the first 5–10 seconds. Establish stakes quickly, show a development
within one minute and open the question the ending answers. Cap the opening at 60 seconds even
when scaling the beat weights to a longer runtime; redistribute its excess budget.

Apply selected mechanical requirements deliberately: question clusters, re-hooks, repeated motif,
plants and reinterpretation. Track anchor, purpose and payoff. Meet chosen frequencies and review
each instance's contribution. Do not erase the format's formula into generic storytelling.

Keep an internal fact/fiction ledger. Documentary narration, fictional witnesses and in-story
records are valid entertainment devices. Establish fictional framing briefly where needed in the
package or narration; do not interrupt every scene with academic qualifications. Verify actual
historical claims and quotations; invented records stay fictional in the source ledger. Preserve
a new causal story rather than merely renaming a reference's characters.

Write and review the entire script without quarter-script continuation gates. Budget words at the
selected rate, then measure actual audio. Keep notes out of spoken-only `script.md`; `VO.txt`, if
requested, is an export, not another editable source. The body-and-mind arc, second-person voice
and word cap do not apply unless an explicit hybrid profile selects them.

Review chronology, motives, clue/payoff alignment, selected retention counts, real quotations,
repetition, originality and the ending. The parent owns ready status, parser-compatible voice
direction and publication artifacts for the requested scope. Do not stretch audio to hide a
length shortfall. Changing approved speech invalidates dependent direction, shots and timing.

## Plan visuals, graphics and sound

After script approval read [production handoff](references/production-handoff.md). Make a style
bible, `SHOTLIST.json`, graphics and sound cues from exact narration. Preserve the concrete
palette, cadence and card recipes instead of generic cinematic adjectives.

Separate real archives, historical reconstructions, sketches, present-day footage and graphics.
Each image prompt stands alone with cast/location anchors, medium, composition, action, era,
light and palette. Sketches use sketch prompts, not photograph prefixes. Vary size and angle;
register intentional motifs. Exact labels belong in editor graphics.

For manual generation export `photo_prompts.txt` plus its stable shot-ID map. Targeted regeneration
preserves IDs. Inspect received images, retain originals and log corrected derivatives.
For Flow use `$youtube-flow-visuals`, preserving this profile rather than inherited stickman examples.

Align to final narration. Local/Gemini routes are explicit provider choices with actual model and
voice settings, never silent fallbacks. Check official API documentation before live use, configure
bounded retries/deadlines and keep credentials out of artifacts. The research skill's existing
local source-transcript requirement remains in force; production alignment does not waive it.

Protect deliberate silence after questions/reveals. Mark protected pauses and verify the actual
postprocessor can preserve them before automatic cleanup. Re-align cues after audio edits.
In-bounds ASR timestamps alone do not prove word-level accuracy.

## Execute and deliver the requested tier

- **Manual stills / prompt handoff:** deliver complete prompts, ID mapping, relevant graphics/sound
  plans and requested narration export. Wait for user assets only when they become necessary.
- **Flow:** hand off separate first-frame/motion prompts with exact narration and editor cues.
- **Local assembly:** HyperFrames/FFmpeg is a candidate, not an installed pipeline stage. Inspect
  tools/components, respect dependency policy and pass a technical test before a full render.
  Do not substitute stick-figure animation for the archival-photo aesthetic.
- **Premiere:** check the actual export/import separately. An MP4 or HTML project does not prove
  editable cards, timing or audio in Premiere.

When selected, preview actual opening material with a typical still, demanding card and transition;
do not cram every card type into one minute. Honor the user's existing preview decision.

Check asset coverage, stable IDs, palette, duration limits, registered reuse, card anchors, final
audio presence, loudness and peaks. Inspect semantic matches, period props, continuity, readability
and actual mixed sound. Grain must not masquerade as camera movement. Record planned, aligned,
rendered and inspected work separately in `SCRIPT_STATE.md`.

Complete the requested tier: script, visual handoff, rendered MP4 or Premiere package. Link actual
outputs and remaining dependent work. A prompt package can be complete without an unrequested
render; an unfinished render cannot be called a completed video.
