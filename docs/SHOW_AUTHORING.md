# Show Authoring Guide

Everything about how the volcano looks, sounds, and times its effects lives
in **`config/show.toml`**. Edit, restart the show (`uv run dmx-volcano`), and
watch the result in the visualizer at `http://localhost:8000`. No code
changes are ever needed for content work.

The one rule that outranks this file: **the safety gate wins.** Any cue that
asks for more fire or CO2 than `safety.py` allows is denied at runtime and
logged with `"granted": false`. Check `logs/<date>.jsonl` after a run if an
effect didn't fire — the log tells you exactly which cue was denied and when.

## 1. Sounds

**Importing files.** Drop audio files into the **`assets/`** directory at
the repo root — subfolders are fine, and the corpus convention is
`assets/sounds/<phase>/` — or use the control page's **Audio → Import**
button, which copies files into `assets/` over the network (handy on the
headless Pi). Config references are paths relative to `assets/`
(`"sounds/active/active_loop_1.wav"`). Prefer 44.1kHz/16-bit stereo `.wav`
(the dmix slave format — zero resampling at show time); anything your
player supports works — the Pi uses `aplay`/`mpg123`, macOS dev uses
`afplay`. Accepted types: `.wav .mp3 .ogg .flac .aiff .aif .m4a`.

**Phase audio is layered.** Each phase's soundscape is built from role
pools in `[audio.phases.<phase>]`; every pool picks a file at random each
time it fires (never the same file twice in a row while the pool has
alternatives), so repeat visitors don't hear an identical show:

```toml
[audio.phases.active]
start = ["awaken.wav"]                  # one-shot the moment the phase begins
loop = ["rumble1.wav", "rumble2.wav"]   # the base bed — loops for the whole phase
bg = ["wind.wav"]                       # second looping layer under/over the bed
accent = ["crack.wav", "hiss.wav"]      # one-shots sprinkled in for texture
accent_interval = [12.0, 40.0]          # seconds between accents (uniform sample)
```

| Role | Behavior |
|---|---|
| `start` | played once on phase entry |
| `loop` | picked at random, replayed every time it ends until the phase exits — a multi-file pool re-picks per cycle, so the bed evolves |
| `bg` | same looping mechanics as `loop`; a second concurrent texture layer |
| `accent` | a one-shot fires every `accent_interval = [min, max]` seconds (default `[10, 30]`), random pick each time; at most 3 sound at once |

Every role is optional. A phase with no table (like `dormant`, silent by
design) is simply omitted; a bare `active = ["rumble.wav"]` still works as
shorthand for a `start` one-shot. All of a phase's layers stop on phase
exit. Loop respawning has a few-ms gap at the file boundary — author loop
files with matched head/tail (or a beat of room tone) so the seam hides.

**Relative loudness is authored into the files.** There is no per-layer
gain: normalize the corpus so beds sit low and accents poke through
(e.g. loops around −20 LUFS, accents peaking ~6–10 dB hotter). This is
deliberate — one less thing to tune blind at the venue.

**Transition stingers** — pools keyed by `"<from>-><to>"`, played once, on
top of the new phase's layers, when the show makes exactly that move — the
audio cue that the volcano is advancing:

```toml
[audio.transitions]
"dormant->active" = ["stir1.wav", "stir2.wav"]  # random, like any pool
"active->imminent" = ["escalate.wav"]
```

Every direction is addressable, so an escalation (`active->imminent`) can
sound different from a fizzle (`active->dormant`). Unlisted transitions are
silent.

**Cue sounds** — fired at exact moments inside the eruption timeline (see
below), e.g. the roar synced to the first flame burst.

Files referenced in config but missing from `assets/` are flagged at
startup (console warning + `audio_missing` log event) and shown with a ⚠ in
the control page's Audio panel — playback just skips them, so sim mode
works without assets. The Audio panel also lists imported files not yet
referenced by any pool, and clicking any present file plays it out the
show speaker (the venue line check). Assigning a file is always a
`show.toml` edit + restart; a phase or role typo in `[audio]` refuses to
start, rather than failing silently on show night.

On the Pi, layering needs the dmix ALSA config (`deploy/asound.conf`) —
see `docs/DEPLOYMENT.md` § Audio out.

## 2. Lighting looks

