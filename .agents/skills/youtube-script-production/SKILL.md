---
name: youtube-script-production
description: Produce a researched YouTube video package from a user-supplied topic. Default to 150 words/minute (140–160), or the explicitly selected format profile, then verify measured audio. Use channel research, drafting, review, a recommended title plus alternatives, publication-ready description, tags, a finished thumbnail, and the selected visual route. Deliver the complete package with direct links.
---

# YouTube Script Production

Produce one researched video package under the managed channel. This skill is the project workflow
previously called Mode 3.

## Required intake before any draft

Ask the user for the **video title** if it has not already been supplied. Invite, but do not
require, the user's narrative or plot direction: desired protagonist, relationship, central
event, ending, or continuation from a previous episode. Do not draft from a title invented by
the agent when the user has not provided one. If a title is supplied, do not ask for it again.
The narrative is optional; if absent, develop a proposal from the channel playbook and verified
research. Record the user title verbatim and the optional narrative in `BRIEF.md`; use a stable
video ID for identity rather than relying on the mutable title.

Write all new channel-facing material in English for the English-speaking audience. Do not ask
which output language to use. Preserve source audio and verbatim research quotations in their
actual language; an English narration adapted from another language needs a verified script.
Do not silently rewrite an existing video's approved narration.

## Ground the idea

### First-minute retention and debate — persistent channel rule

Apply this to every new video, including topics outside the body-and-mind profile. Start with a
concrete action, striking consequence, or unresolved crisis in the first 5–10 seconds. Within
roughly 30 seconds, make the premise, personal stakes, and central constraint understandable.
By the end of the first minute, deliver one real story development and open a specific question
or contradiction whose answer requires the rest of the film. Budget and audit this opening at
the requested speech rate; at 150 words per minute, the opening minute is approximately 150
spoken words. Do not spend it on greetings, background lectures, or production explanations.

Favor entertaining speculation, visible incentives, escalating gains and costs, and choices
that reasonable viewers can disagree about. The final question should invite an actual position
on the story's dilemma. Controversy must arise from that dilemma, not fabricated evidence,
unsupported allegations, hostility, or a misleading promise. Clearly distinguish invented
scenarios, financial returns, and assumptions from verified events or measurements. Keep any
necessary qualification brief and integrated into the narrative. Do not force a sermon or a
generic moral onto a topic whose tension is already concrete. Record the hook, opening-minute
word range, open loop, eventual payoff, and debate question in SCRIPT_STATE.md; include them in
the final review.

### Research workflow

1. Identify the managed channel and relevant playbook. Read its `SOURCES.md`,
   `MASTER_SCRIPT_PROMPT.md`, referenced competitor dossiers, `covered-topics.md`, and relevant
   analysis files before ideation. Consume any gap, outlier, audience, and differentiation findings
   produced with $claude-youtube-competitor.
2. Inspect covered topics from the reference channels and all existing `video.json`, briefs, and
   scripts under the managed channel's `videos/`. Do not accidentally repeat an existing angle. A
   broad subject may be reused only when the thesis, period, protagonist, evidence, or audience
   payoff is materially different; state that difference.
3. Research the proposed topic before drafting. Use the internet when a necessary fact is unknown,
   uncertain, niche, disputed, or time-sensitive. Prefer primary and authoritative sources. Preserve
   URLs and access dates in `SOURCES.md`, distinguish fact from inference, and never fill evidence
   gaps from memory.
4. Do not rerun live competitor collection during drafting. If the required competitor evidence is
   missing or materially stale, return to $youtube-channel-research, update the dossier with
   $claude-youtube-competitor, and only then resume this workflow.

For a user-supplied body-and-mind title, also check YouTube for an existing video with the exact
same what-if premise using `$youtube-body-mind-ideas`'s screening rule. Report any direct match
and propose a genuinely different premise, but keep the user's title until they select a change;
do not silently rename their video. A merely adjacent subject does not trigger replacement.

## Select the researched production format

For an archival mystery or found-record story, apply `$youtube-archival-story-production` after
reading the completed research. It supplies narrative/retention/visual methods and the concrete
format profile; this skill retains title intake, managed state, complete-script review, voice
direction and publication delivery. Do not recursively restart either workflow.

The archival playbook must meet its
[contract](../youtube-archival-story-production/references/playbook-contract.md). Reuse or compile
it from completed research once; preserve its profile ID/revision and freeze resolved per-video
inputs. Do not re-download competitors for every new title. User choices override the selected
profile, then bundled defaults; scope-specific identity and approval rules remain authoritative.

Use the selected profile's speech rate for its word budget, recording any departure from the
generic 150-wpm default; the bundled archival profile uses 170 wpm. Replace estimates with measured
audio. Apply the body-and-mind profile below only to that format or an explicitly chosen hybrid.

## Body-and-mind animated film profile

