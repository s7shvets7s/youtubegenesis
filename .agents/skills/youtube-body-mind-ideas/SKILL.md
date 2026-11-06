---
name: youtube-body-mind-ideas
description: Generate 35 original, scientifically explorable body-and-mind what-if video titles after checking YouTube for videos with the exact same premise. Use before script production when the user asks for ideas for this channel.
---

# Body and mind video ideas

This is the English adaptation of the user's topic-generation prompt, with the user's additional
YouTube screening rule and English output default. Do not treat examples as ideas to output.

## Mandatory YouTube premise check before suggesting any title

Before presenting ideas, search YouTube for existing videos with **the same central premise** as
each candidate: the same human ability or perception changed in materially the same way and to a
comparable extreme. Search the target language and useful English synonyms, including numeric
variants (for example, “ten times faster” and “10x faster”). Inspect the actual video title and
description, and the video when necessary to resolve ambiguity. A broad shared subject alone is
not a duplicate; an existing video that explores the same specific what-if premise is.

If such a video exists, discard the **idea**, not just its wording, and invent a different premise.
Repeat the search for every replacement until 35 distinct candidates have passed. Check the user's
used-title list and the managed channel's covered topics as well. Read and maintain the channel's
`research/body-mind-ideas/OCCUPIED_TOPICS.json` and its readable `OCCUPIED_TOPICS.md` companion.
Append already-covered premises with their video URLs or IDs, search date, evidence, and matching
scope; preserve previous entries. Separate confirmed covered premises from unresolved near
matches. Consult this cumulative registry before every future selection, while still checking
new candidates live. Shared broad subjects alone do not justify rejecting a different premise.
Record search date, query,
matching video URL or ID, and reject/keep decision in the channel's research notes when working in
this project. YouTube search is not proof that no video exists; say “no matching video found in the
queries checked,” never claim universal originality. If live YouTube search is unavailable, do not
present titles as screened or silently skip this gate. Report the unavailable check and resume
only when it becomes possible.

## Translated topic-generation prompt

You are responsible for choosing topics for a faceless YouTube channel with voiceover. The channel
centers on one question: what if your body or mind worked differently? Each video takes one
ordinary human ability—intelligence, the perception of time, reaction speed—and pushes it to an
extreme. The viewer then discovers the consequences step by step, grounded in real science.

The channel's main appeal is that the viewer is the test subject. It is a fantasy of having a
superpower, but one grounded enough in biology, neuroscience, and physics to help viewers
understand their own bodies.

Generate 35 new video titles for this channel.

### Language of the material

Write every title in English for the channel's English-speaking audience. Do not ask a language
question. Keep the language consistent across all titles.

### Example titles and channel tone

- “What if your IQ were 500?”
- “What if one second felt like an eternity?”
- “What if one second felt like ten?”
- “What if you reacted ten times faster?”

### Features shared by every title

- Every title begins with “What if” and ends with a question mark, without exception.
- Every title changes something in the viewer, not in the world or another person. Address the
  viewer as “you” or “your,” or describe a universal human experience such as the feeling of one
  second.
- Push the change to a vivid limit, usually with a specific number: IQ 500, ten times faster, a
  second feeling like ten, or a second feeling like eternity. A number should make the scenario
  immediately imaginable.
- The meaning must be clear within two seconds. No jargon, long preambles, or specialist science
  terms in the title.
- The tone is curiosity and gentle wonder, like an engaging thought experiment. Avoid horror and
  outrage bait. Do not use words equivalent to “Shocking,” “Terrifying,” or “Horrifying.” No emoji
  or words entirely in capitals, except ordinary abbreviations such as IQ.
- Use sentence case: capitalize only the first word, proper names, and established abbreviations;
  do not use title case.
- Aim for six to eight words. Allow up to ten when natural phrasing needs it. Do not sacrifice
  natural language for a mechanical word count.

### Three title patterns

Mix all three patterns. Adjust grammar for natural language while keeping the pattern's meaning
and the mandatory opening.

1. “What if you had [an extreme level of an ability]?” Example: “What if your IQ were 500?”
2. “What if [a unit of ordinary experience] felt like [an extreme]?” Example: “What if one second
   felt like eternity?”
3. “What if [your ability] became [number] times [faster / stronger / sharper / slower]?” Example:
   “What if you reacted ten times faster?”

### Selection rules

- Change exactly one ability or feature of perception. Do not combine two changes in one title.
- Stay with the human body and mind and abilities pushed beyond natural limits. Avoid space,
  planets, animals, history, or the end of the world unless the story directly concerns the
  viewer's body or senses.
- Amplify or reduce something humans already have: intelligence, memory, attention, time
  perception, reaction, reflexes, sight, hearing, smell, taste, touch, pain sensitivity, need for
  sleep, strength, metabolism, pulse, body temperature, emotions, aging, healing, lung capacity,
  or balance. Spread ideas across abilities instead of focusing on one.
- Prefer round, expressive values: ten or one hundred times, 500, one second, eternity, zero.
  Both directions work: faster or slower, stronger or weaker, pushed to the limit or fully off.
- Occasionally revisit a strong idea at a different level, such as a second feeling like ten and
  later like eternity. No more than one fifth of the list—at most seven of 35—may be such
  variations. They must not reproduce the examples above.
- Each idea must support an entire video. Keep only premises that allow several layers of
  consequence: everyday life, body or brain processes, and changes in how the world feels.
  Consider both benefits and the hidden cost. Reject ideas exhausted by one sentence.
- Do not repeat or paraphrase the examples above, topics already proposed in the current
  assignment, or any titles in the list below when it is populated.

ALREADY USED TITLES: [INSERT LIST OR LEAVE EMPTY]

### Response format

After completing the YouTube check, give a numbered list of exactly
35 titles. Titles only: no explanations, introduction, or comments.
