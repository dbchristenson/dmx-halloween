# Audio

How the volcano's sound is made, played, and where it goes next. Config
reference for the pools lives in `docs/SHOW_AUTHORING.md` § 1; wiring and
venue level-setting in `docs/DEPLOYMENT.md` § Audio out.

## The corpus pipeline

```
assets/raw/<phase>/*.wav      downloads, untouched (+ readme.md: notes per file)
        │  uv run tools/prep_audio.py      (recipe: tools/audio_prep.toml)
        ▼
assets/sounds/<phase>/*.wav   show files — the only thing config references
        │  rsync to the Pi (docs/DEPLOYMENT.md § Update workflow)
        ▼
config/show.toml [audio]      pools: which file plays when
```

Both asset folders are gitignored (licensed audio, large); the recipe is the
versioned record of every cut and level. `prep_audio.py` regenerates all of
`assets/sounds/` and deletes any .wav the recipe no longer produces — never
hand-edit or hand-drop files there. Raw files are never played: the asset
listing and the Audio panel's preview both refuse `assets/raw/`.

Every output goes through: stereo 44.1kHz/16-bit → DC removal + 30Hz
high-pass → trim → loop crossfade (beds) or fades (one-shots) → loudness
normalize to its tier → peak limit to −1 dBFS. `--check` reports without
writing; any output needing more than 6dB of limiting is flagged (its tier is
too hot for the material).

### Loudness

Tiers (LUFS, `[tiers]` in the recipe) step up per phase so the show builds:

| Phase | bg | bed | hits (accents, stingers into it) |
|---|---|---|---|
| dormant | −33 | −30 | −26 |
| active | −27 | −24 | −20 |
| imminent | −22 | −19 | −15 |
| eruption | −16 | — | −12 (tail stinger −15) |

plus `master = −6` on everything: dmix sums the layers and saturates past
full scale, and the worst stacks (imminent bed + bg + stinger + 3 accents;
eruption start + bg + both cues + a ringing imminent accent) measured
+5.4dB over the files' own peaks. With the master trim they peak at −0.6 and
−1.4 dBFS. Make the level up on the TS408 gain, never by raising `master`.

### Speaker protection

The Polk PSW10 sub (50W home amp, no confirmed limiter, HARDWARE.md § 6) is
the fragile part. The 30Hz high-pass strips what it can't play but would
strain on (some raw beds were 30–40% sub-30Hz energy; one raw file carried a
−0.1 DC offset), and the limiter + master trim keep the mix from clipping.

## How playback works today

`audio.py`: every sound is its own `aplay` process (afplay on the Mac); the
Pi's dmix (`deploy/asound.conf`) mixes them. `LayeredPlayer`, ticked each
frame:

- **loop / bg**: respawned when the process exits (new random pick). Stopped
  hard at a phase change — the incoming stinger masks the cut.
- **One-shots** (stingers, `start`, accents, eruption cue sounds): ring out
  to their own faded end across phase changes; only shutdown kills them. So
  the eruption's tail decays under the `eruption->dormant` stinger.
- Eruption cue sounds (`[[cues.eruption]] action = "sound"`) take one file or
  a pool.

Known limits of the process-per-sound design:

- **No runtime gain**: no fades, no ducking, no master volume from the web
  page — all loudness is baked into the files.
- **Loop seams**: the recipe crossfades each bed's tail into its head, but
  the respawn still has a few-ms gap (process exit → next frame → new
  process).
- **Loops cut dead** at phase changes (masked by stingers, audible on the
  unstingered `imminent->eruption`, where the eruption start covers it).
- **Timing jitter**: process startup is tens of ms and unmeasured — fine for
  ambience, too loose for tight light/sound sync.
- **Sim fast-forward** doesn't speed audio up (REVISIT.md #8).

## Next: in-process mixer (planned, not started)

Replace per-sound processes with one in-process mixer — a single audio
stream (e.g. `sounddevice`/PortAudio on ALSA, numpy mixing in the callback,
files preloaded into RAM: the whole corpus is ~40 short files). That one
change unlocks:

- **Fades and crossfades** on phase change instead of hard stops; sample-
  accurate seamless loops (no respawn gap).
- **Per-layer and master gain** at runtime, including from the web page (a
  venue volume trim without touching the speaker), and ducking (beds dip
  under a stinger).
- **A clipping guard**: a master limiter in the mix, so `master = −6`
  headroom can come back as loudness.
- **A sample clock** the show can read: the basis for light/audio sync —
  know exactly when a sound's hit lands and schedule DMX against it.
- Sim fast-forward could time-scale or mute audio coherently.

Design constraints to keep:

- Audio must never stall the frame loop or the safety path: the mixer runs
  in its own (PortAudio) thread; the frame loop only enqueues commands.
- A mixer crash must leave the show running silently, like a missing file
  does today (log it, keep DMX going).
- Keep the `SoundBank` pool/selection layer and the `[audio]` config shape;
  only `LayeredPlayer` and `play()` change.
- dmix stays useful for the venue line check from a shell, but the show
  would own the device.

### Light + audio sync ideas

The user has light/audio sync ideas (more complex than the above) to design
together with the mixer — capture them here when that work starts.