When the channel's playbook or the user's request calls for the faceless “what if your body or
mind worked differently?” format, read the **entire** translated head-writer prompt at
`references/body-mind-head-writer.en.md` before outlining. Apply it alongside this workflow.
The reference is the English adaptation of the user-supplied prompt; the points below
are its project integration, not a substitute for reading it.

- Give the viewer one extreme change to a human ability, with one explicit limitation that
  controls the climax. The ability is fictional; physics, biology, and numerical benchmarks
  outside that premise must be defensible.
- Build the ten-beat arc in the reference: striking opening; normal baseline and rule; escalating
  consequences; hidden cost; important relationship and planted detail; crisis with a hard
  choice; people-first resolution and callback; optional reality check; final hook when a sequel
  is intended; and a fitting call to action. Plan these beats as one coherent complete script.
- Budget spoken length at **150 words per minute**, with **140–160 words per minute** as the
  planning range. For a requested duration of D minutes, target D × 150 spoken words and show
  the estimate D × 140 to D × 160. Ten minutes therefore needs about 1,500 words (1,400–1,600),
  not 900–1,050. Exclude titles, emotion tags, citations, and production notes from the count.
  If no duration is specified, retain the compact 900–1,050-word profile and label it about
  six to seven minutes. A requested duration overrides the reference's old 800–1,100-word cap
  and per-beat ranges. Deepen events and choices rather than adding filler. Verify the final
  word count, report its duration at 150 wpm and its range at 140–160 wpm, and replace estimates
  with the actual audio duration once narration exists. Do not stretch audio to conceal a
  script-length shortfall.
- Write in English, predominantly second person and present tense. Keep dialogue
  as brief, context-clear sentences inside the narration. Use a small cast and preserve prior
  episode names, rules, numbers, relationships, and callbacks when applicable.
- Apply the reference's short-sentence rhythm, meaningful pauses, question density, short
  contrasts, “Normal. And you?” benchmarks, dry humor, and family-friendly tone. Its approximate
  counts—18–25 ellipses and 15–20 questions—are review targets; do not add empty punctuation
  or questions merely to hit a number. Keep voiceover numbers in words except years, use metric
  units, and make abbreviations speakable.
- Verify at least five distinct real facts or benchmarks with their measurement conditions.
  Store source URLs and access dates in `SOURCES.md`. Never invent research, quotes, statistics,
  or a scientific measurement of the fictional extreme. Remove or qualify unsupported numbers.
- The final spoken `script.md` must be one continuous paragraph without act labels, speaker
  prefixes, dialogue quotation marks, camera directions, sound cues, timestamps, emoji, or
  Markdown. Visible on-screen text may be quoted when the story requires it.
  Keep production notes, source citations, and the ten-beat outline outside `script.md`.
- Preserve the reference's character and sequel rules, including an emotionally meaningful
  callback, plausible motives, a people-first choice without revenge, and a CTA matching
  whether the story continues or ends. Adapt its sample names and lines; never copy them as
  the user's story.

This profile does not override the managed video, research, complete-script draft, review,
metadata, or build contracts below. The reference's “one complete script” response format
applies when the final consolidated script is delivered. Draft the whole script without forced continuation stops; once finalized, give the title
and complete spoken script when the user asks for the finished text.

## Create the durable video before drafting

Before drafting, create or reuse exactly one video entity at:

`channels/<managed-channel-folder>/videos/<date>-<video-slug>--<video-id>/`

Use `WorkspaceCatalog` so the readable immutable folder slug, stable ID, `video.json`, and Web UI
follow the same contract. Set `script_status` to `planning`. Create or maintain:

```text
BRIEF.md
SOURCES.md
SCRIPT_STATE.md
script.md
METADATA.md          # only after final script review
```

Record the target language, audience, intended duration and word count, selected angle, and
exclusions. For a Veo/CapCut video also record the intended scene cadence in `BRIEF.md`:
`source SRT`, or a fixed number of seconds per scene (for example 4, 8 or 10). These are
editing windows, not a promise that any amount of speech will fit. Never create another video
entity merely to run the completed script.

## Draft the complete script

The user revoked the mandatory four-interaction rule on 2026-10-23. Draft and review the
complete script in the current production request. Do not stop after a quarter or require
three separate continuation messages. A four-act outline may remain an internal planning aid;
it does not divide the delivered narration. Use staged drafting only when the user asks for it.

1. Plan the full causal arc before prose, including the hook, constraint, cost, choice and payoff.
2. Write the complete narration into one canonical `script.md`; keep notes outside the parser input.
3. Update `SCRIPT_STATE.md` with the total word count, continuity, factual questions and review state.
4. Keep `video.json.script_status` at `drafting` until the complete-script review passes.

## Review and finalize

After drafting the complete script:

