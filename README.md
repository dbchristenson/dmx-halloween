# dmx-halloween

Stochastic DMX show controller for a Halloween volcano — flame, CO2, fog, and
LED pars driven from a Raspberry Pi. See `DESIGN.md` for architecture and
safety invariants, `HARDWARE.md` for the procurement list.

## Quickstart

```sh
uv run pytest            # test suite (safety invariants live here)
uv run dmx-volcano       # run the show in sim mode (fake driver)
uv run dmx-volcano --real /dev/ttyUSB0   # real Enttec DMX USB Pro
```

Show tuning (dwell distributions, fixture addresses, channel maps) lives in
`config/show.toml` — edit and restart, no code changes. Nightly JSONL event
logs land in `logs/`.
