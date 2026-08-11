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

Drop audio files into the **`assets/`** directory at the repo root
(`.wav` is safest; anything your player supports works — the Pi uses
`aplay`/`mpg123`, macOS dev uses `afplay`).

Two ways sound gets played:

**Phase-entry sounds** — played once when the show enters a phase:

```toml
[audio]
active = "rumble.wav"      # relative to assets/
imminent = "warning.wav"
```

A phase with no entry (like `dormant`, silent by design) is simply omitted.
The previous phase's sound is stopped on every transition. There is no
looping yet — pick files roughly as long as the phase, or ask for looping
to be added.

**Cue sounds** — fired at exact moments inside the eruption timeline (see
below), e.g. the roar synced to the first flame burst.

Missing files are silently skipped (so sim mode works without assets), but
nothing tells you at show time — test with sound before opening night.

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
| `flame_burst` | `duration` (s) | max 2.0s, min 3s gap (end-to-start); safety channel handled automatically |
| `pars` / `fade` | — | currently no-ops; eruption lighting comes from `[looks.eruption]` |

Timing rules to respect (or the gate denies the cue):

- Flame: each burst ≤ **2.0s**, and ≥ **3s** from the *end* of one burst to
  the *start* of the next. Two bursts at `t=5.0` (0.8s) and `t=9.0` is legal;
  `t=5.0` and `t=7.5` is not — 7.5 − 5.8 = 1.7s gap, denied.
- CO2: each burst ≤ **10s**, ≥ **5s** between bursts.
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