1. Set `script_status` to `review`.
2. Reread the complete script and audit factual support, chronology, names and dates, internal
   consistency, duplicated ideas, narrative continuity, pacing, hook and payoff alignment,
   unsupported certainty, originality, and target length.
3. Correct problems and produce a clean consolidated `script.md` without erasing the research trail.
   Keep nonspoken headings and production notes in `SCRIPT_STATE.md` or `BRIEF.md`: the script
   parser treats heading text in `script.md` as words to narrate. For the body-and-mind profile,
   consolidate the final script into one continuous spoken paragraph and audit all its profile
   requirements, including the real-fact ledger, ability rule, planted payoff, and language.
4. Plan the spoken delivery after the text is final. Write `VOICE_DIRECTION.json` for ordinary
   narration and `CAPCUT_VOICE_DIRECTION.json` when `CAPCUT_BEATS.srt` is used. Each file has
   `{"version":1,"segments":[{"id":1,"text":"Exact English cue","style":"curious",
   "intensity":0.5}]}`. IDs and `text` must match the parser's final spoken segments exactly;
   for CapCut they correspond to SRT cues. For ordinary `script.md`, check the parser's sentence
   splitting before writing the JSON. Use one of `neutral`, `warm`, `curious`, `tense`, `excited`,
   `sad`, `angry`, or `whisper`; intensity is 0–1. Choose changes at narrative turns, keep most
   lines restrained, and avoid contradictory or constant high-emotion directions. Convey pacing
   and emphasis in punctuation and sentence length; do not insert delivery tags into spoken text.
   If any spoken line changes, update its direction entry before launch. CosyVoice 3 consumes these
   directions; Chatterbox cannot render the per-line styles and remains neutral.
5. For CapCut/Veo, create or update `CAPCUT_BEATS.srt` with one spoken cue per intended scene.
   Use variable cue times for `source SRT`, or select the fixed cadence in the Web UI at launch.
   Aim for narration that fits within roughly 90% of each scene and leave room for pauses. Review
   the produced `narration.srt` and rewrite overlong lines rather than forcing extreme speed.
6. Only then set `script_status` to `ready`. Synchronize `script_updated_at`, `script_sha256`, and
   `script_word_count` through `WorkspaceCatalog`.

Create `METADATA.md` only after the full-script audit. It must contain:

- one polished YouTube description grounded in the dossier and actual final script;
- chapters when timing information exists;
- four genuinely distinct title options grounded in the title research;
- at least 60 relevant tags or keyword phrases, even if the reference channel exposes no tags;
- a separate prioritized, platform-ready tag subset that fits current YouTube limits;
- a small set of highly relevant hashtags, not the entire tag bank.

Do not put 60 or more hashtags in the public description. Re-check current YouTube metadata and
spam rules before delivery because platform limits change.

## Publication package and finished thumbnail

A request for a **full video package** includes publication assets, not only narration or a
rendered MP4. Finish and explicitly deliver all of the following under the same managed video:

- `TITLE.txt`: one recommended English publication title, plus four distinct options and their
  rationale in `METADATA.md`. Preserve the user's working title and explain the recommendation.
- `DESCRIPTION.txt`: complete copy-ready English description, with a strong opening, the actual
  story promise, measured chapters when available, relevant source/credit links, a few hashtags,
  and the mandatory space-separated SEO tail described below.
- `TAGS.txt`: the prioritized platform-ready tag subset; retain the 60+ keyword bank separately
  in `METADATA.md`. Do not claim tags or a wording choice guarantee reach or CTR.
- A **finished thumbnail image**, not just concepts or a prompt. Use the requested tool; for a
  generated raster thumbnail, apply the installed imagegen skill. Preserve the film's characters,
  inspect the result, and save the selected asset in the video folder. Check current YouTube
  thumbnail format, dimensions, aspect ratio, and upload-size guidance; retain the generation
  prompt and QC in `THUMBNAIL.md`.
- `PACKAGING_RESEARCH.md`: the actual local competitor dossiers, example titles/descriptions,
  and thumbnail images inspected; distinguish observations from untested editorial hypotheses.
  Adapt information hierarchy and the story promise without copying a competitor composition
  or revealing the ending. Use existing completed research; update it only if necessary.
- A package index linking the final video when requested, narration, exact script, per-line
  expression directions, matching subtitles, publication assets, and editable visual project.
  Supply one portable ZIP for a full-package delivery, excluding model weights, caches, and
  unrelated videos. Verify archive contents and paths. Keep large historical runs outside the ZIP.

The final response must name the recommended title, show the finished thumbnail or its preview,
and give direct links to the complete package and copy-ready description. Do not leave the user
to discover publication assets inside an unmentioned metadata file. If a required asset cannot be
produced, state that specific gap rather than calling the package complete.

### Required final SEO line — user-selected package format

