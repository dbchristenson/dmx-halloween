# Show Authoring Guide

Everything about how the volcano looks, sounds, and times its effects lives
in **`config/show.toml`**. Edit, restart the show (`uv run dmx-volcano`), and
watch the result in the visualizer at `http://localhost:8000`. No code
changes are ever needed for content work.

The one rule that outranks this file: **the safety gate wins.** Any cue that
asks for more fire or CO2 than `safety.py` allows is denied at runtime and
logged with `"granted": false`. Check `logs/<date>.jsonl` after a run if an
effect didn't fire — the log tells you exactly which cue (or `flame_pattern`)
was denied and when.

## 1. Sounds

**The show corpus is rendered, not dropped in.** Downloads go in
`assets/raw/`; `uv run tools/prep_audio.py` renders them into
`assets/sounds/` per the recipe in `tools/audio_prep.toml` — trims, fades,
seamless loops, per-phase loudness, sub protection (`docs/AUDIO.md`). To add
or change a show sound, edit the recipe and re-render. For a quick one-off,
the control page's **Audio → Import** button still copies a file into
`assets/` over the network. Config references are paths relative to
`assets/` (`"sounds/active/bed_lava_rumble.wav"`). Prefer 44.1kHz/16-bit stereo `.wav`
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

Every role is optional; a phase with no table is simply silent. A bare
`active = ["rumble.wav"]` still works as shorthand for a `start` one-shot.
On phase exit the `loop`/`bg` layers stop; one-shots (`start`, accents,
stingers, cue sounds) ring out to their own end. Loop respawning has a
few-ms gap at the file boundary — the recipe's `loop = true` crossfades each
bed's tail into its head so the seam hides.

**Relative loudness is authored into the files.** There is no per-layer
gain: the recipe's `[tiers]` set it (`docs/AUDIO.md` § Loudness). This is
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
below), e.g. the roar synced to the big CO2 burst. `file` takes one file or
a pool.

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
overrides the defaults in `lighting.py`; anything you omit keeps the default.
`config/show.toml` spells out every key, so start from there.

Design principles. Keep new looks inside these:

- One dominant color sets each phase's mood; a minority of accents adds texture.
- Nothing moves in unison: every light runs its own rate, offset and choices.
- Use the whole range: deep lows and full highs.
- Each phase is faster and hotter than the one before it.
- Phase changes are events: snap to black, then sweep in.
- The outer pars frame the volcano and stay warm. The crater is where the
  eye goes, so it gets the wild color and the white blasts.

Every "random" choice (rates, offsets, which accent, each beat's color) is
a fixed hash of the light and the moment, so the show looks organic but
tests can pin it down.

There are two groups. The **outer pars** (`par*`) are described below. The
**caldera** is the crater lights plus the fogger's LEDs, which color the
smoke. Its keys live in `[looks.<phase>.caldera]`; any key missing there
falls back to the phase's own.

- **Dormant / active (breathing):** each par breathes at `pulse_hz`, give
  or take 30%, with its own offset, through the full `dimmer` range. The
  pars take turns drifting from `color` into one of the `accents`, in a
  shuffled stagger, so only about `accent_share` of them are accented at
  any moment.
- **Imminent (heartbeat):** the beat accelerates over `beat_hz`. Each beat
  reaches each par at its own random delay, so it ripples across the
  volcano. Each hit takes a random color from `palette` and a random peak.
- **Eruption (strobe):** each par strobes at `strobe_hz`, give or take 30%,
  out of step with the others. Each flash picks a color from `palette` and
  a brightness within `flash`.
- **Caldera:** dormant is a low sulfur-blue glow with violet and green
  accents. Active cycles each crater light through the firework palette
  in its own order. Imminent uses the same cascading heartbeat as the
  pars, in firework colors. Eruption is all white to light the CO2: white
  pops, then solid full white through every CO2 cue window from the
  timeline, armed or not. The fogger's LEDs have no dimmer, so their
  color is scaled instead.
- **Phase change:** every phase entry blacks the outer pars out for
  `[looks.transition] gap`, then flashes them to full one at a time
  (par1 to par6, `step` apart) in the phase's `hit_color` (default: its
  `color`), then the crater last. Each flash decays into the new look over
  `decay`.

| Key | Phases | Meaning |
|---|---|---|
| `color` | dormant, active | dominant RGB |
| `dimmer` | all | `[min, max]` brightness range |
| `pulse_hz` | dormant, active | breathing rate (cycles/second) |
| `accents` | dormant, active | RGB colors the minority drifts into |
| `accent_share` | dormant, active | fraction of pars accented at once (0–1) |
| `accent_hz` | dormant, active | how often each par's accent episode comes around |
| `beat_hz` | imminent | `[start, end]` accelerating heartbeat rate |
| `strobe_hz` | eruption | average strobe rate |
| `palette` | imminent, eruption | colors each beat or flash picks from |
| `flash` | eruption | `[min, max]` brightness of a flash, 0–1 |
| `hit_color` | any | phase-change flash color |
| `caldera.hold_s` / `caldera.fade` | active | how long each crater light holds a color, and the crossfade share |
| `caldera.pop_hz` | eruption | white pops per second, per crater light |
| `caldera.blast_tail` | eruption | white falloff after each CO2 cue, seconds |
| `fog` | any | fogger level for the phase (0–255) |
| `fog_on` / `fog_off` | any | duty cycle in seconds: fog for `fog_on`, rest for `fog_off`, starting on at phase entry. Omit both for constant fog |
| `[looks.flame] color` / `tail` | — | every light flares to `color` on each flame pulse and fades back over `tail` seconds, armed or not |

