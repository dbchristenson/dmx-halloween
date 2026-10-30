# assets/

`raw/` — audio downloads, untouched, with per-file notes in `raw/readme.md`.
`sounds/` — the show corpus, **rendered** from `raw/` by
`uv run tools/prep_audio.py` (recipe: `tools/audio_prep.toml`): 44.1kHz /
16-bit / stereo WAV leveled per phase. Don't hand-edit `sounds/` — the
script regenerates it and deletes anything the recipe doesn't produce.
Both are gitignored; see docs/AUDIO.md.

Config/show.toml references `sounds/...` paths in `[audio.phases.<phase>]`
role pools, `[audio.transitions]` stingers, and `[[cues.eruption]]` sound
cues. The control page's Audio panel can still import a one-off file into
`assets/` and preview any rendered file out the speaker.