The user explicitly requests a substantial SEO keyword tail at the **very end** of the public
description. Prepare it as **one continuous line**, with words and multiword phrases separated
by ordinary spaces. Do not use bullets, numbering, commas, semicolons, tables, or line breaks
inside this line. Do not place credits, links, or hashtags after it. Ordinary keywords do not
need a `#` prefix; keep the small hashtag set earlier in the description.

Build a broad bank of about **80–120 distinct relevant search phrases** in `METADATA.md`, then
select a substantial, varied subset for the final description line within the current total
description limit. Avoid padding with near-duplicates. Cover the actual topic, ability,
characters or setting, genre, audience intent, and relevant technology. For this project's
AI-in-the-brain premise, examples include `Qwen AI in your brain what if animated story
science fiction entertainment artificial intelligence human intelligence programming social
mobility Germany Kenya`. All authored keywords remain English. Spell the model name `Llama`
correctly if used; include it or another adjacent brand only when the video's actual content
supports that connection. Do not promise a model comparison or tutorial absent from the film.

This exact keyword-tail layout is a **user preference, not a platform recommendation**.
YouTube's current help says excessive tags in descriptions violate its spam policy and that
tags otherwise play a minimal discovery role. Keep this limitation visible in the internal
metadata review; never claim that more keywords guarantee reach, ranking, or CTR. Preparing
the requested local draft does not authorize an upload. If a platform-compliant alternative
is requested, convert the tail into useful, naturally written topical prose.

Sources to re-check before delivery:
https://support.google.com/youtube/answer/146402?hl=en
https://support.google.com/youtube/answer/6390658?hl=en

## Choose the visual production route

**Mandatory sound-effects requirement for a rendered video:** add audible, action-specific
sound effects synchronized to the actual narration and animation. Music alone does not satisfy
this requirement. Use appropriate paper, keyboard, interface, object-contact, notification,
phone, movement, and environment sounds where the story calls for them. Keep emotional scenes
restrained; a dramatic sound on every cut is not required. Honor an explicit user request for
no effects, but never silently omit effects from a full-video build.

Write `SOUND_DESIGN.md` with cue times, actions, asset paths or procedural sound names, gains,
and provenance. The delivered MP4 must contain the mixed effects, not only a sound plan or
unmixed assets. Keep narration intelligible, duck backgrounds where appropriate, and verify
the final muxed audio, duration, loudness and peaks. Include the cue map and used local effect
assets in the full package. A full-video delivery is incomplete if these effects are absent
or have only been proposed in text.

After the spoken script is approved, follow the route the user requests:

- **Google Flow route:** the user asks for first frames and motion descriptions to generate the
  video in Flow. Use the next section and `$youtube-flow-visuals` for a style bible, shot prompts,
  and handoff. Do not claim those prompts are rendered video.
- **Archival still assembly:** when the selected archival format calls for photographs and paper
  cards, use `$youtube-archival-story-production` for the asset/graphics/sound handoff. HyperFrames
  or another still renderer requires prerequisite/component review and a technical test; it is not
  an installed Web UI stage or verified Premiere integration. Do not replace this aesthetic with
  stick figures merely because that renderer is available.
- **Create visuals here:** the user asks the agent to make the visual itself. Use
  `$2d-video-plugin` as an optional, code-rendered 2D route after checking its local prerequisites
  and the match between the requested style and its stick-figure aesthetic. Produce preview
  frames and review them before a final render. Preserve the canonical narration and managed
  video ID. Do not silently substitute this route for Flow or claim it is wired into the
  Premiere pipeline.

If the user has not chosen a route, the writing workflow can finish without generating visuals;
ask for the choice only when visual production becomes the next dependent step.

## Visual handoff for Google Flow

If the selected route uses generated first frames and Google Flow clips, treat `VISUAL_BEATS.md` as
the narrative shot map, not as finished generation prompts. After the spoken script and timing
cues are approved, use `$youtube-flow-visuals` to create `VISUAL_STYLE_BIBLE.md` and
`FLOW_SHOT_PROMPTS.md` in the same managed video directory. The project visual skill applies
`$kaomei-stickman-director` as secondary guidance for self-contained character/style anchors,
shot continuity, and concrete motion. It must preserve the canonical narration, existing style
references, and the local TTS/CapCut handoff. Do not substitute the upstream short-video formula
for this complete long-form script.

## Build handoff

When the user asks to build the video, do not ask for the title or script again. In Web UI they select
`Готовый сценарий из каталога`, the managed channel, and the existing ready video. The server reads
the canonical `script.md`, takes an immutable per-run snapshot, and creates a run under the same
video. Manual title and text input belongs only to `Импорт внешнего сценария`.

Available research, content, brand voice, article, and SEO skills may assist, but the researched
playbook and durable video state remain authoritative. Do not install or update third-party skills
or dependencies without explicit user approval.