**Fog duty cycles.** Running the pump cools the fogger's heater. Fog too
long and the MK-F01 stops itself to reheat (10–30s, typically), and DMX
can't tell the show it has. Size `fog_on`/`fog_off` from the bench numbers
(DESIGN.md open items) so it never stalls, least of all in the eruption.

## 3. Flame patterns

The flame fires "flame balls": short pulses in morse-like rhythms. A
pattern is a string of `.` (dot) and `-` (dash); spaces are ignored, so
`"- . . -"` and `"-..-"` are the same.

```toml
[flame]
dot = 0.15               # pulse lengths, seconds
dash = 0.45
gap = 0.25               # dark time between pulses
clear_before_co2 = 1.5   # the finale ends this long before the first CO2 cue

[flame.transitions]
"dormant->active"    = ["-..-", "-.-"]
"active->dormant"    = ["."]            # fizzle
"active->imminent"   = [".--..-"]
"dormant->imminent"  = [".--..-"]       # manual trigger
"imminent->eruption" = ["...---"]       # the finale
"eruption->dormant"  = ["-.-"]          # the aftermath

[flame.imminent]
patterns = ["-.-.", "..--.."]
rest = [1.0, 2.5]        # random pause between patterns
```

- A transition pattern fires as the show makes exactly that move, at the
  start of the new phase. Each pick is random, never the same pattern
  twice in a row.
- In imminent: the entry pattern, then `[flame.imminent]` patterns back to
  back with a random `rest`, then the finale. The finale plays at the end
  of imminent and finishes `clear_before_co2` before the eruption's first
  CO2 cue; a finale that can't fit is refused at startup.
- No flame in the eruption: the gate refuses flame for 10s after any CO2
  burst. `"eruption->dormant"` (the aftermath) waits that out: its first
  fire lands 10s after the eruption's last CO2 burst ends, early in
  dormant. Holding, pausing or opening test mode before then cancels it.
- An operator hold's active → dormant fires nothing.
- Each pattern opens the safety channel 0.5s before its first pulse and
  holds it to the last; fire rises only inside a pulse. The gate grants the
  whole pattern or none of it, and counts every pulse toward the flame
  budget (10s of fire per 13s).
- Every pulse must be at least 0.15s (`safety.MIN_PULSE`) and at least
  0.25s after the previous one (`safety.MIN_GAP`), both from the bench:
  shorter pulses lit weakly, and back-to-back pulses left the third unlit.
  A pulse that fails to ignite releases unburnt gas.

To try a pattern on the hardware, use the test console's **Fire pattern**
row: type the pattern and set dot/dash/gap there, without editing config.

## 4. The eruption cue timeline

The eruption is a fixed script: `[[cues.eruption]]` entries, each with a
time `t` (seconds after eruption start), an `action`, and that action's
parameters. Entries run in time order regardless of file order.

```toml
[[cues.eruption]]
t = 1.0
action = "co2_burst"
duration = 0.5
```

Action reference:

| Action | Params | Notes |
|---|---|---|
| `sound` | `file` (one file or a pool) | plays once from `assets/`, rings out past the eruption |
| `fog` | `level` (0–255) | holds until the eruption ends (overrides `[looks.eruption] fog`) |
| `co2_burst` | `duration` (s) | max 6s, min 5s gap between bursts; consumes tracked CO2 |
| `pars` / `fade` | — | currently no-ops; eruption lighting comes from `[looks.eruption]` |

The eruption has no flame: the CO2 would smother it (section 3).

Timing rules to respect (or the gate denies the cue):

- CO2: each burst ≤ **6s**, ≥ **5s** from one burst's end to the next one's
  start. In the 12s eruption that fits two bursts: a 0.5s pop, then the
  5s big one.
- Keep the last pyro cue comfortably before `eruption_duration` — a permit
  can't outlive its burst, but a cue landing after the phase ends never fires.

The whole eruption length is `[phases] eruption_duration`. The warning phase
before it is `imminent_duration` (30s). Both are plain numbers to edit.

## 5. Pacing (when things happen at all)

```toml
[phases]
p_erupt = 0.7             # chance active escalates (vs. fizzling back)
imminent_duration = 30.0
eruption_duration = 12.0
cooldown = 60.0           # quiet time after each eruption

[phases.dwell.dormant]
t_min = 180.0             # hard floor, seconds
t_max = 780.0             # hard ceiling
k = 2.0                   # Weibull shape; >1 = more likely as time passes
scale = 430.0             # roughly "typical" dwell, seconds
```

These values give about 5 automatic eruptions an hour, the CO2 budget's
target; manual triggers come on top.

To make eruptions more frequent: lower `scale` values and/or raise
`p_erupt`. After a night, compare the log's `sampled_dwell` vs. manual
trigger timestamps to see whether the crowd wanted faster pacing than the
sampler delivered.

## 6. Testing a change

1. `uv run dmx-volcano`, open `http://localhost:8000`.
2. Use **Trigger eruption** — you'll get the full 30s imminent warning and
   then the timeline, with pyro channels visible in the visualizer (flame
   only renders when its safety channel is in the armed window).
3. `uv run pytest` — the engine tests will catch a timeline that violates
   safety limits.
4. Check `logs/` for any `"granted": false` cues.
