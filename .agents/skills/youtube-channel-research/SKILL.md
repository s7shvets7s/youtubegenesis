---
name: youtube-channel-research
description: Research a reference or competitor YouTube channel for this project, including complete accessible video metadata, selected transcripts, script and visual patterns, title and description performance, and an original reusable script blueprint. Use when the user provides a channel URL or asks how to make similar content. Do not use for drafting a new production script.
---

# YouTube Channel Research

Build a durable, evidence-backed competitor dossier for one managed channel. This skill is the
project workflow previously called Mode 2.

Write new dossiers, analyses, and playbooks in English for the channel's English-speaking
audience. Transcribe each source video's actual spoken language verbatim;
the English default does not change source evidence.

## Resolve the scope

1. Identify the managed channel that will use the research. If it is ambiguous, ask before writing.
2. Resolve and verify the exact reference channel from its URL or handle. Record its canonical URL,
   channel ID when available, research date, user goal, language, and selection constraints.
3. Store one reference channel per dossier:

   `channels/<managed-channel-folder>/research/competitors/<competitor-slug>/`

4. Save dated source information so later sessions can distinguish historical observations from
   current data.

## Collect the evidence

1. Collect metadata for every accessible public video: video ID, URL, title, full description,
   upload date, duration, view count, and other reliably available public engagement fields. Keep
   raw structured data separate from conclusions.
2. Analyze every accessible title and description, not merely the transcript sample. If deletion,
   access, scale, or rate limits prevent a complete export, checkpoint the collected data and state
   the missing coverage explicitly. Never label a sample as all videos.
3. Select three or four suitable long-form reference videos. Follow the user's selection rules;
   otherwise select the most-viewed suitable videos and record the reason for each choice. Do not
   mix Shorts with long-form unless requested.
4. For every selected long-form reference video, use the project's pinned `yt-dlp` installation to
   download the exact public YouTube source into the competitor dossier's ignored `downloads/`
   directory.
   Invoke the installed project module (for example, `.venv/Scripts/python -m yt_dlp`) rather than an
   unrelated global binary, browser extension, or third-party downloader. The downloaded media must
   be the input for a complete local speech transcription on the video's actual spoken language.
   Existing or `yt-dlp`-downloaded YouTube captions may be preserved for comparison, but they never
   replace transcription of the downloaded media. Respect access controls, platform terms, and
   copyright; never bypass authentication, DRM, private access, or regional restrictions.
5. Before analysis, verify that the local transcription reached the end of the downloaded source and
   produced usable timestamp coverage for the full spoken runtime. Spot-check the opening, middle,
   and ending against the media or available captions and record material gaps or disagreements. If
   `yt-dlp` cannot download the public source, the transcription runtime cannot use the actual spoken
   language, or the transcript is incomplete or unreliable, stop the dossier workflow and report the
   exact blocker. Do not silently fall back to captions or an unverified transcript, and do not label
   the research complete.
6. Preserve each selected video's full media-derived transcript with its video URL, stable video ID,
   timestamps, `yt-dlp` version and download command, transcription command and model, configured or
   detected language, and local media and transcript paths. Mark transcription uncertainty and never
   invent missing speech.
7. Record every source, access date, collection method, limitation, and local media path in
   `source-log.md`. Do not commit large downloaded media by default; follow `.gitignore` policy.

## Analyze and synthesize

Read and apply $claude-youtube-competitor as a secondary competitor-analysis framework. Use its
gap, outlier, audience, quick-win, and differentiation lenses, but follow this skill whenever the
upstream framework conflicts with full-catalog coverage, normalized performance, persistent dossier
files, evidence limits, or originality requirements.

Compare the selected videos across:

- hook, promise, audience, narrative structure, and section order;
- pacing, re-hooks, curiosity gaps, transitions, evidence, and examples;
- emotional movement, sentence style, tone, calls to action, approximate word count, and ending;
- when media is available, visual rhythm, recurring shot types, on-screen text, archival or stock
  usage, graphics, and editing cadence.

Faithfully capture the selected format's narrative structure, pacing, retention mechanics, visual
grammar and sound so they can be reproduced in new stories. Do not copy distinctive phrases,
personal stories, branding or long passages. Keep a concrete recipe rather than averaging
contradictory references into a generic style.

Analyze title performance without ranking by raw views alone. Where data permits, account for
publication age, views per day, performance relative to the channel median, and comparable-age
uploads. Separate topic demand from wording when the evidence cannot distinguish them.

Derive description guidance from all collected descriptions: opening lines, promise, chapters,
links and credits, calls to action, keyword placement, disclosures, and recurring weak practices.
Supplement observations with current niche research, using dated citations and primary or
authoritative sources where possible.

Finish with evidence, confidence, limitations, covered topics, content gaps, promising unused
angles, and concrete recommendations.

## Required dossier

Create at least:

```text
channels/<managed-channel-folder>/research/competitors/<competitor-slug>/
  README.md
  source-log.md
  channel-manifest.json
  videos.csv
  transcripts/
  script-analysis.md
  visual-style-analysis.md
  title-description-analysis.md
  niche-research.md
  covered-topics.md
  script-blueprint/
    MASTER_SCRIPT_PROMPT.md
```

Use stable YouTube video IDs in transcript and media filenames.

`MASTER_SCRIPT_PROMPT.md` must encode the evidence-backed structure and style, originality and
fact-checking constraints, placeholders for topic, length, and audience, and the complete-script
drafting/review protocol defined by `$youtube-script-production`.

Several competitor dossiers may support one managed channel. Keep their raw evidence and
conclusions separate. Put a synthesized production prompt in
`channels/<managed-channel-folder>/playbooks/<content-format>/` and create `SOURCES.md` mapping
every adopted pattern to its dossier. Do not silently merge contradictory patterns.

For an archival mystery or found-record format, read the
[archival playbook contract](../youtube-archival-story-production/references/playbook-contract.md).
Compile `FORMAT_PROFILE.json` and `GRAPHICS_CATALOG.md` beside the synthesized
`MASTER_SCRIPT_PROMPT.md` and `SOURCES.md`. Use sampled contact sheets, animation strips and audio
inspection to document concrete timing/graphics rules with coverage limits. The bundled archival
preset may fill adopted production defaults only after research; never label it measured evidence.
These private channel files are consumed by `$youtube-archival-story-production` for later episodes,
without automatically repeating competitor collection.

## Capability and safety rules

- Inspect the available skill catalog before research. Relevant research, content, voice, and SEO
  skills may assist, but they do not replace evidence checks, coverage accounting, performance
  normalization, or the dossier above.
- A video-level transcript tool is only a supplement; it is not a full-channel analyzer.
- Never install, update, or execute an unfamiliar third-party skill without inspecting its
  `SKILL.md` and scripts and obtaining user approval for installation or new dependencies.
- Publishing or account mutations are outside this workflow and require explicit authorization.