Each phase's ambient lighting is a `[looks.<phase>]` table. Anything you set
overrides the defaults in `show.py`; anything you omit keeps the default.

```toml
[looks.dormant]
color = [255, 0, 0]     # RGB 0-255 for all pars
dimmer = [60, 100]      # brightness floor and ceiling of the pulse
pulse_hz = 0.13         # breathing speed (cycles/second)

[looks.imminent]
color = [255, 190, 80]
dimmer = [0, 255]
beat_hz = [0.6, 2.5]    # heartbeat: starts at 0.6Hz, accelerates to 2.5Hz
fog = 255               # fogger output during the phase (0 = off)

[looks.eruption]
strobe_hz = 8.0         # hard strobe rate
```

Parameter reference:

| Key | Phases | Meaning |
|---|---|---|
| `color` | all | RGB applied to every par |
| `dimmer` | all | `[min, max]` brightness range |
| `pulse_hz` | dormant, active | sine "breathing" rate |
| `beat_hz` | imminent | `[start, end]` accelerating heartbeat rate |
| `strobe_hz` | eruption | on/off strobe rate |
| `fog` | any | constant fogger level for the phase (0–255) |

Per-fixture control (different colors on different pars) isn't exposed in
config yet — that requires a `render()` change in `show.py`.

## 3. The eruption cue timeline

The eruption is a fixed script: `[[cues.eruption]]` entries, each with a
time `t` (seconds after eruption start), an `action`, and that action's
parameters. Entries run in time order regardless of file order.

```toml
[[cues.eruption]]
t = 4.0
action = "co2_burst"
duration = 1.5
```

Action reference:

| Action | Params | Notes |
|---|---|---|
| `sound` | `file` | plays once from `assets/` |
| `fog` | `level` (0–255) | holds until the eruption ends |
| `co2_burst` | `duration` (s) | max 10s, min 5s gap between bursts; consumes tracked CO2 |
| `flame_burst` | `duration` (s) | max 10s, at most 10s of fire in any 13s window, bursts may not overlap; the safety channel opens at `t` and fire rises 0.5s later (`FLAME_ARM_LEAD`), so the flame itself burns over `[t+0.5, t+0.5+duration)` |
| `pars` / `fade` | — | currently no-ops; eruption lighting comes from `[looks.eruption]` |

Timing rules to respect (or the gate denies the cue):

- Flame: each burst ≤ **10s**, and the total fire inside *any* 13s window ≤
  **10s**. Short bursts need no gap between them; a 10s burst must be followed
  by 3s of no fire. Bursts at `t=5.0` (8s) and `t=14.0` (2s) are legal (fire
  `[5.5, 13.5)` + `[14.5, 16.5)`: the window ending at 16.5 holds 8 + 2 = 10s);
  a 3s burst at `t=14.0` is not (window ending 17.5 holds 8 + 3 = 11s).
- CO2: each burst ≤ **4s**, ≥ **5s** between bursts.
- Keep the last pyro cue comfortably before `eruption_duration` — a permit
  can't outlive its burst, but a cue landing after the phase ends never fires.

The whole eruption length is `[phases] eruption_duration`. The warning phase
before it is `imminent_duration` (30s). Both are plain numbers to edit.

## 4. Pacing (when things happen at all)

```toml
[phases]
p_erupt = 0.7             # chance active escalates (vs. fizzling back)
imminent_duration = 30.0
eruption_duration = 20.0
cooldown = 60.0           # quiet time after each eruption

[phases.dwell.dormant]
t_min = 120.0             # hard floor, seconds
t_max = 600.0             # hard ceiling
k = 2.0                   # Weibull shape; >1 = more likely as time passes
scale = 300.0             # roughly "typical" dwell, seconds
```

To make eruptions more frequent: lower `scale` values and/or raise
`p_erupt`. After a night, compare the log's `sampled_dwell` vs. manual
trigger timestamps to see whether the crowd wanted faster pacing than the
sampler delivered.

## 5. Testing a change

1. `uv run dmx-volcano`, open `http://localhost:8000`.
2. Use **Trigger eruption** — you'll get the full 30s imminent warning and
   then the timeline, with pyro channels visible in the visualizer (flame
   only renders when its safety channel is in the armed window).
3. `uv run pytest` — the engine tests will catch a timeline that violates
   safety limits.
4. Check `logs/` for any `"granted": false` cues.
