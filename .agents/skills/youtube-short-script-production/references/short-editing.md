# Short editing: sound, active-word captions, and overlays

Read when planning or assembling a short through `youtube-short-script-production`, Flow, or
the local 2D route. The channel playbook and approved narration control the style and content.
Use these defaults unless the user explicitly requests a different treatment.

If a reference video is supplied, inspect representative moments for effect placement, caption
grouping and motion, highlight behavior, and overlay density. Record what was actually observed
in `BRIEF.md`. If the video is unavailable, describe the chosen treatment as a proposal; do not
claim to have matched it. Recreate useful editing principles with original or licensed assets.

## Plan now, time against the final narration

During script review, identify exact spoken anchors and visible actions for SFX and overlays.
Use provisional scene windows only for planning. Keep all production instructions outside
`script.md`; no effect directions or overlay copy may enter spoken narration.

Once the accepted voiceover and its placement are fixed, use available word timestamps or a
forced aligner against the exact narration text and audio. Verify that timestamps refer to the
final edited audio, including offsets, pauses, cuts, and any speed changes. If clips are aligned
separately, map their local timestamps onto the final timeline. Re-align affected words and
retime dependent cues after an audio edit.

Neither `CAPCUT_BEATS.srt` nor the pipeline's segment-level `narration.srt` supplies word alignment.
Never divide a sentence duration equally among its words and call it synchronized. Do not assume
that the current Web UI, Flow, or 2D renderer implements word alignment or animated captions.
If an aligner or editor capability is unavailable, deliver the anchored plan and state the
remaining step in `SCRIPT_STATE.md`; do not silently replace active-word captions with static text.

## Action-specific sound effects

Finished shorts include synchronized audible SFX unless the user requests no effects. Choose
effects for an identifiable action or story change: a contact sound for an impact, a brief whoosh
for a fast movement, a tick for an actual countdown, or a restrained accent for a reveal. Silence
can support a serious or emotional beat. Avoid repetitive transition sounds, constant effects,
and sounds on every highlighted word.

Keep `SOUND_DESIGN.md` in the managed video directory. Each cue records a stable ID, shot ID,
spoken/action anchor, purpose, and planned or actual timeline start/end. Once realized, also
record asset path, gain, fades or ducking, source/license or original-generation provenance, and
verification status. Listen to embedded clip audio before adding layers; remove, mute, or account
for existing effects so the mix does not double them. Preserve separate voice, SFX, and music
assets where supported, with narration clearly intelligible above the other layers.

Account for the environment shown in the picture as well as isolated actions. Rain, moving traffic,
a workshop, or another audible setting may need a continuous scene-specific ambience bed. Adapt
its perspective and transitions between locations; do not leave visible weather acoustically empty
unless silence is an intentional story decision. Keep ambience separate from music and spot SFX.

When music is requested or planned, make it perceptible in the final mix, including on a phone or
mono preview. A very quiet pad can disappear beneath speech and ambience. Check the balance of
voice, music and ambience together, use restrained ducking, and record measured stem levels as
supporting evidence. A nonzero music file alone does not establish an audible soundtrack; listening
remains the final balance check, and unavailable listening must be disclosed.

For a finished render, effects must be audible in the actual final file. A cue sheet, background
music, or unused WAV files alone does not satisfy this requirement. Include the used effects
and cue map in a full editable-package delivery. Do not copy audio from a competitor video.

## Captions that follow the spoken word

Caption the exact approved narration. Show short, readable phrase groups, usually two to five
words and at most two lines, adapting line breaks to syntax and the phone-size preview. Highlight
only the word currently being spoken using a channel-consistent accent, pill, or subtle scale
change. Return completed words to the base style rather than leaving a whole phrase active.
Clear the active state during pauses. Keep surrounding words stable so the viewer can read
ahead without chasing a different layout on every word.

Use restrained phrase entrance/exit motion and selective emphasis on meaningful words. Maintain
legibility through contrast, outline or backing as appropriate to the actual background. Keep
captions inside the target platform's visible safe area, clear of interface controls, faces,
important props, diagrams, and overlay callouts. Inspect the actual composition; a fixed lower
position is not safe for every shot.

After alignment, save `WORD_TIMINGS.json` with the source audio path and SHA-256, timeline
duration, and a word list containing stable word ID, exact text, start/end seconds, and caption
group ID. Check that every spoken word is represented in order, intervals are positive and
within the audio duration, and a single narrator has no conflicting active words. Review
punctuation, contractions, numbers, and ASR discrepancies against the approved script rather
than accepting recognition errors as new narration.

Produce an editor-native animated-caption track or `CAPTIONS.ass` when the selected renderer
supports the required styling. Verify that the format can activate one word and then deactivate
it; a cumulative karaoke fill is insufficient. Keep an ordinary SRT as a portable text/timing
fallback, clearly labeled as lacking active-word styling. Do not replace `CAPCUT_BEATS.srt` with
the caption track: it still controls scene cadence.

## Contextual text and animation over the background

Use a separate compositing layer when it clarifies the beat: a short keyword or number, an arrow
pointing to the relevant object, a simple diagram, a counter, or a small reaction animation.
For example, a spoken comparison may justify a scale comparison graphic, while a rapid movement
may justify a short motion trail. Leave the shot clear when the background and captions already
communicate the point. Avoid repeating the whole subtitle as a second text block.

Save `OVERLAY_PLAN.md` with a stable overlay ID, shot ID, exact spoken/action anchor, editorial
purpose, literal English text or graphic description, planned or actual start/end, position and
safe area, entrance/hold/exit behavior, layer order, and asset/provenance and status when realized.
Short callouts usually need only one to four words. Numbers and scientific graphics must remain
supported by the script and its sources; do not invent measurements or imply a fictional example
is observed evidence.

Composite these elements over the already generated image or clip. Preserve the clean base and
separate editable overlay assets where the chosen editor supports them. Do not regenerate a
whole background just to add a label. Keep exact text, captions, countdowns, and UI glyphs out of
image/video-generation prompts; use the editor to control their spelling and timing. Avoid
competing motion and collisions between callouts, active captions, and the subject.

## Route handoff and completion

- **Script-only delivery:** create anchored `SOUND_DESIGN.md` and `OVERLAY_PLAN.md`, with caption
  style in `BRIEF.md`. Keep unmeasured timestamps provisional and alignment/rendering pending.
- **Flow handoff:** link each shot's editor-overlay notes to overlay IDs and related sound cues.
  Generate clean background footage, then hand off these plans with final narration and caption
  assets when available. Prompts alone do not add captions, overlays, or effects to the footage.
- **Local render or editor assembly:** confirm available alignment/compositing tools, render the
  caption and overlay layers, and mix SFX into the final soundtrack. Respect dependency policy;
  a missing tool is a reported gap, not permission to install it or switch visual routes.

Store plans and accepted assets under the existing managed video, with paths relative to that
directory. Run-specific outputs stay inside the selected run and use run-relative artifact paths
only if the application actually registers them. These editing documents are handoff contracts,
not new manifest fields or automatic pipeline stages. Record planned, aligned, rendered, and
verified work separately in `SCRIPT_STATE.md`, including remaining manual steps.

Before calling an assembled short complete, watch and listen to the final export. Check word
highlights against speech at the opening, middle, ending, and edited joins; inspect caption and
overlay visibility at phone size, and confirm callout entrances and exits follow their anchors.
Check audible action sync, narration clarity, audio presence and duration, loudness, clipping,
and the absence of duplicated clip audio. Record the inspected file and actual results. If only
plans or prompts were requested, finish that handoff while explicitly marking rendering and
final-file checks as pending.
