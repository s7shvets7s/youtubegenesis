# Archival production playbook contract

Use when synthesizing completed competitor research and entering production. These are agent
workflow documents, not new application models, manifest fields or Web UI features. Actual
channel playbooks and video snapshots remain private under `channels/`.

## Required playbook

```text
playbooks/<content-format>/
  SOURCES.md
  MASTER_SCRIPT_PROMPT.md
  FORMAT_PROFILE.json
  GRAPHICS_CATALOG.md
```

`SOURCES.md` maps adopted patterns to dossier/video IDs, timestamps or sample windows, method,
coverage, confidence and date. Separate observation, adopted default and untested improvement.
Explain conflicting references instead of averaging them into a generic style. A preset is not
a measurement; identify the selected model of the format.

`MASTER_SCRIPT_PROMPT.md` is the usable narrative recipe: audience promise, opening, beat behavior,
questions/re-hooks/plants, voice, clue reinterpretation, CTA/ending, fact/fiction handling and
complete-script review. Give episode inputs and outputs. Reference numeric rules by profile ID
and revision instead of maintaining contradictory prose copies. No mandatory four-interaction gate.

`FORMAT_PROFILE.json` follows the complete shape of [the bundled preset](archival-mystery-v1.json):

| Required group | Concrete decisions |
|---|---|
| identity | version, profile_id, revision, provenance, setting origin |
| delivery | default duration, aspect ratio, dimensions, fps, style-preview selection |
| narration | voice, speech rate/range, ordinary and protected-pause treatment |
| narrative_beats | stable IDs, positive weights totaling 100, distinct narrative purposes |
| retention | hook/stakes/open-loop deadlines, question interval/count, motif frequency/basis, plant/payoff and CTA rules |
| visuals | shot shares/denominator, section cadence, minimum duration, transitions, motion, motif exceptions |
| image_prompts | medium-specific templates, length, anchors, monochrome scope, export mapping |
| graphics | palette/fonts, frequency, timing/tolerance, layering and component recipes |
| sound | ambience/spot palette, ducking, repetition limits, loudness/peak delivery targets |
| execution | asset/narration/alignment/render choices, dependency and completion boundaries |

Do not fill required decisions with `TBD`, `same as reference` or an unsupported model alias.
A provider may be `unselected` until that stage is requested; this is not a script-only blocker.
Explain intentional omissions and adopted choices in SOURCES.md, without inventing findings.

Every number has an explicit unit/denominator. Shot-count shares, generated-prompt shares and
screen time differ. Overlays are separate from base shots. Log rebalancing of soft share targets
when hard limits or asset availability require it. For example, licensed contemporary footage
can satisfy present-day shots without increasing the generated-color prompt share.

`GRAPHICS_CATALOG.md` expands relevant component recipes: stable ID, narrative use, literal-text
source, layout, typography, entry/hold/exit, anchor policy, layers and implementation/preview
status. The catalog does not require every component in every episode.

## Evidence behind the recipe

In research, record media metadata and overview contact sheets, then densely inspect the hook,
transitions and representative graphics. Use temporal strips/clips for entry/hold/exit; a
ten-second grid cannot measure three-second shots or subsecond entrances. Save sample timestamps
and limits. Listen to audio; a mixed pause is not a clean music stem. Report supported strengths
and weaknesses without manufacturing five faults to meet a quota.

This supplements the parent research skill's catalog and full-transcript checks. Captions/cloud
services do not silently replace its required local media transcript. Preserve the chosen format's
structure and editing grammar while creating new stories/assets.

## Resolve and freeze one video

Within project policy, current user settings override the selected profile, then bundled defaults.
The parent lifecycle keeps identity, canonical narration, review and package requirements. Surface
an unresolved conflict involving approved speech or a major route instead of silently changing it.

Save the resolved brief and a `production-inputs/r001/` snapshot of all four playbook files under
the existing managed video. Write `PRODUCTION_INPUTS.json` with:

- version 1, stable channel_id/video_id, input_revision, profile_id/revision, snapshot directory;
- each snapshot file's SHA-256 and the parent/genre skill revisions;
- user title, duration, speech rate, per-video overrides and reasons;
- requested tier: `script`, `visual_handoff`, `rendered_mp4` or `premiere_package`;
- selected asset/narration/alignment/render routes; `unselected` until needed is allowed;
- actual model/voice IDs, reference hash and generation settings when selected; seed if supported;
- approved script hash, accepted audio/asset hashes and actual renderer/font/tool versions;
- stage status and unresolved requirements, without secrets or assumed successful checks.

Use video-relative paths. Actual registered run artifacts remain run-relative. Sidecars do not
extend `video.json` or `manifest.json`. Copy an authorized external production asset into the video
or record its source separately; an absolute external path is not a portable artifact.

Use a new snapshot revision for a deliberate brief/profile change and mark dependent work stale.
Never overwrite earlier snapshots or invent a run just to hold these documents. A resumed video
uses its snapshot and accepted assets; a new video can select the current channel profile.

## Readiness by tier

- **Research:** parent evidence checks pass and the four playbook files agree.
- **Script:** user title, complete-script review, word budget/payoffs and parent ready state pass.
- **Visual handoff:** exact script hash, style bible, shots/prompts/maps and graphics/sound cues;
  estimated timing or absent final audio is explicit.
- **Render:** actual accepted assets/audio, aligned cues, supported components, protected pauses,
  transition handles and a passed technical test for the selected route.
- **Final:** inspect the actual export and requested publication/editable package. Script or prompt
  completion is not final-render or Premiere-import verification.
