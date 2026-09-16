# DMX Volcano — Design

A ~10ft wooden-frame volcano for a haunted walk-through display, controlled by a
Raspberry Pi driving one DMX universe. Target: Halloween, October 31 of this year.
Safety is the number one priority — every design decision below defers to that.

## Hardware

| Device | Role | DMX | Status |
|---|---|---|---|
| Raspberry Pi 4/5 | Runs show engine, web UI, audio | — | to purchase/confirm |
| Enttec DMX USB Pro (or equal) | USB→DMX with hardware frame timing | — | to purchase |
| MOKA SFX H-E01A | LPG single-head flame machine | ch1 safety (102–153 = armed), ch2 fire; possibly ch2–6 per one source — **confirm from printed manual** | shipping from China |
| MOKA MK-C09 | CO2 jet cannon | 1 channel (trigger) | owned |
| MOKA MK-F01 | 1500W fogger + 24×3W RGB LEDs | ~8 channels (sibling H-F01 is 8ch) — **confirm from printed manual** | owned |
| ~8× DMX PAR fixtures | Amber/red mood lighting in/around cone | 3–8 ch each | to purchase — require documented channel map and blackout-on-DMX-loss |
| Speakers | Eruption/ambient audio | — | owned; wired aux from Pi (no Bluetooth — latency/dropout) |

- One DMX universe, all fixtures daisy-chained. ~40Hz full-universe refresh.
- Pi joins the on-site Starlink WiFi; operators' phones reach the control page at
  the Pi's local address. No hotspot mode needed.
- Addressable pixel strips (crawling lava) are explicitly **out of scope for v1**;
  possible stretch goal later.

## Safety invariants

These are non-negotiable and reviewed against every change:

1. **Physical arming is per-night and hardware.** An arm switch/valve at the
   volcano plus an easily accessible emergency shutoff. Software can never
   override a hardware disarm. Fire extinguisher staged; operators within a
   couple dozen feet all night.
2. **Firing is programmatic but gated.** The H-E01A's own safety channel
   (ch1 must hold 102–153) is the electronic interlock. `safety.py` sits *below*
   the cue layer: flame and CO2 channels are clamped to 0 unless an approved,
   time-bounded cue is executing. No script can bypass it.
3. **`pyro_enabled` config flag, off by default.** When off, flame/CO2 are
   hard-clamped to 0 even with hardware armed — used for all rehearsal.
   Nightly arming is two-step: physical switch, then a confirmed web toggle.
4. **Restarts come back disarmed.** Re-arming is always a human act, never
   remembered state. The show engine (systemd) auto-starts into dormant with
   pyro disabled.
5. **CO2 operational limits encoded in software:** bursts ≤4s, ≥5s between bursts, siphon/dip-tube tank required, 3m clearance in
   spray direction.
6. **DMX-loss behavior is bench-tested per fixture** (kill signal mid-effect,
   observe) before pyro ever runs on a show night. Not documented publicly for
   any of the three Moka units. Flame machine gets a supervised bench test on
   arrival, and its tip-over shutoff (45°) is verified.

## Show design

Four-phase stochastic state machine:

- **Dormant** (dominant): soft amber/red PAR glow, silence.
- **Active**: fog rolling, occasional CO2 spurts, low rumble loop.
- **Imminent** (fixed 30s): the attention signal — an accelerating full-range
  lighting heartbeat, fog at full, warning sound. Every eruption passes
  through it, including manual triggers: nobody can skip the warning. It
  cannot fizzle; imminent always erupts.
- **Eruption**: fixed scripted cue timeline — full sound, rapid LED pulsing,
  continuous fog, CO2 + LPG bursts — then mandatory cooldown back to dormant.

Transitions:

- Dwell times are **sampled at phase entry** from truncated increasing-hazard
  distributions (Weibull-family, shape k > 1, clipped to [t_min, t_max]).
  Parameters live in config so timing can be retuned night-to-night without a
  deploy. (Note: not a homogeneous Poisson process — that's memoryless; the
  requirement "more likely over time, bounded" implies rising hazard.)
- From active: escalate to eruption ~70%, **fizzle** back to dormant ~30%
  (tunable). Fizzles keep the tell unpredictable and spend no fuel.
- Eruption is *not* sampled — it is a deterministic cue list with exact
  timings, because that's where precision and safety bounds matter.
- **Manual trigger** from the web page requests an eruption; the request passes
  through the same safety gates (cooldown, armed state) as automatic ones.

## Software architecture

Python, managed with `uv`. Single process owns DMX, audio, state machine, and
web UI — sync is trivial and the flame channel's value is provable at all times.

```
src/dmx_volcano/
├── driver/
│   ├── enttec.py        # pyserial driver, Enttec Pro framing
│   ├── fake.py          # dev driver feeding the visualizer
│   └── universe.py      # 512-channel frame buffer (1-indexed)
├── fixtures.py          # channel maps + physical layout (from config)
├── safety.py            # arming, clamps, cooldowns, burst limits
├── phases.py            # dormant/active/eruption state machine
├── stats.py             # dwell-time distributions
├── cues.py              # scripted eruption timeline + CueRunner
├── events.py            # JSONL event log
├── audio.py             # sound pools (per-phase + per-transition) + aux-out playback
├── web/                 # FastAPI control page + 2D visualizer
└── show.py              # entry point: 40Hz frame loop
config/show.toml         # all tuning: dwell params, addresses, channel maps
tests/                   # safety invariants, state machine, wire format
```

