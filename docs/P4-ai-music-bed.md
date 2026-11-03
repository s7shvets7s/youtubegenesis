# Music bed

The `music_bed` stage selects approved local tracks, optionally searches Jamendo, and writes plans, credits, and an audio stem under the run's `music/` directory. Modes are `off`, `local`, and `hybrid`. Unknown or incompatible licenses are rejected. No safe track yields `no_safe_music` without blocking export. Added music and original background remain separate tracks and may need manual mixing.

See [installation](installation.md), [usage](usage.md), and [release verification](release.md).
