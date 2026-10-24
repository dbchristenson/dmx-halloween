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
| MOKA MK-F01 | 1500W fogger + 24×3W RGB LEDs | 7 channels per product page: fog, R, G, B, strobe, LED autorun, autorun speed — value ranges unpublished, **confirm on bench** | owned |
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
5. **CO2 operational limits encoded in software:** bursts ≤4s, ≥5s between bursts (MOKA's MK-C09 page: "two jet time intervals
   cannot be short by 5 seconds"), siphon/dip-tube tank required, 3m
   clearance in spray direction.
   **Flame limits:** bursts ≤10s, and at most 10s of fire in any rolling 13s
   window (sustained use settles at 10s on / 3s off). No fixed gap between
   bursts; an early release books only actual fire. Raised from 3s max + 3s
   gap after the 2026-10 supervised bench test. A flame *pattern* is one
   gate grant: safety held open across it, fire only inside each pulse,
   granted whole or not at all.
   **Unburnt gas:** a flame pulse that fails to ignite releases LPG, which
   is heavier than air and pools in the crater for the next pulse to light.
   So pulses shorter than `safety.MIN_PULSE` are refused (raise it to the
   bench-verified ignition floor), and flame is refused for 60s after any
   CO2 burst ends: CO2 is cold and ~1.5x heavier than air, pools in the
   crater after the visible plume clears, and can stop the flame igniting.
   No published dissipation figure exists; 60s is a deliberately long
   margin the show never comes near (dormant lasts ≥120s after an eruption).
   The crater is not airtight (1x4s across a frame under OSB, gaps along
   the sheet edges, back open while the inside is still being worked on),
   so gas spills out rather than sitting for long. The margins stay anyway:
   they cost the show nothing, and closing the back reduces that venting.
6. **DMX-loss behavior is bench-tested per fixture** (kill signal mid-effect,
   observe) before pyro ever runs on a show night. Not documented publicly for
   any of the three Moka units. Flame machine gets a supervised bench test on
   arrival, and its tip-over shutoff (45°) is verified.
7. **The imminent warning precedes every CO2 burst, not every flame.**
   Transitions fire flame with no warning. That is acceptable only because
   the flame head is raised ~10ft with a 3m exclusion zone on all sides
   that no visitor can enter in any phase — if the rig or the walkway
   changes, revisit this.

## Show design

Four-phase stochastic state machine. The effects have fixed roles (decided
2026-10-24): **CO2 is the showpiece and the scarce resource**, so it is
saved for the eruption alone. The fogger is visually the biggest effect
and runs in every phase. The flame cannot fire in the eruption (the CO2
smothers it), so it marks the transitions and drives imminent, as
pulsed "flame balls".

| Phase | Fogger | Flame | CO2 |
|---|---|---|---|
| Dormant | light duty cycle | — | — |
| Active | heavier duty cycle | — | — |
| Imminent | heavy duty cycle | patterns throughout | — |
| Eruption | full, constant | **never** | a pop, then the big burst |

- **Dormant** (dominant): soft amber/red PAR glow, the occasional fog wisp.
- **Active**: fog rolling, low rumble loop.
- **Imminent** (fixed 30s): the attention signal — an accelerating full-range
  lighting heartbeat, heavy fog, flame patterns, warning sound. Every
  eruption passes through it, including manual triggers: nobody can skip
  the warning. It cannot fizzle; imminent always erupts.
- **Eruption** (10s): fixed scripted cue timeline — full sound, strobing
  pars, white crater, constant fog, two CO2 bursts — then mandatory
  cooldown back to dormant.

Flame patterns are morse-like strings (`.` dot, `-` dash) picked at random
per move, no back-to-back repeats, more flame the nearer the eruption.
Each plays at the start of the new phase, with every light flaring orange
on each pulse — except the imminent → eruption finale, which plays at the
end of imminent and finishes 1.5s before the first CO2 burst. An operator
hold's active → dormant fires nothing; eruption → dormant never fires
(lingering CO2).

The fogger only fogs while its pump runs, and running the pump cools its
heater: run it too long and the MK-F01 stops on its own to reheat, which
DMX cannot report. So each phase's fog is a duty cycle (`fog_on`/`fog_off`)
sized to stay inside the heater's recovery, keeping it from stalling in the
eruption.

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
├── flame.py             # flame-ball patterns for transitions + imminent
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
measured: weigh tank, timed burst, weigh again — or, without a scale, sum
one tank's granted CO2 seconds on the rehearsal night). Budget: 5
eruptions/hour × 4h × 4 nights = 80 eruptions; at 2.5s of CO2 each that is
200s of spray. MOKA's FAQ puts a 40L (~23kg) cylinder at 40–50s of spray,
and we have four large tanks, so the rehearsal decides whether the
per-eruption CO2 or the pacing has to give. Usage persists in
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

- [x] MK-F01 channel order: 7ch from the MOKA product page.
- [x] MK-F01 on its own power (no longer fed through the flame/CO2
      power-through jacks) and address set to `d300` (2026-10-23).
- [x] MK-F01 answers DMX on its own power (2026-10-24: ran the show's
      dormant fog cycle after a Pi reboot).
- [ ] MK-F01: confirm strobe/autorun at 0 really mean off.
- [x] MK-F01 fog bench (2026-10-24): the fog level is proportional (64 vs
      128 clearly differ; 255 adds less, possibly a part-cooled block). At
      255: ~14s continuous, then ~45s to reheat. Phase duty cycles sized so
      the eruption enters with ~12s of full fog banked.
- [ ] H-E01A pulse floor (supervised, with LPG): the shortest pulse that
      ignites *every* time, single and back to back in a pattern. Set
      `safety.MIN_PULSE["flame"]` and `[flame] dot` to it.
- [ ] CO2 seconds per tank, from the rehearsal night; set `rate_kg_per_s`.
- [ ] Check all four CO2 tanks are siphon (dip-tube) tanks, stood upright.
- [ ] Pull the exact DMX channel map from the MK-C09 printed manual.
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
