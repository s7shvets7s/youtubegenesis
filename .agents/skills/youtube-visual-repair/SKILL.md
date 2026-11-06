---
name: youtube-visual-repair
description: Create a copy-ready GPT Image prompt for bad visual-track intervals in an existing project video by mapping final Premiere timeline times to narration lines. Use when the user reports timestamps with poor visuals. Analyze text only; do not inspect or judge generated images unless separately requested.
---

# YouTube Visual Repair

Create replacement background-image descriptions from spoken text and its wider context. This skill
is the project workflow previously called Mode 4.

Write new replacement-image prompts in English for the channel's English-speaking audience.
Preserve exact dialogue quotations and source identifiers from the timeline.

Do not inspect, evaluate, or diagnose the images, clips, provider assets, or visual choices produced
by the pipeline. Do not generate images unless the user separately requests generation. The normal
deliverable is one directly pasteable GPT Image prompt.

## Resolve timeline evidence

1. Interpret reported times as coordinates on the final exported Premiere sequence, not
   automatically as source-video coordinates. Accept `MM:SS`, `HH:MM:SS`, point timestamps, and
   ranges. Normalize them to seconds and preserve the user's original notation.
2. Resolve the managed channel, video, and run under
   `channels/<channel-folder>/videos/<video-folder>/runs/<run-id>/`. Prefer an explicit title or
   video ID plus run ID. Otherwise identify the most recent plausible run from video metadata,
   script artifacts, source metadata, completion time, and Premiere export. If several runs remain
   plausible, ask the user instead of choosing silently.
3. Inspect the selected run's `manifest.json` and current export code before assuming filenames or
   layout. Treat `manifest.artifacts` as authoritative and resolve artifact paths relative to the run
   directory.
4. Prefer this evidence chain when present:
   - `manifest.artifacts["premiere_export"]` and its Premiere export report;
   - the report's dialogue map, normally `premiere-export/dialogue.csv`, for final-timeline start,
     duration, segment ID, and narration text;
   - `manifest.segments` and `manifest.artifacts["script"]` for authoritative spoken text and wider
     context;
   - dialogue markers in `premiere_sequence.xml` only as fallback or cross-check.
5. If artifacts differ because the run uses an older or newer layout, derive the mapping from its
   actual manifest and current export code. Never invent a missing mapping or borrow a similarly
   named file from another run.

## Map lines to image descriptions

1. Collect every dialogue line overlapping each reported interval, including partial overlaps.
2. Deduplicate by stable segment or line ID when user ranges overlap. Treat the same text at a
   different real timeline position as a separate item.
3. Read enough surrounding narration to understand the argument, era, characters, location, and
   transition. Do not create numbered descriptions for context-only lines.
4. Create exactly one replacement image concept for every collected dialogue line. Never combine
   lines or add filler items to reach a target count.
5. If an interval contains no dialogue, report that and ask the user to correct or expand the
   interval. Do not fabricate a visual prompt.
6. Base each concept on the exact line plus its context. Preserve the researched visual style from
   `$youtube-channel-research` and the production brief from `$youtube-script-production`.
7. For factual or historical imagery, verify uncertain people, places, clothing, architecture,
   objects, dates, and symbols on the internet. Do not turn an unverified guess into a factual image.

There is no fixed image count. The numbered descriptions must exactly match the collected dialogue
lines; the user configures generation count separately.

## Save and return the prompt

Save the result under the selected video's `review-prompts/` directory with a descriptive name such
as `gpt-image-visual-repair-<date>.md`. The prompt belongs to the video even though its timing comes
from one run. Do not modify the Premiere package, source media, visual plan, or manifest.

The saved file and user-facing result must contain:

1. One concise context paragraph covering the subject, period, narrative goal, audience, visual
   language, aspect ratio, continuity, and shared constraints. Specify consistent style, 16:9 unless
   the production brief says otherwise, editing-friendly composition, no watermarks, no accidental
   logos, and no rendered captions or text unless verified text is essential.
2. Immediately following numbered paragraphs, exactly one per collected dialogue line. Each item
   must identify its source timeline and dialogue line for editorial mapping while instructing the
   image model not to render those labels. Then describe one self-contained background image:
   narrative purpose, subject, action, setting and period, foreground and background, composition
   and camera distance, lighting, palette, mood, and factual or continuity constraints.

Return the finished copy-ready prompt, not a vague shot list, an analysis of failed images, or a
discussion of alternatives. Mention missing text or timing evidence only when it materially limits
the result.