- **Driver seam:** `send_frame()` on either `EnttecPro` or `FakeDriver`. Safety
  behavior is unit-testable by asserting on the exact bytes that would hit the
  wire.
- **Visualizer:** 2D top-down browser view; fixtures drawn at approximate
  physical positions from a layout config — PARs as colored glows, fogger/CO2/
  flame as icons that animate when their channels go hot. Consumes the same
  frame stream as the wire. Supports fast-forward to validate dwell
  distributions over hundreds of simulated cycles.
- **Web control page:** trigger eruption, hold dormant, pause; shows current
  phase, time-in-phase, eruption count, armed state. Includes a fixture-test
  console (dormant + hold only): light/fog toggles, and time-boxed CO2/flame
  test bursts through the same SafetyGate as cues — never free-running
  toggles. Assumptions made while building it: `docs/REVISIT.md`.
- **Audio:** layered per phase from role pools (`[audio.phases.<phase>]`):
  `start` one-shot on entry, a respawning `loop` bed, a `bg` texture layer,
  and `accent` one-shots at randomly sampled intervals; `[audio.transitions]`
  stingers layer on top when the show makes that exact move — the audible
  "it's advancing" cue. Every pool picks at random, no back-to-back repeats.
  Layers are concurrent player processes (dmix on the Pi:
  `deploy/asound.conf`); relative loudness is authored into the files — no
  runtime gain to tune blind at the venue. The speaker itself is dumb: aux
  line-level from the Pi, no DMX involved. Files are imported by dropping
  into `assets/` or uploading from the web page's Audio panel, which also
  previews any file out the real speaker (the venue line check); pool
  assignment stays a config edit + restart, keeping `show.toml` the single
  source of show content. Missing files warn at startup, not on show night.
- **Deployment:** systemd service, auto-start on boot into safe dormant,
  auto-restart on crash.

## Data logging

Append-only JSONL, one file per night, loads straight into pandas:

- Phase transitions: timestamp, from/to, sampled dwell vs. actual elapsed,
  trigger cause (timer, fizzle roll, manual).
- Eruptions: trigger source, cue timeline as executed, cooldown applied.
- Operator actions: arm/disarm, manual triggers, pauses.
- Health: frame rate, serial errors, restarts.

Manual triggers double as a crowd-demand signal: comparing when operators
wanted eruptions vs. when the sampler fired them drives night-to-night retuning
of the distribution parameters. Post-season analysis: empirical vs. theoretical
dwell distributions, eruptions/hour, manual-vs-auto ratio.

## CO2 tracking & alerts

Every granted CO2 burst consumes `duration x rate_kg_per_s` (rate must be
measured: weigh tank, timed burst, weigh again). Usage persists in
`var/co2_tank.json` — restarts never "refill" the tank; only the web UI's
tank-reset does. When remaining capacity crosses the warn threshold, one SMS
per tank goes to the hardcoded operator numbers in config (Twilio; creds via
env / systemd EnvironmentFile; unconfigured = log-only, so dev machines never
text anyone).

## Show content & deployment

All show content — sounds, per-phase lighting looks, eruption cue timings,
dwell parameters — lives in `config/show.toml`: see `docs/SHOW_AUTHORING.md`.
The Pi environment (Raspberry Pi OS Lite 64-bit + uv-pinned Python), its
Docker emulation for the Mac, systemd unit, and udev rule are specified in
`docs/DEPLOYMENT.md` + `deploy/`.

## Build order

Sim-first, hardware staged, flame last:

1. Frame loop + fake driver + visualizer; state machine and distributions
   validated in simulation.
2. Real adapter + PAR fixtures (verify addresses, channel maps, DMX-loss).
3. Fogger (channel map from printed manual).
4. CO2 cannon (limits enforced; bench-test DMX-loss).
5. Flame machine on arrival: supervised bench test (safety channel values,
   DMX-loss, tip-over), then integrate; full-show rehearsals with
   `pyro_enabled` off, then supervised full tests.

## Open items

- [ ] Pull exact DMX channel maps from the MK-F01 and MK-C09 printed manuals.
- [ ] H-E01A: confirm channel count (2 vs 6) and safety-channel values from its
      manual on arrival.
- [ ] Bench-test DMX-loss behavior on all three Moka units.
- [ ] Choose and order PAR fixtures and the Enttec adapter.
- [ ] Confirm Pi model on hand or order.

### Sources

- [Finale 3D — Moka H-E01](https://finale3d.com/documentation/mk-h-e01/)
- [MOKA H-E01A product page](https://www.mokalighting.com/products/moka-sfx-h-e01a-lgp-single-head-flame-mchine)
- [MOKA MK-C09 product page](https://www.mokalighting.com/products/moka-sfx-mk-c09-special-effects-dj-co2-jet)
- [MOKA MK-F01 product page](https://mokasfx.com/mk-f01-1500w-led-fog-machine-dmx512/)
