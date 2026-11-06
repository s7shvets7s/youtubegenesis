---
name: youtube-short-script-production
description: Produce a researched short YouTube script from a completed channel playbook, with a durable video record, review, metadata, and an edit plan for synchronized sound effects, word-highlight captions, and contextual overlays. Use for new short-form videos rather than the long-form production workflow.
---

# YouTube Short Script Production

Use for a new short video, normally 30–180 seconds. A longer request belongs in
`$youtube-script-production` unless the user explicitly wants a different structure. This skill
shares the same channel research, durable video, and factual standards as the long-form workflow;
both workflows draft and review the complete script without mandatory continuation gates.

Write all new titles, narration, metadata, and visual text in English for the channel's
English-speaking audience. Preserve original source audio and research quotations as evidence.

## Research and create

1. Identify the managed channel and its playbook. Read playbook `SOURCES.md` and
   `MASTER_SCRIPT_PROMPT.md`, the relevant competitor dossier, covered topics, and existing video
   metadata. Complete missing research through `$youtube-channel-research` before drafting.
2. Choose a distinct angle and verify niche, uncertain, or time-sensitive claims with primary or
   authoritative sources. Record sources, access dates, and fact/inference boundaries in the new
   video's `SOURCES.md`.
3. Use `WorkspaceCatalog` to create or reuse exactly one managed video. Record target audience,
   language, duration, format, visual cadence, thesis, exclusions, and differentiation in
   `BRIEF.md`. Include the caption style, sound-design approach, and overlay policy. Initialize
   `SCRIPT_STATE.md` and a clean spoken `script.md`.

## Draft and review

Apply the channel-wide opening-retention rule from `$youtube-script-production`, compressed for
the short format: show the concrete hook in the first 1–3 seconds, establish the premise and
stakes without a preamble, and open a question whose payoff arrives before the ending. Favor
interesting speculation and a genuinely debatable choice while labeling fictional assumptions
and preserving factual support. Record the hook and payoff in SCRIPT_STATE.md. Never turn an
entire short into setup for a payoff outside the delivered video.

Draft one complete short script with a concrete hook, escalation, payoff, and natural spoken
language. Read `$kaomei-stickman-director` for optional short-video structure and style guidance.
Do not impose its five-stage pattern, debate question, or 60-second duration on a
channel whose playbook calls for another form. Preserve originality and avoid copied characters,
dialogue, and signature scenes.

Review factual support, spoken word count, delivery pace, continuity, and the ending. Put only
spoken words in `script.md`; keep production notes in `BRIEF.md` or `SCRIPT_STATE.md`. For a fixed
scene cadence, write `CAPCUT_BEATS.srt` with one exact spoken cue per scene. Treat roughly 20–25
English words per ten seconds as a *starting estimate*, then check measured local TTS against the
window and rewrite overlong cues. Write `VOICE_DIRECTION.json` or
`CAPCUT_VOICE_DIRECTION.json` when the selected local TTS route supports it. Mark
`video.json.script_status` ready only after final review and synchronize the script hash and word
count through `WorkspaceCatalog`.

## Sound, captions, and contextual overlays

Read [references/short-editing.md](references/short-editing.md) for every new short. Plan
action-specific SFX, captions that highlight the currently spoken word, and context-driven text
or animation composited over the generated background. Keep narration exact and the generated
base footage reusable. Additional callouts and animations are selective; do not force one into
every shot or add a sound on every word.

Draft cue plans with exact spoken anchors before audio is available. Once final narration exists,
align words to that audio and finalize cue timing. A scene SRT or estimated speaking pace does not
provide word timings. Keep script approval separate from edit completion: a ready script may
still have planned captions, missing effects, or pending overlays, recorded in `SCRIPT_STATE.md`.

Write `METADATA.md` after review with distinct title options, a grounded description, relevant
tags, a platform-ready tag subset, and a small relevant hashtag set. Recheck current YouTube
metadata limits before delivery. When visuals are requested, pass the approved script and timed
beats plus the edit plans to `$youtube-flow-visuals`, or to `$2d-video-plugin` when the user asks
for locally rendered visuals. Both routes must carry the sound, caption, and overlay contract
through the edit; neither may rewrite narration or create a second video entity. A prompt-only
handoff must list remaining assembly work. The Web UI selects the ready video and snapshots its
script for each run; this does not itself render the new edit layers.
