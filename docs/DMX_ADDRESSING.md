# DMX addressing: chain order does not matter (and how to keep it that way)

## The property you asked for is native to DMX

DMX512 is a **multi-drop bus**, not a pipeline. The Enttec transmits the same
512-channel frame to every fixture on the chain; each fixture ignores
everything except the slots starting at its own **start address**, which is
set on the fixture itself (menu display or DIP switches). Signal-wise the
daisy-chain is just shared wiring.

Consequences:

- **Physically reordering the chain changes nothing.** Enttec → CO2 → PAR
  and Enttec → PAR → CO2 behave identically. Swap freely.
- The only two rules the *wiring* has: no Y-splits (daisy-chain or use a
  splitter), and terminate the last fixture (120Ω) on long runs.
- The only thing that must agree is **the start address set on each
  fixture's own hardware** vs. `[fixtures.*]` in the config. Nothing in the
  software knows or assumes chain order — `fixtures.py` maps names to
  addresses from config only.

## Current address plan (from `config/show.toml`)

| Fixture | Start address | Channels used | Set on device to |
|---|---|---|---|
| Flame (H-E01A) | 1 | 1 safety, 2 fire | `001` |
| CO2 cannon (MK-C09) | 10 | 10 jet | `010` |
| Fogger (MK-F01) | 20 | 20–23 | `020` |
| par1…par8 | 30, 37, 44, 51, 58, 65, 72, 79 | 4 each (dimmer,R,G,B) | `030` … `079` |

Before any bench test, check each fixture's display/DIP setting against this
table. A fixture at the wrong address doesn't "half work" — it silently
listens to someone else's channels. If a device's address is awkward to
change physically, change the config instead: edit `address = …`, restart.
Addresses may overlap zero channels; gaps are fine and deliberate (room for
confirmed channel maps to grow).

## Detection: what is and isn't possible

- **The Enttec itself is truly detectable**: `tools/enttec_detect.py` sends
  Get-Params/Get-Serial requests and requires a framed reply, proving
  two-way comms with the widget (not just USB enumeration).
- **Fixtures are not electronically discoverable.** DMX is one-way; the
  fixtures never talk back. (The Enttec Pro can do RDM, but the MOKA units
  and budget PARs don't implement it.) "Detection" for a fixture means a
  human sees it respond: use the web test console's per-fixture light
  toggles and time-boxed CO2 bursts, one fixture at a time.
- **Never "find" a fixture by sweeping channels.** With a CO2 cannon (or
  later, the flame machine) on the chain at an unverified address, a blind
  sweep can hit its trigger channel. The SafetyGate only clamps the pyro
  channels it knows from config — it cannot protect a pyro device sitting at
  an address the config doesn't list. Identify pyro units by reading the
  address off the device, never by probing.

## Bench-test procedure

See `config/bench.toml` (short phases, 0.5s CO2 pops, nothing transitions on
its own). Run on the Pi:

```sh
uv run dmx-volcano --real /dev/enttec --config config/bench.toml
```

then from the control page (or `curl`): test mode → PAR toggles/colors →
one CO2 burst → exit test mode → release hold → trigger → watch the
5s imminent + 12s eruption fire the same 0.5s CO2 cue through the gate.
Every grant/denial lands in `logs/*.jsonl`.
