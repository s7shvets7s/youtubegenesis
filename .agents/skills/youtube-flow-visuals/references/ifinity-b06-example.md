# Worked example: Ifinity B06

This is one improved prompt pair based on the approved B06 reference image inspected on
2026-10-19 and the exact cue in the managed video's `CAPCUT_BEATS.srt`. The Flow result has
**not** been generated or evaluated. Do not substitute this single sample for the full 60-shot
continuity review.

## Shot record

- ID and window: B06, 00:50–01:00, 16:9.
- Exact local narration: “Before the vision ends, you step toward the next gate. The future
  changes instantly. This time, the gate opens, and you pass through.”
- Story job: Show the red rejected gate being replaced by a reachable green path when the
  protagonist changes position. The vision reveals a likely outcome, not a guaranteed one.
- Inspected frame: round pale face with small black profile mark; dark navy jacket, black pants,
  light sneakers, cyan wrist ring; camera behind protagonist; blocked red gate on left and green
  gate on right; deep navy station with cyan backlight and two distant commuters. Existing cyan
  `10` in the upper-right must stay unchanged if this actual frame is animated.

## First-frame image prompt, if the frame must be regenerated

> Wide 16:9 cinematic 2D illustration, viewed from slightly behind the protagonist at about
> waist height. Keep the established Ifinity character: smooth pale round head with a thin dark
> outline and one small black profile eye, no hair; dark navy jacket with restrained cyan trim,
> dark trousers, light sneakers, and a cyan wrist ring. Exactly two arms and two legs, stable
> proportions and clean line weight. Center the protagonist between two waist-high subway
> turnstiles: the left reader glows red and its arm blocks the lane; the right reader glows green
> with the route ahead visible. Place the gates large in the foreground, the protagonist in the
> middle ground, and only two soft commuter silhouettes against a cyan-lit opening in the deep
> background. Deep midnight-blue architecture, cyan edge light, controlled reflections on the
> tiled floor, one small warm accent far left. The protagonist has just turned attention from
> the red gate toward the green one; one foot is poised to step right. The image communicates
> a changed choice in one glance. Keep the station geometry crisp and the figures stylized 2D,
> with no extra people, no invented signage, and no readable text. Add any countdown graphic
> in the editor after image generation.

## Image-to-video prompt for the inspected B06 frame

> Animate the supplied 16:9 frame as a restrained 2D shot. This image is the exact first frame
> and the authority for character design, gate positions, colors, lighting, and composition.
> Preserve the protagonist's pale round head, navy jacket, dark trousers, light sneakers, cyan
> wrist ring, body proportions, and line weight. Keep the left gate red and blocked, the right
> gate green, and the two distant commuter silhouettes in place. Begin with a brief glance toward
> the red reader, then turn head and shoulders toward the right. Shift weight and take one clear
> step toward the green lane. Let only the right turnstile arm rotate enough for passage as the
> protagonist moves through; end on a stable frame just beyond the right gate. Camera stays
> nearly locked, with at most a gentle rightward follow that does not bend architecture. Keep
> the existing upper-right `10` rigid and legible; do not redraw its digits. No new people or
> objects, no duplicated limbs, no face change, no morphing turnstiles, no moving walls or text,
> no cut. Generate no voice, captions, or music; the approved local WAV and SRT supply them.

Before using this with a provider, fit the action to the provider's actual clip duration. The
ten-second narration window does not prove that a single generated clip will last ten seconds.
