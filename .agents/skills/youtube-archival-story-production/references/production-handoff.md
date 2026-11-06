# Archival production handoff

Read after approved narration for visual planning/assembly. Use the frozen profile, exact script
and parent lifecycle, not a newly selected example.

## Shots and assets

Create `VISUAL_STYLE_BIBLE.md`: cast, costume, rooms/props, era signatures, palette, medium, ratio,
text policy and source references. Continuity changes belong to the story, not prompt drift.

`SHOTLIST.json` has version 1, video_id, script_sha256, profile_sha256 and `shots`. Each shot has:

- immutable shot_id independent of list position;
- exact narration_text, cue/segment ID when available, disambiguated anchor and narrative purpose;
- one base type: historical_reconstruction, real_archive, graphite_sketch, present_day or full_frame_graphic;
- cast/location anchors, first-frame prompt, motion, transition, graphic/sound IDs and motif exception;
- asset status, source/rights record, accepted video-relative path and hash when available;
- timing status estimated/aligned, with start/end seconds or null when not known.

Overlays are not additional base shots. In video `OVERLAY_PLAN.md`, record instance ID, shot ID,
literal text/source, speech/action anchor, time, safe area, entry/hold/exit, layers and actual
implementation status. `SOUND_DESIGN.md` records action/scene anchors, sources, gain/fades/ducking.

Export manual `photo_prompts.txt` and `PHOTO_PROMPT_MAP.json`: version 1, script/profile hashes and
ordered entries with paragraph_index, shot_id and prompt_sha256. One-based paragraph position is
an export coordinate only. Regeneration preserves IDs and gives explicit replacements. Never map
assets by filename sort or creation time. These sidecars are not automatic worker/manifest fields.

## Actual audio and intentional pauses

Record narration provider/model/voice, text hash and accepted audio hash. Align to final audio and
its placement. Local/Gemini alignment is explicit. Check official API shape, availability, limits
and budget before use; bound polling/retries and clean up uploads. Do not hardcode a daily quota.

Verify word order, coverage, opening/middle/ending, joins and repeated anchors. Flag disagreement
between approved text and actual speech for deliberate correction; never canonize an ASR error.
An estimated SRT or in-range JSON is not proof of accurate word alignment.

Mark protected pauses with ID, purpose, surrounding exact words and measured duration. A reveal
hold may exceed the ordinary 1.1-second review threshold. Inspect whether the real postprocessor
can preserve exceptions before cleanup. Preserve the source and re-align after audio edits.

## Acceptance and assembly

Verify every required ID has the correct asset, not just matching counts. Inspect contact sheets
and details for period, subject, continuity, text, color and defects. Normalized HSV saturation
is a flag, not semantic QC. Preserve originals and log corrected copies; re-prompt wrong subjects.
Use project security/rights checks and per-item credits; respect the disabled NARA route.

Choose the output before promising assembly: prompts, Flow handoff, MP4 and Premiere differ.
For HyperFrames/another local renderer inspect its available skill/docs/interfaces, actual tools,
fonts and components, then run a small technical test. New dependencies require normal repository
authorization. Confirm editable layers and keep actual project files. Stickman support does not
prove archival-photo/card rendering.

For chunked rendering preserve global frame/audio coordinates, transition handles, consistent
codecs, continuous audio and checkpoints/logs. Inspect joins; concat alone does not verify sync.
If a style preview is selected, use actual opening footage with a typical still/card/transition.
Use another small sample for an absent high-risk component instead of distorting the opening.

## Verification and repeat runs

Check profile shot duration/reuse exceptions, generated-prompt palette share, card anchors,
final audio presence/duration, loudness and true peaks. Watch/listen to the actual export at the
opening, climax, ending and joins. Check graphics, props, continuity, pauses and audible effects.
Grain/flicker must not stand in for camera movement. Record inspected file, method and result.

Save hashes, accepted assets, component code, fonts and tool/model settings. New episodes reuse
the channel recipe; resumed episodes use frozen inputs. Regeneration may vary even with the same
prompt/seed. Rendering fixed material is different from generating it again; changed encoders or
environments need not produce identical bytes.

Deliver direct links to the requested tier and parent publication assets for that scope. Track
missing assets, alignment, compositing, mix, render checks and Premiere import explicitly. Keep
research, private media, credentials and generated deliverables out of Git.
