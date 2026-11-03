# Local avatar

The optional `avatar_overlay` stage assembles timed speech, normalizes a supplied motion video, calls MuseTalk, validates its output, and saves both synchronized portrait and composite video. Inputs, logs, and outputs live in the run's `avatar/` directory. Use `--avatar-mode musetalk --avatar-source avatar-motion.mp4`. GPU/VRAM compatibility and full inference require manual verification; no avatar sample is bundled.

See [installation](installation.md), [usage](usage.md), and [release verification](release.md).
